# 插件投放目录

把插件放进本目录（每个插件一个子文件夹），重启桌宠即生效。

```
plugins/
├── wiki-local/          # 本地 Wiki 知识插件（随仓库发布，数据需自建）
│   └── plugin.json
├── deepseek-web/        # DeepSeek 网页版后端插件（随仓库发布，需装 wasmtime 等）
│   └── plugin.json
└── …
```

## 入库策略（单仓库）

| 内容 | 是否入库 | 说明 |
|---|---|---|
| 插件**代码**（`plugin.json` / `plugin.py` / 模块 / wasm） | ✅ 入库 | 随主仓库发布 |
| 插件**数据**（`data/`、Wiki 库、向量索引、模型） | ❌ 不入库 | 体积与派生数据许可原因，由插件自带脚本重建 |
| `.venv/`、缓存 | ❌ 不入库 | 见根 `.gitignore` |

> 因此**全新 clone 下来插件会缺数据**：此时插件应优雅降级（如 Wiki 插件回「不注入」），
> 而不是报错影响宿主；按插件 README 跑一遍建库命令即可补齐。

* 发布版还会扫描 `%LOCALAPPDATA%\DesktopPet\plugins\`，两个位置等价。
* 也可以把插件做成 pip 包（`entry_points` 组 `deskpet.plugins`），`pip install` 后自动发现。
* 用 `python main.py --doctor` 查看已装插件与各扩展点状态。

插件接口、清单字段、示例与命名约定见 [`../docs/PLUGIN_API.md`](../docs/PLUGIN_API.md)。

> ⚠️ 插件是**可执行代码**，会在桌宠主进程内运行。请只安装可信来源的插件。
