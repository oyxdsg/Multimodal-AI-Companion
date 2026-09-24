# 示例插件（复制即可用）

一个**最小可运行**的桌宠插件骨架，演示两件事：

* 两个「知识」扩展点 —— `wiki-knowledge`（游戏事件驱动）与 `knowledge-qa`（用户提问驱动）
* `host.llm()` —— 插件需要模型能力时如何**向宿主借**，而不是自己读凭据 / `import ai.*`

契约全文见 [`../PLUGIN_API.md`](../PLUGIN_API.md)。

## 跑起来

```bash
# 1. 复制到插件目录（目录名 = plugin.json 里的 id）
cp -r desktop-pet/docs/example-plugin desktop-pet/plugins/my-plugin

# 2. 改 id / name（目录名与 id 保持一致）
#    desktop-pet/plugins/my-plugin/plugin.json

# 3. 确认被发现、被注册
python desktop-pet/main.py --doctor

# 4. 重启桌宠生效（设置 →「扩展 → 插件」可随时启停）
python desktop-pet/main.py
```

`--doctor` 里应当能看到这个插件，并且对应扩展点的「最终实现」指向它。没出现就看
`desktop-pet/logs/plugin.log`（加载失败原因都写在那儿）。

## 文件

| 文件 | 作用 |
|---|---|
| `plugin.json` | 清单：`id` / `type` / `entry` / `priority` … |
| `plugin.py` | 入口 `register(host)` + 两个实现类 |

## 改的时候注意

| 事项 | 说明 |
|---|---|
| 只依赖公共契约 | 只能 `import` `plugin.api` / `plugin.contracts`；**不要**碰 `ai.*` / `pet.*` / `game.*` / `core.*` |
| 失败要自己兜底 | 拿不到数据就返回 `None` / `""`。宿主会兜异常，但那是最后一道网，不该依赖 |
| 别在 `register()` 里做重活 | 它在宿主启动路径上，耗时会给用户造成"卡启动" |
| 大依赖别塞进包 | `plugin.json.requires` 只声明、宿主不代装；重型依赖建议让用户按你的 README 单独装（参考 `agentic-rag`） |
| 多扩展点可共存 | 一个插件能同时注册 `wiki-knowledge` + `knowledge-qa` + `assets`，注意别互相打架 |

## 这个示例是"空实现"

两个方法都直接返回 `""`（= 不注入），所以装上它**不会**有任何可见行为变化 ——
这本身也演示了宿主的一等保证：**插件返回空 = 与没装插件完全一致**。

要看到效果，在方法里返回一段字符串即可，例如：

```python
class ExampleQA:
    def ask(self, question, *, session_key="", max_chars=1200):
        if "钻石" not in question:
            return ""
        return "钻石在 Y=-59 附近最常见（1.18+ 高度重做后）。"
```

之后在游戏模式里提问"钻石在哪挖"，这段文本会以 `[知识]` 块注入当轮 prompt，
由主对话 AI 用人设口吻组织成回答。

## 许可

示例代码随主仓库以 **MIT** 发布（见根 [`LICENSE`](../../../LICENSE)），随便改、随便抄。
