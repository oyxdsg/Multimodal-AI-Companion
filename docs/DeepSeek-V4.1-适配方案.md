# DeepSeek V4.1 适配方案（v4 · 已实施完成）

> 状态：**已实施并验证通过**，随 `desktop-pet` **2.17.0** 发布（本地提交 `db5ac85`）。
> 实施前基线提交 `59b81d5`（积压的分包重构 / 素材 / 模组改动）。
> 侦察脚本位于 `.workbuddy/recon/`（开发用，不进产品、不纳入仓库）。
> 核心结论：**V4.1 没有破坏协议层**，现有客户端原样可用；实际要做的是「删快速/专家 + 新增图片上传」，且图片上传链路比最初预想**简单**（2 步，无需 fork / HIF）。

---

## 0. 实施记录（2.17.0）

**改动文件（8 个）**

| 文件 | 改动 |
|---|---|
| `ai/deepseek.py` | `CLIENT_VERSION` 2.3.0→**2.4.0**；端点抽常量（`COMPLETION_PATH`/`SESSION_CREATE_PATH`/`POW_CHALLENGE_PATH`/`FILE_UPLOAD_PATH`）；新增 `encode_image_for_upload()`、`upload_image_file()`、`upload_image_bytes()`；`chat()/_chat_once()` 支持 `ref_file_ids` 且请求体补 `action: null`；`_extract_content()` 改类型表分发、未知 fragment 丢弃 |
| `ai/chat.py` | 聊天窗「📎」选图入口 + 待发送缩略图条（上限 4、可单独移除）；worker 先上传再提问；只发图不打字自动补默认提问；千问后端提示并忽略图片 |
| `pet/settings_dialog.py` | 删除「快速/专家模式」下拉；`model` 固定 `config.AI_MODEL_TYPE`；清理 `ai_model_type` 键；「深度思考」文案更新 |
| `config.py` | 新增 `IMAGE_EXTENSIONS` / `IMAGE_MAX_FILES` / `IMAGE_PROMPT_HINT` |
| `core/icons.py` | 新增 `paperclip` / `image` 图标 |
| `tests/test_reconstruction.py` | 新增 4 个用例（SSE 容错、上传链路与请求体 mock、图片编码、模式项已删） |
| `README.md` / `CHANGELOG.md` | 版本 2.17.0、识图用法、设置表更新 |

**验证结果**

| 项 | 结果 |
|---|---|
| 回归测试（新增 4 例） | ✅ 全部通过 |
| `python main.py --smoke` | ✅ exit=0（`crash.log` 未增长） |
| 离屏构建聊天窗 / 设置对话框 | ✅ 正常，`model_combo` 已不存在 |
| 真实接口端到端 | ✅ 上传纯蓝 PNG → 问颜色 → 模型答「蓝色」 |

**与 v3 预判的差异（实测修正）**
1. 侦察结论**比预期更好**：`model_type` 的 `default` 与 `expert` 服务端**都接受**，三模式合并确认为**纯前端改动**——删「快速/专家」是产品/UX 决策，而非技术必须。
2. `X-Client-Version` **2.3.0 仍可用**，2.4.0 属"面向未来"的对齐，非必需。
3. 图片上传**只需 2 步**，实测**未调用也无需** `fork_file_task`；`hif-leim` / `hif-dliq` / `files.deepseeksvc.com` 仅浏览器端缩略图预览用，可跳过。
4. 唯一硬性坑：上传的 PoW 挑战 `target_path` 必须 = `/api/v0/file/upload_file`（指向 completion → `40301 INVALID_POW_RESPONSE`）。

**未做（按你的决定）**
- 千问后端**未改**，暂不支持传图（选图会提示并忽略）。
- 图片入口**只做「选择文件」**，未做粘贴 / 拖拽 / 右键。

**环境备注（复现侦察时）**
- 项目依赖装在系统 Python；本机 bash 中 `APPDATA` / `PROCESSOR_ARCHITECTURE` 为空，跑侦察脚本需显式设置，否则 wasmtime 报 `unsupported architecture`、playwright 会落到错误 profile。

---

## 1. 侦察结论（实测）

| # | 事项 | 旧假设 | **实测结果** | 影响 |
|---|---|---|---|---|
| 1 | `model_type` | 专家模式取消 → `expert` 可能被拒 | **`default` 与 `expert` 都返回 200**，服务端原样回显 | ✅ **无破坏**。删「快速/专家」是产品/UX 决策，**不是技术必须** |
| 2 | `X-Client-Version` | 2.3.0 可能被拒 | **2.3.0 仍被接受**；浏览器实际发送 **2.4.0** | ⚠️ 建议升到 2.4.0（面向未来，非紧急） |
| 3 | SSE 结构 | 可能新增 fragment 类型 | **完全未变**（`event: ready` / `fragments[].type=RESPONSE` / `p,o,v`） | ✅ 无需改解析（仍建议加容错） |
| 4 | `thinking_enabled` / `search_enabled` | 合并后是否还在 | **仍在，仍有效**（浏览器默认发 `true`） | ✅ 保留（符合你的决策） |
| 5 | 端点路径 `/api/v0/*` | 可能 bump | **未变**（chat_session/create、chat/completion、create_pow_challenge 均 200） | ✅ 无需改 |
| 6 | PoW | 可能换算法 | 未变（`DeepSeekHashV1` + wasm 正常） | ✅ 无需改 |
| 7 | 登录键名 | 可能改 | **未变**：`localStorage.userToken`（本次长度 92）、Cookie `usertoken` | ✅ 无需改 |
| 8 | 多轮会话 | — | 未变（`chat_session_id` + `parent_message_id`） | ✅ 无需改 |
| 9 | **图片上传** | 需 upload → fork → wait → HIF | **只需 2 步**：`upload_file` → `ref_file_ids`（**无需 fork_file_task / HIF**） | ⭐ 见第 2 节，已跑通 |
| 10 | completion 请求体 | — | 浏览器新增字段 **`action: null`**，且**不带 `stream`**；我们带 `stream:false` 也正常 | 可选项 |

> 一句话：**三模式合并是纯前端改动，协议层没动。** 你升级到 V4.1 后桌宠不会坏。

---

## 2. 图片上传：实测链路（已端到端跑通 ✅）

### 2.1 真实流程（只需 2 步）

```
① POST https://chat.deepseek.com/api/v0/file/upload_file
     - multipart/form-data，字段名 "file"（"upload_file" 也可）
     - 请求头：常规头 + Authorization + X-DS-PoW-Response
       ★ 该 PoW 挑战的 target_path 必须 = "/api/v0/file/upload_file"
     - 去掉 Content-Type: application/json（由 multipart 自行设置 boundary）
     返回：data.biz_data.id = "file-xxxx"（is_image=true, model_kind="VISION"）

② POST https://chat.deepseek.com/api/v0/chat/completion
     - body 里 "ref_file_ids": ["file-xxxx"]，其余同普通聊天
     返回：正常 SSE，模型已"看到"图片
```

### 2.2 关键坑（已用矩阵实验钉死）

| 变量 | 结论 |
|---|---|
| **PoW 的 `target_path`** | **决定性因素**。指向 `/api/v0/file/upload_file` → 成功；指向 `/api/v0/chat/completion` → `40301 INVALID_POW_RESPONSE` |
| 客户端版本 2.3.0 / 2.4.0 | 都成功，**不影响**上传 |
| multipart 字段名 `file` / `upload_file` | 都成功，**不影响** |
| `fork_file_task` | **实测未被调用，也不需要** |
| `hif-leim` / `hif-dliq` / `files.deepseeksvc.com` | 浏览器会调，但**仅用于界面里显示图片缩略图预览**；模型识图**不需要**，可跳过 |
| `B` = base64 直传 | 网页内部接口**不支持**；必须走 upload_file 拿 id |

### 2.3 端到端验证
上传一张纯蓝色 PNG → 提问"这张图主要是什么颜色？" → 模型回答 **"蓝色"** ✅

### 2.4 实现要点（落代码时）
- `ai/deepseek.py` 新增 `upload_image(path)`：`_get_challenge("/api/v0/file/upload_file")` → `_solve_pow()` → `CurlMime` 上传 → 取 `biz_data.id`。
- **注意 curl_cffi 不支持 `files=`**，必须用 `from curl_cffi import CurlMime`。
- `_chat_once()` 增加 `ref_file_ids` 透传。
- 上传前压缩图片（单图约 ≤384 token，省钱省时）；限制格式 JPEG/PNG/GIF/WebP。
- 上传失败即回退纯文本问答，不影响聊天。

---

## 3. 按你的决策落地

| 决策 | 落地动作 | 说明 |
|---|---|---|
| **双后端保留** | 不动 `ai/client.py` 的后端切换与降级 | 网页接口若再变，降级千问 |
| **删除「快速/专家」** | 删 `settings_dialog.py` 的 `model_combo` 与 `ai_model_type` 存储；`model_type` 固定 `"default"` | 实测非必需，但产品语义已无模式之分 |
| **保留深度思考/智能搜索** | 保留两个开关，继续透传 `thinking_enabled` / `search_enabled` | 实测仍有效 |
| **新增图片上传** | 按第 2 节实现 | 入口只做**一个「📎 选择文件」**（你的决定） |
| 千问 | **不动** | 本轮不涉及 |

> 建议顺带：`X-Client-Version` 升到 `2.4.0`（与浏览器一致，降低未来被判定为"过旧客户端"的风险）。

---

## 4. 风险评估（个人使用 / 无高并发）

你的场景是**单人、单账号、无并发**，风险和"拿它做多账号代理"完全不是一个量级。分档看：

### 4.1 结论：**风险低，可接受**
| 维度 | 你的情况 | 风险 |
|---|---|---|
| 账号数量 | 1 个自己的账号 | 低（黑产风控主要打击多账号批量） |
| 请求频率 | 人工节奏（每次对话间隔数秒~数分钟），图片偶发 | 低 |
| 并发 | 无（桌面宠物天然串行，且项目本身有全局串行锁） | 低 |
| 指纹一致性 | 已用 `curl_cffi` 模拟 Chrome TLS 指纹；本次实测 200 正常 | 低 |
| 行为特征 | 与「网页版正常使用」高度接近 | 低 |

### 4.2 真正需要防的三件事（与并发无关）
1. **请求形态"不像"真实客户端** —— 例如 PoW 复用、缺头导致 `INVALID_POW_RESPONSE` / `MISSING_HEADER`。**这类"畸形请求"比正常请求更容易触发风控**。→ 对策：上传用对应 target_path 的 PoW；把 `X-Client-Version` 对齐浏览器（2.4.0）。
2. **突发批量** —— 例如一次灌很多图 / 脚本刷。→ 对策：保持人工节奏，图片按需上传、复用 `file_id`，不轮询刷接口。
3. **账号本身异常**（多设备登录、共享）。→ 对策：保持单账号自用。

### 4.3 兜底（已有）
- 项目已内置 `MutedError`（限流/禁言）与 WAF 检测，会给出明确文案，不会静默失败。
- **双后端 = 天然保险**：网页接口一旦异常，切千问即可，桌宠不至于哑掉。
- 建议（可选）：在设置里保留一个"一键切千问"的顺口提示，减少被封期的影响。

> 提醒：社区里"1 天 → 3 天 → 8 天 → 永久"的升级处罚，案例基本来自**多账号/共享代理**。个人自用按上述节奏使用，属于低风险区间；但**风险不为零**，请知悉。

---

## 5. 改动清单（最终）

| 文件 | 改动 |
|---|---|
| `ai/deepseek.py` | 新增 `upload_image()`（upload_file + 专用 PoW）；`_chat_once()` 支持 `ref_file_ids`；`X-Client-Version` → `2.4.0`；`_extract_content` 加未知类型容错；请求体补 `action: null`（可选） |
| `ai/client.py` | `chat_once()` 增加 `images=[...]` 参数；透传 |
| `ai/chat.py` | 聊天窗口加「📎 选择文件」按钮 + 待发送图片缩略图 |
| `pet/settings_dialog.py` | **删除**「快速模式/专家模式」下拉；保留深度思考/联网搜索 |
| `config.py` | 图片提示词片段、大小/格式常量；`model_type` 固定值 |
| `tests/test_reconstruction.py` | 上传返回解析、带图请求体组装、SSE 容错回归 |
| `README.md` / `CHANGELOG.md` | 说明与版本记录（2.17.0） |

---

## 6. 测试计划

- **单元**：上传返回 `biz_data.id` 解析；`ref_file_ids` 组装；SSE 未知 fragment 容错。
- **冒烟**：`python main.py --smoke`。
- **手工**：登录校验 / 多轮 / 三模式 / wiki 提炼 / **选图问答（含大图、非法格式、无网络）** / 设置项已无「快速/专家」/ 断网降级千问。
- **观测**：`logs/perf.log` 增加上传耗时项。

---

## 7. 回滚与遗留

- **回滚**：改动集中在 `ai/deepseek.py` + 聊天窗/设置项；打 tag 小步提交。
- **本次侦察副作用**：在你的 DeepSeek 账号上产生了约 10 个测试会话与数张测试图（`recon_test.png`/`blue.png`），可自行在网页端删除。
- **侦察脚本**：`.workbuddy/recon/`（probe_completion / probe_upload / probe_upload2 / probe_vision / capture_upload）。日后接口再变可直接重跑。

---

## 8. 决策与落实（已全部落地）

| 决策 | 你的选择 | 落实情况 |
|---|---|---|
| 后端走向 | 双后端（DeepSeek 网页 + 千问） | ✅ 未动后端切换；网页接口异常仍可降级千问 |
| 「快速/专家」设置项 | 删除 | ✅ 已删下拉，`model_type` 固定 `default`，清理旧键 |
| 深度思考 / 联网搜索 | 保留 | ✅ 两个开关保留并继续透传 |
| 图片上传 | 做，且只做「选择文件」入口 | ✅ 聊天窗「📎」按钮 + 缩略图预览 |
| 千问后端 | 暂不改 | ✅ 未改（选图时提示并忽略） |
| 侦察 | 由我执行 | ✅ 已完成并端到端验证（脚本留在 `.workbuddy/recon/`） |
| 客户端版本 | 升到 2.4.0 | ✅ 已升 |

**后续可选**：给千问后端也接传图；图片入口扩展粘贴/拖拽；把 `.workbuddy/recon/` 的侦察脚本整理成可复跑的接口自检工具。
