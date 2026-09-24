# 贡献指南

感谢愿意动手。这个仓库是三个子项目 + 若干文档的单仓库工程，先选对入口再下手。

## 一、想改什么 → 去哪

| 想改什么 | 去哪 | 先读 |
|---|---|---|
| 桌宠本体（界面 / 聊天 / 语音 / 游戏联动 / 小游戏） | `desktop-pet/` | [README](desktop-pet/README.md) · [AI 接口设计](desktop-pet/docs/design/DESIGN_AI_PROVIDERS.md) |
| 插件（AI 后端 / 知识库 / 素材） | `desktop-pet/plugins/` | [PLUGIN_API.md](desktop-pet/docs/PLUGIN_API.md) · [示例插件](desktop-pet/docs/example-plugin/) |
| Minecraft 模组（事件采集 / 建筑识别） | `deskpet-mod/` | [README](deskpet-mod/README.md) |
| 素材制作工具（绿幕 → 动作帧 → 角色包） | `deskpet-tools/` | [README](deskpet-tools/README.md) |
| 设计稿 / 测试报告 / 原始文档 | `desktop-pet/docs/` · `docs/` | [文档索引](docs/README.md) |

> 游戏里的**女仆本体**是姊妹项目 SmartMaid（独立仓库），不在本仓库内；
> 本仓库只含与它通信的模组 `deskpet-mod/` 与桌宠侧联动代码。

## 二、开发环境

```bash
pip install -r desktop-pet/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
python desktop-pet/main.py
```

* Python **3.10+**，当前主要目标平台是 **Windows**。
* 仓库**不含动画素材**：首次启动是静态形象，属**预期行为**不是 bug（补素材见根 README）。
* 只做自检、不开界面：`python desktop-pet/main.py --smoke`
* 看插件与依赖状态：`python desktop-pet/main.py --doctor`
* 改设置界面又不想启桌宠：`python desktop-pet/tools/preview_settings_dialog.py`（离屏出预览图）

## 三、提交前请跑测试

```bash
python desktop-pet/tests/test_providers.py     # 供应商 / 模型 / 能力位 + 版本铁律
python desktop-pet/tests/test_plugins.py       # 插件宿主契约
python desktop-pet/tests/test_knowledge_qa.py  # 知识扩展点
```

`desktop-pet/tests/` 下是**无框架纯断言**脚本（不需要 pytest）：逐行打印 `[P1] … OK`，
末行打印「全部通过」；断言失败会直接抛异常 → **非零退出即失败**。

**改哪块就跑哪块**。现有 16 个测试覆盖：AI 链路（chat 单元 / 链路 / 供应商）、NLU、
工具调用、结构化输出、重试、trace、agent 状态机、语音（TTS 目录 / 音频流）、
静态兜底、重建、女仆循环、插件、知识问答。

> 少数测试会用到 PySide6（界面相关）；按上面的 `requirements.txt` 装齐后全部可跑。
> 纯逻辑测试在没装 PySide6 的环境里也能跑，相关用例会自行跳过。

## 四、这个项目当纪律来守的几条

1. **版本铁律**：`desktop-pet/config.py` 的 `VERSION` 必须**等于** `desktop-pet/CHANGELOG.md`
   顶部的版本号（有测试守卫，改错会红）。改功能请同时改**两处**并补一节 CHANGELOG。
   新增能力升**次版本**，修 bug 升**修订号**。
2. **契约只增不改**：`desktop-pet/plugin/api.py` 与 `plugin/contracts.py` 是插件的公共契约。
   新增能力一律以**可选方法 / 可选字段**追加；破坏性变更必须升 `API_VERSION`
   并在 `PLUGIN_API.md` 写迁移说明。
3. **插件不 import 主体内部模块**（`ai.*` / `pet.*` / `game.*` / `core.*`），
   只用 `plugin.api` 与 `plugin.contracts`。需要模型能力 → 用 `host.llm()`。
4. **文档即规格**：设计稿在 `desktop-pet/docs/design/`（`DESIGN_*`），
   测试与审计报告在 `desktop-pet/docs/reports/`。改了行为就改对应文档。
   **未做的事要明确标注为未做** —— 本项目宁可留一条"已知限制"，也不留含糊。
5. **不提交大文件与凭据**：动画素材、`wiki_data/`、插件 `data/`、`.venv/`、`logs/`、
   任何 API Key 都不入库（见根 `.gitignore`）。数据类内容请给**重建脚本**，不要给数据本身。

## 五、提交信息

```
feat(desktop-pet): 2.31.0 知识能力插件化 —— 新增 knowledge-qa 扩展点与 HostAPI.llm()
fix(deskpet-mod): 修正坡顶被误判为 pointed 的评分
docs: 补插件 API 示例与贡献指南
```

格式：`<类型>(<范围>): <一句话> —— <要点>`。类型用 `feat` / `fix` / `docs` / `refactor` / `test` / `chore`；
范围用子项目名（`desktop-pet` / `deskpet-mod` / `deskpet-tools` / `docs`）。

## 六、写插件

插件是**可选增强** —— 宿主一个插件都不装也完整可用，请保持这个性质。

1. 复制 [`desktop-pet/docs/example-plugin/`](desktop-pet/docs/example-plugin/) 到 `desktop-pet/plugins/<你的id>/`
2. 改 `plugin.json` 的 `id` / `name` / `type` / `priority`（目录名与 `id` 保持一致）
3. 实现对应 Protocol 的方法；**失败自己兜底**，返回 `None` / `""`
4. `python desktop-pet/main.py --doctor` 确认被发现、已注册、扩展点最终实现是你

扩展点、清单字段、两种接入形态（目录包 / pip 包）、容错与生命周期，见
[PLUGIN_API.md](desktop-pet/docs/PLUGIN_API.md)。命名约定：插件 id 小写连字符，
目录名同 id，pip 包名 `deskpet-plugin-<id>`。

> **第一方插件**（`wiki-local` / `agentic-rag` / `deepseek-web`）代码随主仓库发布、
> **数据不随**；第三方插件建议独立仓库。

> ⚠️ **插件是主进程内的可执行代码，宿主不做沙箱**。提 PR 时请避免：静默联网上报、
> 写用户私有目录、读取与功能无关的本地数据。

## 七、报告问题

发 issue 时请带上：

* 系统版本 + `python --version` + 桌宠版本（`desktop-pet/config.py` 的 `VERSION`，或 `--doctor` 输出）
* 复现步骤、期望结果、实际结果
* `python desktop-pet/main.py --doctor` 的完整输出（插件 / 依赖问题**必带**）
* 相关日志：`desktop-pet/logs/`（插件问题看 `logs/plugin.log`）

**不要贴 API Key**（截图里也要遮）。怀疑是 AI 回复质量问题时，请说明用的是哪个后端与模型，
以及是否装了知识插件。

## 八、许可

提交即表示你同意以 **MIT** 许可（见 [LICENSE](LICENSE)）发布你的贡献。
若引入第三方代码或数据，请一并更新 [NOTICE](NOTICE) 说明来源与许可。

⚠️ **两类内容与 MIT 不兼容，不要直接往主仓库里放**：

1. **Wiki 类数据**（Minecraft 中文 Wiki，CC BY-NC-SA）—— 只给重建脚本，不给数据；
2. **角色美术素材** —— 默认形象「大肥鱼 / 女仆鲸鱼娘」是他人作品（原创 OC @上善无形，
   女仆版二次设计 @ZipZipPipe），以 **CC BY-NC-SA 4.0** 开放二次创作，
   **禁止商业使用、需署名、衍生须相同方式共享**。提交涉及形象的素材前请先确认授权。
