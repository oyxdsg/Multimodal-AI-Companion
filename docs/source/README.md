# 原始设计稿（source）

这里的文件是**设计源头**：项目里的成品（人设提示词、建筑识别规则等）由它们整理而来，
保留在此以便追溯"当初为什么这么定"。

| 文件 | 是什么 | 对应成品 |
|---|---|---|
| `Prompt.docx` | 人格「宠物女仆」人设原始稿 | `desktop-pet/prompt.txt` 及精简/极简版 |
| `prompt2.docx` | 人格「御坂美琴」人设原始稿 | `desktop-pet/prompt2*.txt` |
| `prompt精简版api用.docx` | 面向无状态 API 后端的人设精简稿 | `desktop-pet/prompt_api.txt` / `prompt_min.txt` |
| `情绪系统.docx` | 情绪 / 情态值理论（情绪强度 = 情态值高低） | 人设提示词的「情绪理论」段 |
| `建筑识别方案.docx` | 建筑识别系统的设计思路 | `deskpet-mod/src/main/java/com/deskpet/mod/build/` |
| `建筑知识库.txt` | 建筑分类与优化建议的素材 | `BuildingAdviceKb.java` 的建议条目 |

> 这些是**只读参考稿**，不参与运行；改代码不需要动它们。
> 人设的**实际生效文件**始终是 `desktop-pet/prompt*.txt`（程序启动时加载）。
