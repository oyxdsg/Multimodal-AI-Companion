# -*- coding: utf-8 -*-
"""游戏/修仙/建筑事件 → AI 互动的纯业务处理器（不依赖 pet UI 层）。

从 pet/modes.py 的 _game_worker / _xiuxian_worker / _building_worker 提取：
- 组装 prompt：wiki 知识查询与提炼、实体/会话去重、人格关系注入、环境上下文注入
- 只调用 ai.chat_service（共享 AI 会话）与 ai.client / game.wiki_kb，
  不接触 pet.bubble / 消息队列 / 信号
- 返回结构化结果 {"text": ..., "kind": ...} 或 None（无需播报时），由调用方（handler）展示

状态语义（与重构前一致）：
- wiki 实体去重绑定 AI 会话 id（换会话重置）
- 游戏关系描述只在首次 / 换人格后附带一次
- 环境上下文只在变化时注入
- 回复去重：相同回复不重复播报
- 模式切换声明只在联动类型 / 人格 / 会话变化时发送一次
"""
import json
import os

from PySide6.QtCore import QSettings

import config
import ai.client as ai_client
from ai.chat_service import shared as chat_service
import game.mod_data as deskpet
import game.xiuxian as xiuxian


class GameProcessor:
    """游戏联动事件的处理与 prompt 组装。线程安全由调用方保证
    （AI 调用本身经 chat_service 全局串行）。"""

    def __init__(self):
        self._store = QSettings("DesktopPet", "PetWindow")
        # 已发送游戏关系描述的人格（只发一次，换人格后重发）
        self._role_persona = None
        # 环境上下文注入去重（只在变化时注入）
        self._env_last = None
        # 模式切换声明去重（联动类型/人格/会话变化时发一次）
        self._switch_key = None
        # 回复去重
        self._game_last_msg = ""
        self._xiuxian_last_msg = ""

    # ---------- Minecraft 事件 ----------

    def process_game(self, events, terms, player=""):
        """Minecraft 事件 → AI 回复文本。未登录 / 无内容 / 重复时返回 None。"""
        if not self._logged_in():
            return None
        log_text = "\n".join(events[-config.GAME_LOG_MAX_LINES:])
        # Wiki 知识整块可插拔：交给 wiki-knowledge 扩展点（无插件则空 → 大模型自答）
        wiki = self._wiki_lookup(log_text, terms)
        # 玩家名：自动提取优先，其次设置里手动配置，最后回退当前人格的称呼
        persona = str(self._store.value("persona", "") or "") or None
        info = config.persona_info(persona)
        # 关系描述只在首次（或人格切换后）附带一次，避免反复发送
        role = ""
        if persona != self._role_persona:
            self._role_persona = persona
            player = (player or str(self._store.value("game_player_name", "") or "")
                      or info["game_owner"])
            role = (info["game_role"].format(player=player)
                    + "\n请以游戏伙伴身份互动。\n")
        prompt = (self._switch_decl(3)
                  + config.GAME_PROMPT.format(
                      log=log_text, wiki=wiki or "无", role=role))
        # 环境上下文：服务器/游戏/世界类型，只有变化时才注入，避免每轮重复
        env = self._read_game_env()
        if env and env != self._env_last:
            self._env_last = env
            prompt = env + "\n" + prompt
        content = chat_service.chat(prompt, "游戏")
        content = (content or "").strip()
        if content and content != self._game_last_msg:
            self._game_last_msg = content
            return {"text": content, "kind": "game"}
        return None

    # ---------- 修仙事件 ----------

    def process_xiuxian(self, events, state_summary):
        """修仙事件 → AI 回复文本。未登录 / 无内容 / 重复时返回 None。"""
        if not self._logged_in():
            return None
        lines = [f"[{e.get('title', '')}] {e.get('detail', '')}"
                 for e in events[-8:] if e.get("detail")]
        if not lines:
            return None
        log_text = "\n".join(lines)
        state_desc = self._format_xiuxian_state(state_summary)
        prompt = (self._switch_decl(3)
                  + config.XIUXIAN_PROMPT.format(
                      log=log_text, state=state_desc))
        content = chat_service.chat(prompt, "修仙")
        content = (content or "").strip()
        if content and content != self._xiuxian_last_msg:
            self._xiuxian_last_msg = content
            return {"text": content, "kind": "xiuxian"}
        return None

    # ---------- 建筑识别感知包 ----------

    def building_text(self, pkg):
        """生成建筑播报文本。

        进行中建筑（state=in_progress）：mod 已把 classification 设为意图口语提示
        （intent.hint），直接返回、不占 AI 资源；完整建筑仍走 AI 润色 / 模板回退。
        """
        cls = (pkg.get("classification") or "").strip()
        if not cls:
            return ""
        if pkg.get("state") == "in_progress":
            hint = (pkg.get("intent") or {}).get("hint") or cls
            return hint
        if not self._logged_in():
            return f"主人盖了{cls}！"
        return chat_service.chat(self._building_prompt(pkg), "建筑")

    def _building_prompt(self, pkg):
        """组装建筑播报的 AI 提示。

        评分隐式：不告诉用户具体分/等级，只给程序的总体评价 + 针对性建议；
        AI 只负责按人格组织语言说出（不吐露分数）。
        """
        p = "主人可能在搭建" + (pkg.get("classification") or "未知") + "。"
        sc = pkg.get("score") or {}
        comment = (sc.get("comment") or "").strip()
        suggestions = sc.get("suggestions") or []
        if comment:
            p += "\n(评价：" + comment + ")"
        if suggestions:
            p += "\n(建议：" + "；".join(suggestions) + ")"
        return p

    # ---------- 内部工具 ----------

    def _wiki_lookup(self, log_text, hints):
        """经 wiki-knowledge 扩展点取知识；无实现 / 异常 → 空串（不注入）。

        Wiki 整块可插拔：不装插件时由大模型凭自身知识作答
        （见 DESIGN_OPTIONAL.md §4.2）。
        """
        try:
            from plugin import host as plugin_host
            impl = plugin_host.registry().wiki_knowledge()
            if impl is None:
                return ""
            sid, _pid = ai_client.load_thread_state()
            out = impl.know(log_text, session_key=sid,
                            max_chars=config.GAME_WIKI_MAX_CHARS,
                            hints=list(hints or []))
            return out or ""
        except Exception:
            return ""

    def _logged_in(self):
        return bool(str(self._store.value("ai_token", "") or ""))

    def _switch_decl(self, mode_id=3):
        """游戏模式切换声明：仅在游戏联动类型 / 人格 / AI 会话变化时发送一次。
        同局同会话内不重复发送，避免每次互动都带「切换到 3、游戏模式」。

        **仅网页版需要**：SessionBackend 的 system 只在会话首条注入，模式只能靠 user
        消息提醒。官方 API / 千问无状态、system 每轮重发且当前模式已写进 system，
        这里返回空串（避免污染 history 被反复重放）。
        """
        if config.is_messages_backend():
            return ""
        persona = str(self._store.value("persona", "") or "") or ""
        sid, _pid = ai_client.load_thread_state()
        gtype = int(self._store.value("game_type", 0) or 0)
        key = (gtype, persona, sid)
        if self._switch_key != key:
            self._switch_key = key
            name = next((m["name"] for m in config.AI_MODES
                         if m["id"] == mode_id), "游戏模式")
            return f"现在切换到 {mode_id}、{name}。\n"
        return ""

    def _read_game_env(self):
        """读取 mod 写的环境状态文件（deskpet/state.json），返回环境描述行。
        含当前服务器 / 小游戏模式（带玩法介绍）/ 世界类型（超平坦等）。"""
        try:
            log_path = str(self._store.value("game_log_path", "") or "")
            desk_dir = deskpet.deskpet_dir_from_log(log_path)
            state = os.path.join(desk_dir, "state.json")
            if not os.path.isfile(state):
                return ""
            with open(state, "r", encoding="utf-8") as f:
                d = json.load(f)
            parts = []
            if d.get("server"):
                parts.append(f"服务器={d['server']}")
            game = (d.get("game") or "").strip().upper()
            if game:
                desc = config.HYPIXEL_GAMES.get(game, "")
                if desc:
                    parts.append(f"游戏={game}（{desc}）")
                else:
                    parts.append(f"游戏={game}")
            if d.get("mode"):
                parts.append(f"模式={d['mode']}")
            if d.get("world_type"):
                parts.append(f"世界={d['world_type']}")
            return "当前环境：" + "，".join(parts) + "。" if parts else ""
        except Exception:
            return ""

    @staticmethod
    def _format_xiuxian_state(s):
        """把修仙存档摘要转成中文状态行。"""
        if not s:
            return "（无）"
        stage = xiuxian._stage_name(s.get("currentStage"))
        parts = [
            f"境界={stage}期",
            f"年龄={s.get('age', '?')}岁",
            f"寿元上限={s.get('maxAge', '?')}岁",
            f"难度={s.get('difficulty', '?')}",
            f"世界={s.get('world', '?')}",
        ]
        for k, label in (("cultivation", "修为"), ("health", "气血"),
                         ("daoHeart", "道心"), ("karma", "业力"),
                         ("spiritStones", "灵石"), ("reputation", "声望")):
            v = s.get(k)
            if isinstance(v, (int, float)):
                parts.append(f"{label}={v}")
        return "，".join(parts)


# 全局唯一实例（进程内单例，各 handler 共用同一套去重状态）
shared = GameProcessor()
