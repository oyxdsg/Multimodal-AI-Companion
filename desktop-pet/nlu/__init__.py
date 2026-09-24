# -*- coding: utf-8 -*-
"""桌面宠物 NLU（自然语言理解）子包。

用途：在把用户输入交给大 AI 之前，先用**本地小模型 + 规则**做一次预处理——
识别「用户想让女仆干活」的意图、抽出物品/数量/坐标等槽位，进而直接驱动女仆
执行（例如自动合成），并把结果结构化回传给大 AI。

模块
----
* ``taxonomy``  意图体系（意图集 ↔ 女仆指令映射，单一事实来源）
* ``synth``     训练语料合成（模板 × 词典 × 口语变体 × 同音错字噪声）
* ``features``  字符 n-gram 特征器（训练/推理共用）
* ``model``     意图识别小模型（纯 numpy，EmbeddingBag + MLP）
* ``train``     离线训练/评估/导出
* ``intent``    运行时推理入口
* ``slots``     槽位抽取（物品名拼音模糊匹配等）
* ``router``    意图 → 动作编排（含合成 dry-run 链路 + script 下发）
"""

__all__ = ["taxonomy", "synth", "features", "model", "intent", "slots", "router"]
