# -*- coding: utf-8 -*-
"""动作 key 规范来源（ActionKey）。

桌宠内部动作 key（"idle"/"think" 等）的统一枚举，替代散落的硬编码字符串。
- 动作 key 与 config.ACTIONS 的键一致（值 = ActionKey.value）；
- 素材目录名（config.ACTIONS 的值，如 "坐地上生气"/"正面"）**不参与枚举化**；
- 台词类别（config.AI_PRESET_LINES 的键，如 "click"/"drag"）与动作 key 同名不同义，
  也不参与枚举化；
- AI_ACTION_TAGS 的键（"开心"/"惊讶" 等 AI 自由生成的中文标签）为外部自由文本，
  同样不参与枚举化。

SEQUENCE 是特殊动作（右键「表演一套动作」），非 config.ACTIONS 成员。
"""
from enum import Enum


class ActionKey(Enum):
    IDLE = "idle"
    FRONT = "front"
    BACK = "back"
    SIDE = "side"
    SIT = "sit"
    ANGRY = "angry"
    RISE = "rise"
    TURN = "turn"
    THINK = "think"
    HAPPY = "happy"
    CRY = "cry"
    SHY = "shy"
    SURPRISED = "surprised"
    SLEEP = "sleep"
    JUMP = "jump"
    ROLL = "roll"
    DANCE = "dance"
    SEQUENCE = "sequence"      # 特殊：表演一套动作（非 ACTIONS 成员）

    @classmethod
    def from_value(cls, v):
        """容错转换：合法动作 key 返回枚举成员，否则返回 None。
        用于皮肤 role.json 等外部数据的未知 key 过滤。"""
        try:
            return cls(str(v))
        except (ValueError, TypeError):
            return None
