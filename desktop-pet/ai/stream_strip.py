# -*- coding: utf-8 -*-
r"""流式文本的标签/指令剥离 + 朗读切句（设计见 DESIGN_AI_PROVIDERS.md §11.2）。

要解决的两个缺陷
----------------
1. **显示层**：原先对累积全文调 `parse_ai_output`，而动作标签正则**要求闭合符**，
   流式中途的 `"好的{开"` 会原样返回 → **半个标签闪现**。
2. **语音层（会把指令念出来）**：原先"先切句、再逐句剥离"，而切句正则
   `(?<=[。！？!?…~；;])` 把 **`~` 当句末终止符**，偏偏 DSL 的相对坐标就用 `~`
   → `【move(pos=~,~,~)】` 被切成 4 段、每段缺 `】` 剥离失败 → **逐段朗读指令**。

解法
----
**一个剥离器，两个下游**；且**剥离必须发生在切句之前**：

    stream() → TagStripper.feed(delta) → (safe_text, captured_tags)
                        ├→ 显示层
                        └→ 句切分 → TTS

`TagStripper` 是状态机 + **holdback 缓冲**：见到开标签符后若还没闭合，
就压住不发（既不显示也不朗读），等闭合符到了再整段吞掉；
超过长度上限或超时仍不闭合，则判定为普通文本放行，避免卡住不显示。

上限分两档（依据现有正则）：
* 动作标签：`[\[{]([^\]\}]{1,10})[\]}]` → 内部 ≤10 字，回退上限 12
* DSL：`【\s*\w+\s*(?:\(.*?\))?\s*】` 可带长参数（如 `【attack(target=minecraft:pig,range=10)】`）
  → 上限 96 字 **+ 超时兜底**
"""

import re
import time

from dataclasses import dataclass

# 标签正则的**规范定义**（ai/client.py 从这里取，避免两份事实）
# 兼容新提示词的 {} 动作标签与旧版 [] 标签
ACTION_TAG_RE = re.compile(r"[\[{]([^\]\}]{1,10})[\]}]")
DSL_RE = re.compile(r"【\s*\w+\s*(?:\(.*?\))?\s*】", re.S)
# 句末终止符：**保留 `~`**（"好的呢~" 是正常语气），
# 之所以以前会出事是因为剥离顺序错了，而不是 `~` 不该在这里
SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?…~；;])")

ACTION_MAX = 12       # 开 + 10 + 闭
DSL_MAX = 96          # 含长参数
HOLD_TIMEOUT_S = 0.3  # 超过这么久还不闭合就当作普通文本放行

KIND_ACTION = "action"
KIND_DSL = "dsl"


@dataclass
class Tag:
    """捕获到的完整标签（不是简单地丢弃——动作标签要驱动动画，DSL 要下发女仆）。"""

    kind: str      # KIND_ACTION / KIND_DSL
    inner: str     # 动作词 或 指令全文（不含【】）
    raw: str       # 原始文本（含定界符）

    @property
    def is_action(self):
        return self.kind == KIND_ACTION

    @property
    def is_dsl(self):
        return self.kind == KIND_DSL


class TagStripper:
    """增量剥离器。**一轮对话一个实例**（状态不可跨轮复用）。

    :param clock: 单调时钟（测试可注入假时钟）
    """

    def __init__(self, clock=None):
        self._buf = ""
        self._clock = clock or time.monotonic
        self._hold_since = None

    # ---------------- 对外 ----------------

    def feed(self, text):
        """喂入增量，返回 ``(safe_text, [Tag, ...])``。"""
        if text:
            self._buf += text
        return self._drain()

    def flush(self):
        """收尾：把 holdback 里的残余当普通文本放行（防止最后一个标签之后的正文被吞）。"""
        out = self._buf
        self._buf = ""
        self._hold_since = None
        return out, []

    def reset(self):
        self._buf = ""
        self._hold_since = None

    @property
    def pending(self):
        """当前被压住的长度（调试/测试用）。"""
        return len(self._buf)

    # ---------------- 状态机 ----------------

    def _drain(self):
        buf = self._buf
        out = []
        tags = []
        i = 0
        n = len(buf)
        while i < n:
            ch = buf[i]
            opener = ch in "{[【"
            if not opener:
                out.append(ch)
                self._hold_since = None
                i += 1
                continue

            match = self._match_at(buf, i)
            if match is None:
                # 可能是"还没闭合的标签" → holdback
                if self._hold_since is None:
                    self._hold_since = self._clock()
                held = buf[i:]
                # 超长或超时 → 判定为普通文本，放行
                if self._expired(held):
                    out.append(ch)
                    self._hold_since = None
                    i += 1
                    continue
                break
            tag, end = match
            tags.append(tag)
            self._hold_since = None
            i = end

        self._buf = buf[i:]
        return "".join(out), tags

    def _expired(self, held):
        limit = DSL_MAX if held.startswith("【") else ACTION_MAX
        if len(held) > limit:
            return True
        if self._hold_since is None:
            return False
        return (self._clock() - self._hold_since) > HOLD_TIMEOUT_S

    @staticmethod
    def _match_at(buf, i):
        """从 i 处的开标签符尝试匹配一个**完整**标签。

        返回 ``(Tag, 结束下标)``；未闭合或形状不合法返回 ``None``。
        """
        ch = buf[i]
        if ch == "【":
            j = buf.find("】", i + 1)
            if j < 0:
                return None
            raw = buf[i:j + 1]
            if not DSL_RE.fullmatch(raw):
                return None
            return Tag(KIND_DSL, raw[1:-1].strip(), raw), j + 1

        # `{` / `[`：沿用既有正则的宽松配对（闭合符可以是 ] 或 }）
        ends = [k for k in (buf.find("]", i + 1), buf.find("}", i + 1)) if k >= 0]
        if not ends:
            return None
        j = min(ends)
        raw = buf[i:j + 1]
        if not ACTION_TAG_RE.fullmatch(raw):
            return None
        return Tag(KIND_ACTION, raw[1:-1].strip(), raw), j + 1


def strip_tags_incremental(text, clock=None):
    """一次性剥离（非流式场景的便捷函数，语义与 parse_ai_output 的剥离一致）。"""
    s = TagStripper(clock=clock)
    safe, tags = s.feed(text or "")
    tail, more = s.flush()
    return safe + tail, tags + more


def split_speakable(text):
    """把**已剥离**的文本按句末终止符切开，返回 (完整句列表, 未完成的尾部)。

    约定：尾部不朗读（留给收尾时读），避免把半句话念出来。
    """
    if not text:
        return [], ""
    parts = SENT_SPLIT_RE.split(text)
    complete = [p.strip() for p in parts[:-1] if p.strip()]
    return complete, (parts[-1] if parts else "")
