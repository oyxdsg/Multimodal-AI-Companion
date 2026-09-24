# 文档索引

本目录集中桌宠项目的设计、报告与指南。项目说明见 [`../README.md`](../README.md)、
更新历史见 [`../CHANGELOG.md`](../CHANGELOG.md)。

## 接口与规范

- [PLUGIN_API.md](PLUGIN_API.md) —— **插件 API 与生态规范**（第三方插件必读）
- [example-plugin/](example-plugin/) —— 最小示例插件

## 设计文档 `design/`

- [DESIGN_OPTIONAL.md](design/DESIGN_OPTIONAL.md) —— **开源瘦身 · 插件化**（宿主 / 扩展点 / P0–P5 落地记录）
- [DESIGN_AI_PROVIDERS.md](design/DESIGN_AI_PROVIDERS.md) —— AI 接口通用化（供应商注册表 + 协议适配器）
- [DESIGN_AI_ROADMAP.md](design/DESIGN_AI_ROADMAP.md) —— AI 与检索链路演进（LangChain 理念 + 本地 RAG 微量改造，P0–P3 路线图）
- [DESIGN_LOOP_DEEPENED.md](design/DESIGN_LOOP_DEEPENED.md) —— 通讯闭环（用户↔AI↔女仆），v0.3 已实施
- [DESIGN_LOOP.md](design/DESIGN_LOOP.md) —— 通讯闭环 v0.2（已被 v0.3 取代）
- [DESIGN_NLU.md](design/DESIGN_NLU.md) —— 本地意图识别 + 自动执行
- [DESIGN_WIKI_REFINE.md](design/DESIGN_WIKI_REFINE.md) —— Wiki 知识提炼（本地小模型）
- [DESIGN_UI_LANGUAGE.md](design/DESIGN_UI_LANGUAGE.md) —— WorkBuddy 视觉语言取色与移植
- [DESIGN_PERSONALIZE.md](design/DESIGN_PERSONALIZE.md) —— 个性化设置页（设计稿，未实现）

## 报告 `reports/`

- [TEST_PLAN.md](reports/TEST_PLAN.md) —— 全链路测试方案
- [CHAIN_TEST_REPORT.md](reports/CHAIN_TEST_REPORT.md) —— 全链路真机测试报告
- [API_PROMPT_TEST_REPORT.md](reports/API_PROMPT_TEST_REPORT.md) —— API 后端 × 模式 prompt 实测
- [WIKI_AUDIT.md](reports/WIKI_AUDIT.md) —— Wiki 链路实测与优化记录
- [RESEARCH_OPENCODE_PROVIDER.md](reports/RESEARCH_OPENCODE_PROVIDER.md) —— opencode provider 接口实证

## 指南

- [视频处理成图片成为宠物动作.md](视频处理成图片成为宠物动作.md) —— 绿幕视频 → 桌宠动作
