# deepseek-web —— DeepSeek 网页版后端插件

`ai-provider` 扩展点的实现：走 `chat.deepseek.com` 的**网页内部接口**（不是官方 SDK），
用你自己的网页账号，**免费**，并独占提供两项能力：

| 能力 | 说明 |
|---|---|
| **服务端记忆** | 会话与人格存在服务端，客户端只发当轮（与官方 API 的"客户端组装 + 滑窗提炼"是两种上下文策略） |
| **图片输入** | 本插件是目前**唯一支持传图**的后端；其余后端模型虽支持图片，但图像输入尚未实现，界面刻意不显示 `[识图]` |

此外还带联网搜索、PoW（工作量证明）预取。

## 依赖

宿主**不会代装**依赖，按需自行安装：

```bash
pip install -r requirements-web.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

| 包 | 用途 |
|---|---|
| `wasmtime` | 执行 PoW 所需的 wasm（`sha3_wasm_bg.wasm`） |
| `curl_cffi` | 带浏览器指纹的 HTTP（主依赖里已有） |
| `playwright` | 自动登录驱动系统 Edge（可选，手动粘 Token 也能用） |

缺依赖时插件加载失败、宿主照常启动，`python main.py --doctor` 会给出补装指引。

## 使用

1. 装好依赖 → 重启桌宠。
2. 设置 →「基础」→「AI 后端」选 **DeepSeek（网页版）**。
3. 右键宠物 →「登录 DeepSeek」：自动驱动系统 Edge 完成登录并把凭证持久化，之后静默复用；
   自动登录不可用时可手动粘贴 Token / Cookies。

登录调试日志：`logs/login_debug.log`。

## ⚠️ 声明

- 本插件是**网页端内部接口的逆向实现**，**不是** DeepSeek 官方 SDK，与官方无关。
- 使用它意味着你的请求走你自己的网页账号，**请自行确认并遵守服务方的用户协议与使用条款**；
  账号安全、请求频率、可用性风险由使用者自行承担。
- 网页端改版可能导致接口失配 —— 这也是它被做成**可选插件**（而非宿主内置）的原因之一。
- **优先推荐**官方 API（`deepseek-api`）或千问：接口稳定、有明确条款；本插件适合"不想额外付费、
  且愿意接受失配风险"的场景。

## 结构

```
deepseek-web/
├── plugin.json              # 清单（type: ai-provider；requires: wasmtime/curl_cffi/playwright）
├── plugin.py                # register(host)：注册供应商 profile + 客户端工厂
├── deepseek_client.py       # 网页接口客户端（PoW / 会话 / SSE / 上传）
├── login.py                 # playwright 自动登录
├── sha3_wasm_bg.wasm        # PoW 用的 wasm
└── tests/test_deepseek.py   # 离线测试（SSE 解析 / 上传 / 编码）
```

凭证读写沿用宿主的 `ai/credentials.py`（QSettings），**不在插件里另存一份**。

## 测试

```bash
python desktop-pet/plugins/deepseek-web/tests/test_deepseek.py
```
