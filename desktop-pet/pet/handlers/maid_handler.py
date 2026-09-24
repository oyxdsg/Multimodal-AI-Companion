# -*- coding: utf-8 -*-
"""智能女仆联动 Handler（M5-b）。

职责：
- 按「游戏联动类型 = Minecraft」启停 :class:`game.maid_link.MaidLink`（WebSocket Server）
- 主线程定时 :meth:`tick` 取出模组上报的女仆事件，转成中文经 ``game_ready`` 播报
- 对外提供 :meth:`send_command` / :meth:`speak` / :meth:`animate`，供对话层驱动女仆

线程约定：MaidLink 跑在独立线程，本 Handler 只在 Qt 主线程访问它（poll / 发送）。
"""

import json
import os
import threading
import time

import config
from game import maid_link
from game import maid_intent
from game.maid_context import evaluate_danger
from pet.handlers.base import BaseHandler

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 开发调试用：把指令写进这个文件，桌宠会在下一拍下发给女仆（发送成功才删除该文件）
DEBUG_CMD_FILE = os.path.join(_BASE, ".maid_command.json")
# 全链路测试用：写任意 JSON（如 {}）→ 桌宠把当前合并后的感知全量转储到
# logs/maid_snapshot.json（供测试做「指令前/后」状态 diff，验证指令是否真的生效）
STATE_DUMP_FILE = os.path.join(_BASE, ".maid_state.json")
SNAPSHOT_OUT = os.path.join(_BASE, "logs", "maid_snapshot.json")


class MaidHandler(BaseHandler):

    def __init__(self, app):
        super().__init__(app)
        self._link = None
        self._last_event_at = 0.0
        self._reported_missing_dep = False
        self._logged_connected = False
        self._last_stats_at = time.time()
        self._maid_loop = None
        self._last_decision_at = 0.0
        self._last_danger = (False, False)
        self._decision_cooldown = float(
            getattr(config, "MAID_DECISION_COOLDOWN", 30))
        # 是否因"女仆在场"而隐藏了桌宠窗口（离线/断线时据此恢复）
        self._hidden_by_maid = False
        # 最近一次女仆对话时间（秒时间戳）：游戏事件线据此判断玩家是否正主动跟女仆聊，
        # 避免事件 AI 抢话
        self._last_chat_at = 0.0

    # ---------- 生命周期 ----------

    def _cfg(self, key, default):
        try:
            return self.store.value(key, default)
        except Exception:
            return default

    def _want_enabled(self):
        """仅在「游戏联动类型 = Minecraft」且配置开启时启用。"""
        return self.game_type == 1 and bool(getattr(config, "MAID_LINK_ENABLED", True))

    def _ensure_link(self):
        if self._link is not None and self._link.running:
            return True
        port = int(self._cfg("maid_link_port", getattr(config, "MAID_LINK_PORT", 21420)) or 21420)
        token = str(self._cfg("maid_link_token", getattr(config, "MAID_LINK_TOKEN", "")) or "")
        if self._link is None or self._link.port != port:
            if self._link is not None:
                self._link.stop()
            self._link = maid_link.MaidLink(port=port, token=token, log=self._log)
        if not self._link.available:
            if not self._reported_missing_dep:
                self._reported_missing_dep = True
                self.show_bubble("女仆联动需要 websockets 库：pip install websockets",
                                 error=True)
            return False
        return self._link.start()

    def stop(self):
        if self._link is not None:
            self._link.stop()

    def _log(self, text):
        """MaidLink 线程回调：打印 + 落日志文件（便于事后排查），不触碰 UI。"""
        line = "[maid_link] %s" % text
        try:
            print(line)
        except Exception:
            pass
        try:
            path = os.path.join(_BASE, "logs", "maid_link.log")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                f.write("[%s] %s\n" % (stamp, text))
        except Exception:
            pass

    # ---------- 心跳（Qt 主线程） ----------

    def tick(self):
        if not self._want_enabled():
            if self._link is not None and self._link.running:
                self._link.stop()
                self._logged_connected = False
                self._log("游戏联动已关闭，女仆联动停止")
            return
        if not self._ensure_link():
            return
        if self._link.connected and not self._logged_connected:
            self._logged_connected = True
            self._log("女仆已连接")
        # 连接断开（游戏退出/崩溃）时若曾因女仆隐藏窗口，恢复显示
        if not self._link.connected and self._hidden_by_maid:
            self._restore_window()
        self._poll_messages()
        self._poll_debug_command()
        self._poll_state_dump()
        self._ai_decision_tick()
        self._report_stats()

    def _ensure_maid_loop(self):
        """懒创建 MaidLoop（DSL 闭环 + 状态注入 + 决策）。"""
        if self._maid_loop is None:
            from game.maid_loop import MaidLoop
            self._maid_loop = MaidLoop(self._link)
        return self._maid_loop

    def _ai_decision_tick(self):
        """AI 主动决策（决策 3 边界）：低血 <30% / 被围 3+ 敌对 10 格内。

        命中且超过冷却 → 后台线程调一次 AI 决策（ephemeral，不进主对话历史），
        决策回复可夹带【DSL】下发给女仆（允许抢占），再以气泡播报。
        AI 调用在后台线程（阻塞网络），不冻结 Qt 主线程。
        """
        link = self._link
        if not (link and link.connected):
            return
        if not self.app._game_enabled and self.game_type != 1:
            return
        snap = link.state()
        try:
            low, surr = evaluate_danger(snap)
        except Exception:
            return
        if not (low or surr):
            return
        now = time.time()
        if now - self._last_decision_at < self._decision_cooldown:
            return
        self._last_decision_at = now
        # 后台线程：AI 调用 + DSL 下发都阻塞，不能占 Qt 主线程
        threading.Thread(target=self._run_decision, args=(snap, low, surr),
                         daemon=True).start()

    def _run_decision(self, snap, low, surr):
        try:
            loop = self._ensure_maid_loop()
            from game import maid_context as mctx
            transient = mctx.build_transient(
                snap, events=loop.events(), replies=loop.drain_replies())
            danger = ("低血" if low else "") + ("被围" if surr else "")
            self._log("AI 决策触发：%s" % danger)
            # 全局软限流：游戏/女仆闭环链路共用计数，超限降级模板播报（不调 AI）
            from core.ai_throttle import shared as throttle
            if not throttle.allow():
                self._log("AI 调用限流，降级模板播报")
                self.app.game_ready.emit("我%s，会小心的！" % danger)
                return
            prompt = ("（你是玩家在游戏里召唤的女仆，现在正用第一人称「我」说话。）"
                      "你%s！用一句话决定怎么自救，末尾可附一条指令"
                      "（允许抢占你当前的任务）。指令示例："
                      "【attack(range=10)】或【guard(range=10)】或【stop】"
                      "或【move(pos=[100,64,-50])】。" % danger)
            from ai.chat_service import shared as chat_service
            reply = chat_service.chat(prompt, source="女仆决策",
                                      transient=transient, ephemeral=True,
                                      thinking=False)
            reply = (reply or "").strip()
            if not reply:
                return
            self._log("AI 决策 -> %s" % reply)
            cmd, clean = maid_intent.parse(reply)
            if cmd:
                self._log("决策下发指令: %s" % cmd["cmd"])
                loop._link.request(cmd["cmd"], cmd["params"],
                                   cancel_previous=True)
            if clean:
                self.app.game_ready.emit(clean)
        except Exception as exc:
            self._log("AI 决策失败: %s" % exc)

    def _report_stats(self):
        """每 60s 落一次统计日志：感知上报量 + 增量收益 + 女仆状态（便于事后排查）。"""
        now = time.time()
        if now - self._last_stats_at < 60:
            return
        self._last_stats_at = now
        if not (self._link and self._link.connected):
            return
        stats = self._link.stats_line()
        if stats:
            self._log("上报统计 -> %s" % stats)
        state = self._link.status_line()
        if state:
            self._log("状态 -> %s" % state)

    def _poll_debug_command(self):
        """开发调试：把 ``.maid_command.json`` 里的指令下发给女仆。

        没连上时不删文件，等链路建立后下一拍重试，便于"先摆好文件再开游戏"。
        无该文件时只是一次 os.path.isfile，开销可忽略。
        """
        if not os.path.isfile(DEBUG_CMD_FILE):
            return
        try:
            with open(DEBUG_CMD_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as exc:
            self._log("调试指令文件解析失败（保留原文件）: %s" % exc)
            return
        items = payload if isinstance(payload, list) else [payload]
        sent_all = True
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "command").lower()
            maid = item.get("maid", "")
            if kind == "command":
                ok = self.send_command(item.get("cmd"), item.get("params"), maid=maid)
            elif kind == "speak":
                ok = self.speak(item.get("text", ""), maid=maid)
            elif kind == "animation":
                ok = self.animate(name=item.get("name", ""), anim_id=item.get("id"), maid=maid)
            else:
                self._log("调试指令类型未知: %s" % kind)
                ok = False
            sent_all = sent_all and bool(ok)
        if sent_all:
            try:
                os.remove(DEBUG_CMD_FILE)
                self._log("调试指令已下发并清理 %s" % os.path.basename(DEBUG_CMD_FILE))
            except OSError:
                pass
        else:
            self._log("调试指令未发送（女仆未连接），保留文件等下一拍重试")

    def _poll_state_dump(self):
        """全链路测试：写 ``.maid_state.json``（内容任意）→ 把当前合并后的感知全量
        转储到 ``logs/maid_snapshot.json``（含 ts / connected / maids / snapshot）。

        供测试做「指令前 / 指令后」状态 diff，验证指令是否真的落到游戏世界——
        而不是只看 AI 怎么说、回执怎么报。
        """
        if not os.path.isfile(STATE_DUMP_FILE):
            return
        try:
            with open(STATE_DUMP_FILE, "r", encoding="utf-8") as f:
                json.load(f)          # 内容仅占位，无实际语义
        except Exception:
            pass
        try:
            os.remove(STATE_DUMP_FILE)
        except OSError:
            return
        link = self._link
        payload = {
            "ts": time.time(),
            "connected": bool(link and link.connected),
            "maids": link.maids() if link else [],
            "snapshot": link.snapshot() if (link and link.connected) else {},
        }
        try:
            os.makedirs(os.path.dirname(SNAPSHOT_OUT), exist_ok=True)
            with open(SNAPSHOT_OUT, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
        except Exception as e:
            self._log("感知转储失败: %s" % e)

    def _poll_messages(self):
        link = self._link
        for msg in link.poll():
            mtype = msg.get("type")
            if mtype == "event":
                self._report_event(msg.get("event") or {})
            elif mtype == "command_result":
                self._report_reply(msg.get("reply") or {})
            elif mtype == "hello":
                maids = msg.get("maids") or []
                names = "、".join([m.get("name") or "女仆" for m in maids]) or "（暂无）"
                self.show_bubble("智能女仆已连接：%s" % names, speak=True)
                # 重连时女仆已在线：按设置隐退窗口
                if maids:
                    self._on_presence(True)
            elif mtype == "chat":
                text = str(msg.get("text") or "").strip()
                if text:
                    # AI 调用会阻塞网络，放后台线程，不冻结 Qt 主线程
                    threading.Thread(target=self._maid_chat_turn,
                                     args=(text, msg.get("maid", "")),
                                     daemon=True).start()
            elif mtype == "maid_presence":
                self._on_presence(bool(msg.get("online")), msg.get("maid", ""))
        # 事件已由 poll 投递过一份，这里清空队列避免堆积
        link.drain_events()
        link.drain_replies()

    def _report_event(self, event):
        text = maid_link.event_to_text(event)
        if not text:
            return
        # 感知事件播报开关：默认关（只播 AI 聊天）。
        # 关闭时感知事件（发现敌人/被打/任务启停）仅记日志、不进气泡/语音，
        # 避免"女仆老在报过程"刷屏盖过真正的 AI 聊天。
        if not bool(getattr(config, "MAID_EVENT_BUBBLE", False)):
            self._log("感知事件（已静默，仅日志）：%s" % text)
            return
        # 通道去重：与文件通道（game_handler）共享指纹，避免同一事件播报两次；
        # 任务启停类（task_started/task_done）不参与去重（每次都要播报）。
        from core.event_dedup import shared as dedup, is_task_boundary
        if not is_task_boundary(text) and not dedup.check(text):
            self._log("事件已播报过，去重跳过：%s" % text)
            return
        cooldown = float(getattr(config, "MAID_EVENT_COOLDOWN", 8))
        now = time.time()
        if now - self._last_event_at < cooldown:
            return
        self._last_event_at = now
        self._log("播报 -> %s" % text)
        self.app.game_ready.emit(text)

    def _report_reply(self, reply):
        if not reply:
            return
        self._log("指令回执: ok=%s state=%s id=%s"
                  % (reply.get("ok"), reply.get("state"), reply.get("id")))
        if not reply.get("ok"):
            err = (reply.get("result") or {}).get("error") or "未知原因"
            self.show_bubble("女仆没能执行：%s" % err, error=True)

    # ---------- 对外：驱动女仆 ----------

    @property
    def connected(self):
        return bool(self._link and self._link.connected)

    def recently_in_chat(self, window=120.0):
        """玩家最近 window 秒内是否跟女仆说过话（游戏事件线抢话抑制用）。
        默认 120s：对话后两分钟内，游戏事件 AI 不插到女仆嘴边。"""
        return (time.time() - self._last_chat_at) < window

    def maid_state_line(self, maid=""):
        """女仆当前状态的一行中文摘要（未连接时为空串）。"""
        return self._link.status_line(maid) if self._link else ""

    def send_command(self, cmd, params=None, maid="", cancel_previous=False):
        """下发指令给女仆（指令集与模组 /maidtasks 一致）。

        cancel_previous 默认 False（排队，不抢占女仆当前任务）。"""
        if not (self._link and self._link.connected):
            self._log("女仆未连接，指令未发送: %s" % cmd)
            return False
        return self._link.send_command(cmd, params, maid=maid,
                                       cancel_previous=cancel_previous)

    def speak(self, text, maid=""):
        """让女仆头顶冒气泡。"""
        if not (self._link and self._link.connected):
            return False
        return self._link.send_speak(text, maid=maid)

    def animate(self, name="", anim_id=None, maid=""):
        """让女仆播放动作（waving / clap / backflip ...）。"""
        if not (self._link and self._link.connected):
            return False
        return self._link.send_animation(name=name, anim_id=anim_id, maid=maid)

    # ---------- 玩家 ↔ 女仆 对话（聊天栏 / 语音） ----------

    def chat_turn(self, text, maid="", speak=True):
        """玩家说的话（游戏聊天栏或语音识别）→ AI → 回给女仆（气泡 + 游戏聊天栏）。

        公开方法，供 STT Handler 在女仆在线时调用（调用方需在后台线程，会阻塞 AI）。
        :param speak: 是否让桌宠朗读回复（默认 True，窗口隐藏也能出声）
        """
        self._maid_chat_turn(text, maid, speak)

    def _maid_chat_turn(self, text, maid="", speak=True):
        link = self._link
        text = str(text or "").strip()
        if not text or not (link and link.connected):
            return
        self._last_chat_at = time.time()
        self._log("女仆对话 <- %s" % text)
        try:
            from ai.chat_service import shared as chat_service
            # 本地预处理（NLU）：语音/文字里的物品错字先纠正，能自动执行的顺手执行
            extra = ""
            try:
                r = self.app.nlu_preprocess(text)
                extra = r[0] if isinstance(r, tuple) else (r or "")
            except Exception:
                extra = ""
            status = ""
            try:
                status = link.status_line(maid)
            except Exception:
                status = ""
            head = ("（**你就是玩家在游戏里召唤的那个女仆**——玩家现在直接对着你说话，"
                    "用第一人称“我”回复，不要把“女仆”当别人、不要第三人称称呼自己；"
                    "请用简体中文一句话回复；"
                    "你会自主战斗：会自动攻击靠近的敌对生物，你【不需要】"
                    "为打怪发 attack/guard 指令，只有玩家明确要求指定目标时"
                    "（如“打那头猪”）才用 【attack(target=minecraft:pig)】；"
                    "需要你动手时，在回复末尾附一条指令，格式如："
                    "【stop】【move(pos=[x,y,z])】【drop】【pickup】"
                    "【equip(item=minecraft:diamond_pickaxe)】；"
                    "把背包某格的物品丢给主人用 "
                    "【transfer(from=inv:格号,to=world:[主人x,主人y,主人z])】，"
                    "to 必须写主人的绝对坐标，不要用 ~）\n")
            if status:
                # 状态行本身是第三人称"女仆：..."，这里去前缀转成第一人称，
                # 否则"你当前状态：女仆：生命=..."里又冒出"女仆"字样，AI 会跟着第三人称。
                if status.startswith("女仆："):
                    status = "我的" + status[len("女仆："):]
                head += "（你当前状态：%s）\n" % status
            # 背包槽位 + 主人坐标：让 AI 能生成「把背包第 N 格的某物丢给主人」这类可执行指令
            try:
                inv = link.inventory_slots(maid)
            except Exception:
                inv = []
            if inv:
                head += "（女仆背包：%s）\n" % "，".join(
                    "%s×%d@槽%d" % (s["id"], s["count"], s["i"]) for s in inv[:16])
            try:
                op = link.owner_pos(maid)
            except Exception:
                op = None
            if op:
                head += "（主人坐标：%s）\n" % (list(op),)
            prompt = head + (extra + "\n\n" if extra else "") + text
            reply = (chat_service.chat(prompt, "女仆对话") or "").strip()
            if not reply:
                return
            # 纠错：模组的 world 槽位必须是绝对坐标，AI 常写成 world:~（无效）→ 换成主人坐标
            if op:
                rx = "world:%d,%d,%d" % (op[0], op[1], op[2])
                for bad in ("world:~", "world:owner", "world:me",
                            "world:主人", "world:我", "world:[~,~,~]"):
                    if bad in reply:
                        reply = reply.replace(bad, rx)
            # 1) 解析 AI 回复里的【指令】并下发给女仆（自动去掉指令文本）
            clean, _inject = self._ensure_maid_loop().process_reply(reply)
            # 2) 去掉动作标签 → 驱动桌宠动作
            try:
                import ai.client as ai_client
                clean, actions = ai_client.parse_ai_output(clean)
                if actions:
                    self.app._apply_ai_actions(actions)
            except Exception:
                pass
            clean = (clean or "").strip() or "好的"
            # 3) 回女仆：头顶气泡 + 游戏聊天栏
            link.send_chat_reply(clean, maid=maid)
            # 4) 语音朗读（桌宠后端，窗口隐藏也能出声）
            if speak:
                try:
                    self.msg.post(clean, kind="ai_reply", speak=True)
                except Exception:
                    pass
            self._log("女仆对话 -> %s" % clean)
        except Exception as exc:
            self._log("女仆对话失败: %s" % exc)

    # ---------- 女仆在场 → 隐藏/恢复桌宠窗口 ----------

    def _on_presence(self, online, maid=""):
        hide = bool(self._cfg("maid_hide_pet",
                              getattr(config, "MAID_HIDE_PET_ON_MAID", True)))
        self._log("收到女仆 presence online=%s hide=%s" % (online, hide))
        if not hide:
            return
        if online:
            if not self._hidden_by_maid:
                self._hidden_by_maid = True
                # 隐藏期间不再渲染桌宠气泡（气泡是独立窗口，hide 主窗挡不住）
                try:
                    self.app._suppress_bubble = True
                except Exception:
                    pass
                self._log("女仆上线：隐藏桌宠窗口 + 气泡（AI/语音后端保留）")
                try:
                    self.app.hide()
                except Exception:
                    pass
        else:
            self._restore_window()

    def _restore_window(self):
        if not self._hidden_by_maid:
            return
        self._hidden_by_maid = False
        self._log("女仆离线：恢复桌宠窗口")
        try:
            self.app._suppress_bubble = False
            self.app.show()
            self.app.raise_()
        except Exception:
            pass
