# -*- coding: utf-8 -*-
"""智能女仆联动（M5-b）：WebSocket Server，接收 SmartMaid 模组的感知上报并下发指令。

架构
----
模组侧（SmartMaid，Fabric）作为 **WebSocket Client** 连接本机 ``ws://127.0.0.1:<port>``；
本模块作为 **Server** 跑在独立线程的 asyncio 事件循环里。

- 收到的消息放进线程安全队列，由 Qt 主线程调用 :meth:`MaidLink.poll` 取出
  —— 本模块**自身不触碰任何 UI**，跨线程只走队列。
- 发送通过 ``run_coroutine_threadsafe`` 投递到 asyncio 线程。

协议（与模组 ``MaidWsClient`` 对齐）
------------------------------------
模组 → 桌宠::

    {"type":"hello","mod":"smartmaid","token":"...","maids":[{"id","name","owner"}]}
    {"type":"perception","maid":"<uuid>","seq":3,"tick":123,"full":false,
     "changed":{...},"removed":["self.status.target"]}
    {"type":"perception","maid":"<uuid>","seq":1,"tick":100,"full":true,"snapshot":{...}}
    {"type":"event","maid":"<uuid>","event":{"t":100,"type":"hurt","data":{...}}}
    {"type":"command_result","maid":"<uuid>","reply":{"id","ok","state","step","result"}}
    {"type":"item_index","count":1523,"items":{"铁镐":"minecraft:iron_pickaxe", ...}}
    {"type":"pong","t":123}

``item_index`` 在握手后由模组下发一次（当前语言的物品名 → id），
供 NLU 做中文/拼音模糊匹配（「稿子」→「镐子」）。

桌宠 → 模组::

    {"type":"hello_ack","ok":true}
    {"type":"command","maid":"","id":"c1","cmd":"mine","params":{...}}
    {"type":"command","maid":"","json":"{...完整指令 JSON，直接透传...}"}
    {"type":"speak","maid":"","text":"好的主人"}
    {"type":"animation","maid":"","name":"waving"}
    {"type":"ping","t":123}

依赖：``pip install websockets``
"""

import asyncio
import copy
import itertools
import json
import queue
import threading
import time

try:  # websockets >= 12 新 asyncio 实现
    from websockets.asyncio.server import serve as _ws_serve
except ImportError:  # 兼容旧版本
    try:
        from websockets import serve as _ws_serve
    except ImportError:  # 未安装
        _ws_serve = None

DEFAULT_PORT = 21420


# ---------------------------------------------------------------- 感知状态

def _merge_into(base, changed):
    """把 changed 递归合并进 base（与模组 PerceptionDiff.merge 语义一致）。"""
    for key, val in (changed or {}).items():
        exist = base.get(key)
        if isinstance(val, dict) and isinstance(exist, dict):
            _merge_into(exist, val)
        else:
            base[key] = copy.deepcopy(val)


def _remove_path(base, path):
    """按点分路径删除字段，如 self.status.target。"""
    parts = path.split(".")
    node = base
    for part in parts[:-1]:
        node = node.get(part)
        if not isinstance(node, dict):
            return
    node.pop(parts[-1], None)


def merge_snapshot(base, message):
    """把一条 perception 消息合并进基线快照（就地修改 base）。"""
    if message.get("full"):
        base.clear()
        snap = message.get("snapshot") or {}
        if isinstance(snap, dict):
            base.update(copy.deepcopy(snap))
        return
    _merge_into(base, message.get("changed") or {})
    for path in message.get("removed") or []:
        if isinstance(path, str):
            _remove_path(base, path)


def summarize_snapshot(snap):
    """把女仆快照摘要成一行中文状态（供气泡/日志用）。"""
    if not snap:
        return ""
    self_ = snap.get("self") or {}
    status = self_.get("status") or {}
    equip = self_.get("equipment") or {}
    pos = self_.get("pos") or []
    pos_str = "(%s,%s,%s)" % tuple(
        [round(v) if isinstance(v, (int, float)) else "?" for v in (pos + ["?", "?", "?"])[:3]])
    parts = []
    if self_.get("health") is not None:
        parts.append("生命=%s" % self_.get("health"))
    if pos_str != "(?,?,?)":
        parts.append("位置=%s" % pos_str)
    task = status.get("task_id")
    parts.append("任务=%s" % (task if task else "待命"))
    if status.get("target"):
        tgt = status.get("target") or {}
        parts.append("目标=%s" % _short_name(tgt.get("type")))
    mainhand = (equip.get("mainhand") or {}).get("id")
    if mainhand and mainhand != "minecraft:air":
        parts.append("手持=%s" % mainhand)
    return "女仆：" + "，".join(parts)


# 任务 id → 中文（与模组 /maidtasks 指令集一致）
TASK_NAMES = {
    "attack": "战斗", "guard": "护卫", "feed": "喂食", "eat": "进食", "mine": "挖矿", "farm": "耕作",
    "build": "建造", "collect": "收集", "craft": "合成", "smelt": "烧炼",
    "transfer": "物品转移", "chestopen": "开箱子", "chestput": "存箱子",
    "chesttake": "取箱子", "move": "移动", "look": "张望", "break": "挖方块",
    "place": "放置", "use": "使用物品", "equip": "换装", "store": "收纳",
    "drop": "丢出", "pickup": "拾取", "sit": "坐下", "stop": "停下",
    "script": "脚本任务", "harvest": "收割",
}

# 环境危险类型 → 中文
DANGER_NAMES = {"water": "水域", "lava": "岩浆", "fire": "火焰"}

# 实体 id 后缀 → 中文（生物/怪物名，供感知事件与状态行翻译）。
# 覆盖女仆战斗/索敌常见目标；未列出的回退英文短名。
ENTITY_CN = {
    "zombie": "僵尸", "husk": "尸壳", "drowned": "溺尸", "zombified_piglin": "僵尸猪灵",
    "skeleton": "骷髅", "stray": "流浪者", "bogged": "苔藓骷髅",
    "creeper": "苦力怕", "spider": "蜘蛛", "cave_spider": "洞穴蜘蛛",
    "enderman": "末影人", "witch": "女巫", "slime": "史莱姆", "magma_cube": "岩浆怪",
    "blaze": "烈焰人", "ghast": "恶魂", "pillager": "掠夺者", "vindicator": "卫道士",
    "evoker": "唤魔者", "illusioner": "幻术师", "ravager": "劫掠兽", "vex": "恼鬼",
    "guardian": "守卫者", "elder_guardian": "远古守卫者", "shulker": "潜影贝",
    "warden": "监守者", "phantom": "幻翼", "silverfish": "蠹虫",
    "zoglin": "僵尸疣猪兽", "hoglin": "疣猪兽", "piglin": "猪灵",
    "piglin_brute": "猪灵蛮兵", "wither_skeleton": "凋灵骷髅", "wither": "凋灵",
    "ender_dragon": "末影龙", "shulker_bullet": "潜影贝导弹",
    "parched": "帕查德", "breeze": "旋风人", "sporeling": "孢子人",
    # 常见生物（非敌对，用于状态行/掉落说明）
    "pig": "猪", "cow": "牛", "sheep": "羊", "chicken": "鸡", "rabbit": "兔子",
    "wolf": "狼", "cat": "猫", "horse": "马", "donkey": "驴", "mule": "骡",
    "fox": "狐狸", "panda": "熊猫", "polar_bear": "北极熊", "villager": "村民",
    "iron_golem": "铁傀儡", "snow_golem": "雪傀儡", "goat": "山羊",
    "allay": "悦灵", "axolotl": "美西螈", "turtle": "海龟", "frog": "青蛙",
    "bee": "蜜蜂", "cod": "鳕鱼", "salmon": "鲑鱼", "pufferfish": "河豚",
    "tropical_fish": "热带鱼", "squid": "鱿鱼", "glow_squid": "发光鱿鱼",
    "dolphin": "海豚", "bat": "蝙蝠", "ocelot": "豹猫", "parrot": "鹦鹉",
    "llama": "羊驼", "trader_llama": "行商羊驼", "wandering_trader": "流浪商人",
    "strider": "炽足兽", "camel": "骆驼", "sniffer": "嗅探兽",
}


def _short_name(ident):
    """minecraft:zombie → 僵尸；无翻译时回退英文短名 zombie。"""
    if not ident:
        return "?"
    key = str(ident).split(":")[-1]
    return ENTITY_CN.get(key, key)


def event_to_text(event):
    """把模组感知事件转成一句中文（供气泡/播报）。无法识别时返回空串。"""
    if not isinstance(event, dict):
        return ""
    etype = (event.get("type") or "").lower()
    data = event.get("data") or {}
    if etype == "hurt":
        who = _short_name(data.get("attacker"))
        hp = data.get("health")
        return "女仆被 %s 攻击了，剩余生命 %s" % (who, hp if hp is not None else "?")
    if etype == "enemy_spotted":
        dist = data.get("dist")
        tail = "（%s 格外）" % dist if dist is not None else ""
        return "女仆发现了%s%s" % (_short_name(data.get("type")), tail)
    if etype == "task_started":
        task = TASK_NAMES.get(data.get("task"), data.get("task"))
        return "女仆开始%s了" % task
    if etype == "task_done":
        task = TASK_NAMES.get(data.get("task"), data.get("task"))
        result = data.get("result") or {}
        if isinstance(result, dict):
            reason = result.get("reason")
            if reason:
                return "女仆%s完成：%s" % (task, reason)
        return "女仆完成%s了" % task
    if etype == "environment_danger":
        danger = DANGER_NAMES.get(data.get("danger"), "危险")
        return "女仆%s了%s" % ("进入" if data.get("enter") else "离开", danger)
    return ""


# ---------------------------------------------------------------- 联动主体

class MaidLink:
    """WebSocket Server + 感知状态缓存 + 指令下发。线程安全（跨线程只走队列）。"""

    def __init__(self, port=DEFAULT_PORT, token="", log=None):
        self.port = int(port)
        self.token = token or ""
        self._log = log
        self._inbox = queue.Queue()       # 收到的消息（dict）→ 主线程 poll
        self._state = {}                  # maid_id -> 合并后的快照
        self._events = []                 # 未取走的事件
        self._replies = []                # 未取走的指令回执
        self._pending = {}                # cmd_id -> {"event": Event, "reply": dict}
        self._seq = itertools.count(1)    # 指令 id 自增序号（避免同毫秒撞号）
        self._item_index = {}             # 物品名 → minecraft:id（模组握手后下发）
        self._conns = set()               # 当前连接（通常 1 个）
        self._loop = None
        self._server = None
        self._thread = None
        self._thread_ready = threading.Event()
        self._stopping = False
        self.last_seen = 0.0              # 最近一次收到模组消息的时间
        # 感知上报统计（用于实测增量 diff 的真实收益）
        self.stats = {"perception": 0, "total_bytes": 0,
                      "full_count": 0, "full_bytes": 0,
                      "inc_count": 0, "inc_bytes": 0}

    # ---------------- 生命周期 ----------------

    @property
    def available(self):
        """运行环境是否具备依赖。"""
        return _ws_serve is not None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def connected(self):
        return bool(self._conns)

    def start(self):
        if self.running:
            return True
        if not self.available:
            self._say("未安装 websockets 库，女仆联动不可用（pip install websockets）")
            return False
        self._stopping = False
        self._thread = threading.Thread(
            target=self._run, name="maid-link", daemon=True)
        self._thread.start()
        self._thread_ready.wait(timeout=3)
        return True

    def stop(self):
        """关闭服务：先关 server（serve_forever 随之返回）再回收线程。"""
        self._stopping = True
        loop, server, thread = self._loop, self._server, self._thread
        if loop is not None:
            try:
                # 关 server 而不是 stop loop：让 _serve 自然收尾，避免事件循环已关闭的报错
                if server is not None:
                    loop.call_soon_threadsafe(server.close)
                else:
                    loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        if thread is not None:
            thread.join(timeout=2)
            if thread.is_alive() and loop is not None:
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except Exception:
                    pass
                thread.join(timeout=1)
        self._thread = None
        self._server = None
        self._conns.clear()
        # 唤醒所有等回执的线程，避免 stop 时被卡满超时
        for slot in list(self._pending.values()):
            slot["event"].set()
        self._pending.clear()

    def _run(self):
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except Exception as exc:
            self._say("女仆联动服务异常: %s" % exc)
        finally:
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None

    async def _serve(self):
        try:
            server = await _ws_serve(self._handler, "127.0.0.1", self.port)
        except OSError as exc:
            self._say("女仆联动端口 %d 不可用: %s" % (self.port, exc))
            self._thread_ready.set()
            return
        self._server = server
        self._say("女仆联动已监听 ws://127.0.0.1:%d" % self.port)
        self._thread_ready.set()
        try:
            await server.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            self._server = None

    # ---------------- 连接处理（asyncio 线程） ----------------

    async def _handler(self, websocket, *args):
        peer = getattr(websocket, "remote_address", None)
        self._conns.add(websocket)
        self.last_seen = time.time()
        try:
            async for raw in websocket:
                self.last_seen = time.time()
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                if not self._check_token(msg):
                    self._say("握手 token 不匹配，拒绝连接")
                    await websocket.close(1008, "bad token")
                    return
                self._on_message(msg, websocket, len(raw.encode("utf-8")))
        except Exception:
            pass
        finally:
            self._conns.discard(websocket)
            if self._conns:
                self._say("女仆联动：一个连接断开（仍有 %d 个）" % len(self._conns))
            else:
                self._say("女仆联动已断开")

    def _check_token(self, msg):
        """token 校验：桌宠侧配置了 token 才校验（留空 = 不校验）。"""
        if not self.token:
            return True
        if msg.get("type") == "hello":
            return (msg.get("token") or "") == self.token
        return True  # 后续消息沿用已通过握手的连接

    def _on_message(self, msg, websocket, size=0):
        mtype = msg.get("type") or ""
        if mtype == "hello":
            maids = msg.get("maids") or []
            self._say("女仆联动已握手（模组 %s，女仆 %d 只）" % (msg.get("mod"), len(maids)))
            self._reply({"type": "hello_ack", "ok": True}, websocket)
            self._inbox.put(msg)
            return
        if mtype == "perception":
            maid = msg.get("maid") or ""
            if msg.get("full"):
                self._state[maid] = {}
            base = self._state.setdefault(maid, {})
            merge_snapshot(base, msg)
            st = self.stats
            st["perception"] += 1
            st["total_bytes"] += size
            if msg.get("full"):
                st["full_count"] += 1
                st["full_bytes"] += size
            else:
                st["inc_count"] += 1
                st["inc_bytes"] += size
            if msg.get("full"):
                self._inbox.put(msg)
            return
        if mtype == "event":
            self._events.append(msg.get("event") or {})
            self._inbox.put(msg)
            return
        if mtype == "item_index":
            items = msg.get("items") or {}
            if isinstance(items, dict) and items:
                self._item_index = {str(k): str(v) for k, v in items.items()
                                    if k and v}
                self._say("已收到物品索引 %d 条" % len(self._item_index))
            self._inbox.put(msg)
            return
        if mtype == "chat":
            # 玩家在聊天栏对女仆说的话 → 主线程走 AI 对话
            self._inbox.put(msg)
            return
        if mtype == "maid_presence":
            # 女仆上线/离线通知 → 主线程按设置隐藏/恢复桌宠窗口
            self._inbox.put(msg)
            return
        if mtype == "command_result":
            reply = msg.get("reply") or {}
            self._replies.append(reply)
            # 唤醒等待该指令回执的调用方（NLU 自动执行链路）
            rid = reply.get("id")
            slot = self._pending.pop(rid, None) if rid else None
            if slot is not None:
                slot["reply"] = reply
                slot["event"].set()
            self._inbox.put(msg)
            return
        if mtype == "ping":
            # 原样回带 t，模组侧才能算出往返延迟
            self._reply({"type": "pong", "t": msg.get("t")}, websocket)
            return
        if mtype == "pong":
            return

    def _reply(self, obj, websocket):
        loop = self._loop
        if loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                websocket.send(json.dumps(obj, ensure_ascii=False)), loop)
        except Exception:
            pass

    # ---------------- 主线程接口 ----------------

    def poll(self):
        """取出自上次调用以来收到的消息（dict 列表）。由 Qt 主线程调用。"""
        out = []
        while True:
            try:
                out.append(self._inbox.get_nowait())
            except queue.Empty:
                break
        return out

    def drain_events(self):
        """取走并清空事件列表。"""
        events, self._events = self._events, []
        return events

    def drain_replies(self):
        """取走并清空指令回执列表。"""
        replies, self._replies = self._replies, []
        return replies

    def item_index(self):
        """模组下发的「物品名 → minecraft:id」索引（未下发时为空 dict）。"""
        return dict(self._item_index)

    def inventory_counts(self, maid=""):
        """把快照里的背包槽位汇总成 {物品 id: 总数}。"""
        inv = (self.state(maid) or {}).get("inventory") or {}
        counts = {}
        for slot in inv.get("slots") or []:
            if not isinstance(slot, dict):
                continue
            iid = slot.get("id")
            if not iid:
                continue
            counts[iid] = counts.get(iid, 0) + int(slot.get("count") or 0)
        return counts

    def inventory_slots(self, maid=""):
        """背包槽位明细 ``[{"i": 槽位号, "id": ..., "count": ...}]``。

        NLU 需要**槽位号**才能拼出 ``inv:<n>`` 的转移源
        （「把铁锭换到主手」「把头盔戴上」都要先知道它在哪一格）。
        """
        inv = (self.state(maid) or {}).get("inventory") or {}
        out = []
        for slot in inv.get("slots") or []:
            if not isinstance(slot, dict):
                continue
            iid = slot.get("id")
            if not iid:
                continue
            try:
                idx = int(slot.get("i"))
            except (TypeError, ValueError):
                continue
            out.append({"i": idx, "id": iid,
                        "count": int(slot.get("count") or 0)})
        out.sort(key=lambda s: s["i"])
        return out

    @staticmethod
    def _pos_of(section):
        pos = (section or {}).get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) >= 3:
            try:
                return tuple(int(round(float(v))) for v in pos[:3])
            except (TypeError, ValueError):
                return None
        return None

    def maid_pos(self, maid=""):
        """女仆当前方块坐标 (x, y, z)；无数据返回 None。"""
        return self._pos_of((self.state(maid) or {}).get("self"))

    def owner_pos(self, maid=""):
        """主人当前方块坐标；模组未上报时为 None。"""
        return self._pos_of((self.state(maid) or {}).get("owner"))

    # ---------------- 请求/等待回执（阻塞，仅供工作线程调用） ----------------

    def _new_cmd_id(self):
        return "pet-%d-%d" % (int(time.time() * 1000), next(self._seq))

    def wait_reply(self, cmd_id, timeout=5.0):
        """等待某条指令的回执；超时返回 None。

        只能在**工作线程**调用（会阻塞）。回执同时仍会进 :meth:`poll`，
        不影响「不等待」的既有消费路径。
        """
        slot = self._pending.get(cmd_id)
        if slot is None:
            return None
        try:
            got = slot["event"].wait(timeout)
        finally:
            self._pending.pop(cmd_id, None)
        return (slot.get("reply") if got else None)

    def request(self, cmd, params=None, maid="", timeout=5.0,
                cancel_previous=False):
        """下发指令并同步等待回执（阻塞）。

        :param cancel_previous: 是否抢占女仆当前任务（默认 False = 排队）。
        :return: 回执 dict（``{"id","ok","state","step","result"}``）；未连接/超时返回 None
        """
        if not self.connected or self._loop is None:
            return None
        cmd_id = self._new_cmd_id()
        slot = {"event": threading.Event(), "reply": None}
        self._pending[cmd_id] = slot
        try:
            sent = self.send_command(cmd, params, maid=maid, cmd_id=cmd_id,
                                     cancel_previous=cancel_previous)
            if not sent:
                self._pending.pop(cmd_id, None)
                return None
            return self.wait_reply(cmd_id, timeout)
        except Exception:
            self._pending.pop(cmd_id, None)
            return None

    def query_craft(self, item_id, count=1, timeout=3.0, maid=""):
        """干跑查询配方（不消耗材料）。返回模组侧 ``result`` 字段；失败返回 None。"""
        reply = self.request("craft_check", {"item": item_id, "count": int(count)},
                             maid=maid, timeout=timeout)
        if not reply or not reply.get("ok"):
            return None
        result = reply.get("result") or {}
        return result if isinstance(result, dict) else None

    def query_status(self, timeout=2.0, maid=""):
        """查询女仆当前任务（纯查询：不建任务、不取消任何东西）。"""
        reply = self.request("status", {}, maid=maid, timeout=timeout,
                             cancel_previous=False)
        if not reply or not reply.get("ok"):
            return None
        result = reply.get("result") or {}
        return result if isinstance(result, dict) else None

    def wait_task_done(self, task_id, timeout=4.0, maid="", interval=0.15):
        """等某个任务结束（轮询 status 直到当前任务不再是它）。

        为什么需要：模组的 ``chestopen`` 是**异步任务**——回执只说「已受理」，
        真正把容器记为「已打开」是在任务 tick 里做的。所以 ``chestput`` /
        ``chesttake`` 之前必须等它跑完，否则一定报「女仆还没打开箱子」。
        """
        deadline = time.time() + max(0.5, timeout)
        while True:
            left = deadline - time.time()
            if left <= 0:
                return False
            st = self.query_status(timeout=min(2.0, max(0.3, left)), maid=maid)
            if st is None:
                return False
            if st.get("task") != task_id:
                return True
            time.sleep(interval)

    def state(self, maid=""):
        """某女仆的合并后快照（maid 留空 = 第一只）。"""
        if maid and maid in self._state:
            return self._state[maid]
        # 指定了 uuid 才精确取；否则优先返回「活着」的那只——
        # 女仆死亡后 noSave 消失，但旧 uuid 的快照会冻结在 health=0，
        # 重召的新女仆是另一个 uuid，直接取「第一只」会拿到死的那只。
        for key, snap in self._state.items():
            hp = (snap.get("self") or {}).get("health")
            if hp is None or hp > 0:
                return snap
        for key, snap in self._state.items():
            return snap
        return {}

    def snapshot(self, maid=""):
        """合并后快照的**深拷贝**（供外部读取/转储，不暴露内部可变引用）。

        全链路测试用它做「指令前 / 指令后」状态 diff，验证指令是否真的落到游戏世界。
        """
        import copy
        return copy.deepcopy(self.state(maid))

    def maids(self):
        """已知女仆 id 列表（按首次出现顺序）。"""
        return list(self._state.keys())

    def status_line(self, maid=""):
        """一行中文状态摘要。"""
        return summarize_snapshot(self.state(maid))

    def stats_line(self):
        """感知上报统计（实测增量 diff 收益）；无数据时返回空串。"""
        st = self.stats
        if not st["perception"]:
            return ""
        parts = ["感知 %d 条、合计 %.1f KB" % (st["perception"], st["total_bytes"] / 1024.0)]
        if st["full_count"] and st["inc_count"]:
            full_avg = st["full_bytes"] / st["full_count"]
            inc_avg = st["inc_bytes"] / st["inc_count"]
            saving = (1 - inc_avg / full_avg) * 100 if full_avg > 0 else 0
            parts.append("全量均 %.0fB、增量均 %.0fB（降 %.1f%%）"
                         % (full_avg, inc_avg, saving))
        return "；".join(parts)

    # ---------------- 下发 ----------------

    def _send(self, obj):
        if not self._conns or self._loop is None:
            return False
        payload = json.dumps(obj, ensure_ascii=False)
        for ws in list(self._conns):
            try:
                asyncio.run_coroutine_threadsafe(ws.send(payload), self._loop)
            except Exception:
                continue
        return True

    def send_command(self, cmd, params=None, maid="", cmd_id=None,
                     cancel_previous=False, persist=False):
        """下发一条女仆指令（走模组 MaidAIBridge，指令集与 /maidtasks 一致）。

        cancel_previous 默认 False（排队，不抢占女仆当前任务）。"""
        msg = {
            "type": "command",
            "maid": maid,
            "id": cmd_id or ("pet-%d" % int(time.time() * 1000)),
            "cmd": cmd,
            "params": params or {},
            "cancel_previous": cancel_previous,
        }
        if persist:
            msg["persist"] = True
        return self._send(msg)

    def send_speak(self, text, maid="", ticks=80):
        """让女仆头顶冒气泡（+ 桌宠可另行配音）。"""
        return self._send({"type": "speak", "maid": maid, "text": text, "ticks": ticks})

    def send_chat_reply(self, text, maid=""):
        """把 AI 回复回给模组：女仆头顶气泡 + 主人游戏内聊天栏。"""
        if not text:
            return False
        return self._send({"type": "chat_reply", "maid": maid, "text": text})

    def send_animation(self, name="", anim_id=None, maid=""):
        """让女仆播放动作（Emotecraft：waving / clap / backflip ...）。"""
        if anim_id is not None:
            return self._send({"type": "animation", "maid": maid, "id": anim_id})
        return self._send({"type": "animation", "maid": maid, "name": name})

    def _say(self, text):
        if self._log:
            try:
                self._log(text)
            except Exception:
                pass
