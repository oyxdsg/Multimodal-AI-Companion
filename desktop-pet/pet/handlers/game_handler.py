# -*- coding: utf-8 -*-
"""Minecraft 游戏联动 Handler：日志/模组数据轮询、事件聚合、AI 互动调度。

从 pet/modes.py 的 PetModesMixin 游戏域迁移：
- 数据源轮询（模组窗口优先，日志回退）
- 事件聚合（普通活动按周期合并汇报）
- 建筑感知包检测与播报
- AI 互动（组装后交给 game.processor，结果经 app.game_ready 信号展示）

窗口级共享状态（_game_enabled / _game_type / _set_game_type）保留在窗口骨架。
"""
import os
import threading
import time

import config
import game.mod_data as deskpet
import game.mc_log as mc
import game.processor as game_processor
from pet.handlers.base import BaseHandler


def _log_worker_error(tag, exc):
    """把 worker 线程被吞掉的异常写入 logs/crash.log，便于定位「无播报」类问题。"""
    try:
        import traceback as _tb
        log = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "logs", "crash.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n===== WorkerError[{tag}] =====")
            _tb.print_exception(type(exc), exc, exc.__traceback__, file=f)
    except Exception:
        pass


class GameHandler(BaseHandler):
    """Minecraft 游戏联动（模组数据 + 日志回退 + 建筑感知）。"""

    def __init__(self, app):
        super().__init__(app)
        store = self.store
        self._bootstrapped = False   # 启动跳存量：只处理本次运行后新增的事件
        self._game_freq = int(store.value("game_freq", 20) or 20)
        self._game_log_path = str(store.value("game_log_path", "") or "")
        self._game_files_state = {}  # 日志文件 -> (字节偏移, 最后时间戳)
        self._game_last_key = ""        # 上次发送的事件文本，去重
        self._game_last_adv = None      # 上次见到的玩家进度数量（基线）
        # 桌宠模组数据（deskpet）
        self._game_source = int(store.value("game_source", 0) or 0)
        self._game_normal_summary_min = int(
            store.value("game_normal_summary", 20) or 20)
        self._game_chat_respond = str(
            store.value("game_chat_respond", True)) != "false"
        self._game_win_pos = {}      # 窗口文件 -> 字节偏移
        self._game_normal_accum = []  # 累积的普通活动窗口
        self._game_last_summary = 0.0
        self._game_last_ai = 0.0     # 上次 AI 调用时间（冷却）
        self._game_last_check = 0.0
        # 建筑识别感知包（mod 建筑识别系统，deskpet/buildings/latest.json）
        self._game_build_enabled = str(
            store.value("game_build", True)).lower() not in ("false", "0", "off")
        self._game_build_win = None    # 已处理的最大感知包窗口序号（去重）
        self._game_build_last_key = ""  # 建筑播报去重

    # ---------- 心跳轮询 ----------

    def _bootstrap_cursor(self):
        """启动一次性：跳过磁盘上已有的存量窗口/日志文件。

        桌宠启动时把已存在的模组 jsonl 与日志游标初始化到文件当前大小，
        只处理**本次运行后新增**的事件——避免一打开桌宠就把上次游戏的
        存量日志内容全部重读并发送给 AI。玩家进入游戏后产生的新数据照常增量读取。
        """
        self._bootstrapped = True
        desk_dir = deskpet.deskpet_dir_from_log(self._game_log_path)
        for fn in deskpet.window_files(desk_dir):
            path = os.path.join(desk_dir, fn)
            try:
                self._game_win_pos[path] = os.path.getsize(path)
            except OSError:
                pass
        base = self._game_log_path or mc.find_log_path()
        for p in mc.active_log_files(base):
            try:
                self._game_files_state[p] = (os.path.getsize(p), None)
            except OSError:
                pass

    def tick(self):
        """由窗口统一心跳按周期调用。"""
        if not self.app._game_enabled:
            return
        if not self._bootstrapped:
            self._bootstrap_cursor()
        if time.time() - self._game_last_check < self._game_freq:
            return
        self._game_last_check = time.time()
        # 建筑感知包（独立于事件窗口，20s 窗口 / 休眠时由 mod 写入）
        self._poll_building_package()
        source = self._game_source
        # 数据源：0 自动（模组优先） 1 仅模组 2 仅日志
        if source in (0, 1):
            desk_dir = deskpet.deskpet_dir_from_log(self._game_log_path)
            windows = self._read_deskpet_windows(desk_dir)
            if windows:
                self._handle_game_windows(windows)
                return
            if source == 1:      # 仅模组但没有数据
                return
            # 自动模式：模组目录存在（模组在跑）时宁可等下一轮窗口，
            # 也不回落 latest.log（其含 intermediary 混淆类名与乱码）。
            if deskpet.window_files(desk_dir):
                return
        if source in (0, 2):
            self._poll_game_log()

    # ---------- 桌宠模组窗口事件 ----------

    def _read_deskpet_windows(self, desk_dir):
        windows = []
        if not desk_dir or not os.path.isdir(desk_dir):
            return windows
        for fn in deskpet.window_files(desk_dir):
            # maid/ 子目录是智能女仆（smartmaid）的感知事件写入区，走 WS 通道
            # （maid_handler）单独处理；这里跳过，避免女仆过程事件混进
            # 游戏事件汇总喂给 AI 播报（"女仆发现敌人 X 格外"这类过程刷屏）。
            if fn.startswith("maid/"):
                continue
            path = os.path.join(desk_dir, fn)
            pos = self._game_win_pos.get(path, 0)
            new_wins, new_pos = deskpet.read_window(path, pos)
            self._game_win_pos[path] = new_pos
            if new_wins:
                windows.extend(new_wins)
        return windows

    def _handle_game_windows(self, windows):
        # 女仆对话期间（120s 内玩家正跟女仆聊）：游戏事件线整体静默——
        # 不调 AI、不播报，避免"两条线抢话"（女仆对话时游戏事件 AI 插嘴很突兀）。
        try:
            if self.app.maid_link_connected \
                    and self.app._maid_handler.recently_in_chat():
                self._game_normal_accum = []
                self._game_last_summary = time.time()
                return
        except Exception:
            pass
        # 所有事件（关键 CRITICAL + 普通 NORMAL）统一累积，
        # 每 _game_normal_summary_min（默认 20s）合并汇报给 AI 一次。
        for win in windows:
            imp = deskpet.window_importance(win)
            if imp in ("CRITICAL", "NORMAL"):
                self._game_normal_accum.append(win)
        if time.time() - self._game_last_summary < self._game_normal_summary_min:
            return
        self._game_last_summary = time.time()
        merged = self._merge_windows(self._game_normal_accum)
        self._game_normal_accum = []
        if not merged:
            return
        lines = [l for l in deskpet.highlights_to_text(merged) if l]
        if not lines:
            return
        # 聊天回应开关：未开全回时只保留问句/含关键词的聊天
        if not self._game_chat_respond:
            lines = [l for l in lines if self._is_chat_worth(l)]
        if not lines:
            return
        # 通道去重：与 WS 通道（maid_handler）共享指纹，避免 smartmaid
        # 同时走文件+WS 时同一事件播报两次；任务启停类不参与去重。
        from core.event_dedup import shared as dedup, is_task_boundary
        lines = [l for l in lines
                 if is_task_boundary(l) or dedup.check(l)]
        if not lines:
            return
        key = "\n".join(lines)
        if key == self._game_last_key:
            return
        self._game_last_key = key
        # AI 调用冷却，避免频率限制
        if time.time() - self._game_last_ai < config.GAME_AI_COOLDOWN:
            return
        self._game_last_ai = time.time()
        # 搜索词：优先取模组数据里的目标实体（物品/生物/伤害来源/进度名），
        # 再补充日志正则提取与本地库反向匹配，确保「获得那么多东西」都能被检索
        terms = self._extract_win_targets(merged) + mc.extract_terms(lines)
        player = self._extract_player(merged)
        threading.Thread(target=self._game_worker,
                         args=(lines, terms, player), daemon=True).start()

    @staticmethod
    def _extract_win_targets(win):
        """从窗口事件 highlights 提取目标实体名作为 wiki 搜索词。
        覆盖：获得物品 / 击杀 / 受伤来源 / 破坏·放置·使用 / 进度名。"""
        out = []
        for h in (win or {}).get("highlights") or []:
            if not isinstance(h, dict):
                continue
            t = (h.get("type") or "").lower()
            target = (h.get("target") or "").strip()
            detail = (h.get("detail") or "").strip()
            if t in ("item_gain", "kill", "damage", "break",
                     "place", "use_item") and target:
                out.append(target)
            elif t == "advancement" and detail:
                out.append(detail)
        # 去重且按长度降序（具体词条优先）
        seen, ordered = set(), []
        for s in sorted(out, key=len, reverse=True):
            s = s.strip()
            if s and s not in seen:
                seen.add(s)
                ordered.append(s)
        return ordered[:12]

    @staticmethod
    def _extract_player(win):
        """从窗口事件的 highlights 中提取玩家（主人）名。"""
        for h in (win or {}).get("highlights") or []:
            if isinstance(h, dict) and h.get("player"):
                return h["player"]
        return ""

    def _merge_windows(self, windows):
        hl = []
        for win in windows:
            hl.extend(win.get("highlights") or [])
        return {"importance": "CRITICAL", "highlights": hl} if hl else None

    @staticmethod
    def _is_chat_worth(line):
        return ("？" in line or "?" in line
                or any(k in line for k in ("你", "吗", "呢", "吧", "来", "看")))

    # ---------- 游戏日志事件（无模组时回退） ----------

    def _poll_game_log(self):
        base = self._game_log_path or mc.find_log_path()
        if not base:
            return
        # 通过时间筛选出当前活动日志（多文件增量监听）
        files = mc.active_log_files(base)
        if not files:
            return
        events = []
        for p in files:
            pos, last_ts = self._game_files_state.get(p, (0, None))
            ev, new_pos, new_last = mc.read_events_increment(p, pos, last_ts)
            self._game_files_state[p] = (new_pos, new_last)
            if ev:
                events.extend(ev)
        # 去重保序
        seen, uniq = set(), []
        for e in events:
            if e not in seen:
                seen.add(e)
                uniq.append(e)
        if not uniq:
            return
        # 进度事件按数量变化过滤（启动全量忽略、首次设基线、增长才触发）
        uniq, self._game_last_adv = mc.filter_advancements(
            uniq, self._game_last_adv)
        if not uniq:
            return
        key = "\n".join(uniq)
        if key == self._game_last_key:
            return
        self._game_last_key = key
        if time.time() - self._game_last_ai < config.GAME_AI_COOLDOWN:
            return
        self._game_last_ai = time.time()
        terms = mc.extract_terms(uniq)
        player = mc.extract_player(uniq)
        threading.Thread(target=self._game_worker,
                         args=(uniq, terms, player), daemon=True).start()

    # ---------- 建筑识别感知包（mod 建筑识别系统） ----------

    def _poll_building_package(self):
        """检测建筑感知包新窗口（window 序号变化）→ 触发建筑播报。"""
        if not self._game_build_enabled:
            return
        desk_dir = deskpet.deskpet_dir_from_log(self._game_log_path)
        pkg = deskpet.read_building_package(desk_dir)
        if not pkg:
            return
        win = pkg.get("window")
        if win is None or win == self._game_build_win:
            return
        self._game_build_win = win
        if pkg.get("state") == "in_progress":
            # 进行中建筑：直接口语提示播报，不调 AI → 不占 AI 冷却
            # （避免盖房子时频繁刷冷却把游戏事件播报一直压住）
            threading.Thread(target=self._building_worker,
                             args=(pkg,), daemon=True).start()
            return
        # 完整建筑需要 AI 润色 → 走 AI 冷却（避免与游戏事件互抢限流）
        if time.time() - self._game_last_ai < config.GAME_AI_COOLDOWN:
            return
        self._game_last_ai = time.time()
        threading.Thread(target=self._building_worker,
                         args=(pkg,), daemon=True).start()

    def _building_worker(self, pkg):
        try:
            # 完整建筑走 AI 润色 → 过全局软限流（与女仆决策共用计数），
            # 超限降级为模板描述（不调 AI）。
            if pkg.get("state") != "in_progress":
                from core.ai_throttle import shared as throttle
                if not throttle.allow():
                    cls = (pkg.get("classification") or "").strip()
                    if cls:
                        self.app.game_ready.emit(f"主人盖了{cls}！")
                    return
            text = game_processor.shared.building_text(pkg)
            text = (text or "").strip()
            if not text:
                return
            # 去重 key：进行中建筑用「意图+阶段」（同一阶段不重复，阶段推进才播报一次）；
            # 完整建筑用文本本身。
            if pkg.get("state") == "in_progress":
                intent = pkg.get("intent") or {}
                key = "{}/{}".format(intent.get("category"), intent.get("phase"))
            else:
                key = text
            if key != self._game_build_last_key:
                self._game_build_last_key = key
                self.app.game_ready.emit(text)
        except Exception as e:
            _log_worker_error("building", e)

    def _game_worker(self, events, terms, player=""):
        # 互动由 AI 生成（processor 内部处理未登录/去重/wiki/人格关系/环境注入）
        try:
            result = game_processor.shared.process_game(events, terms, player)
            if result:
                self.app.game_ready.emit(result["text"])
        except Exception as e:
            _log_worker_error("game", e)
