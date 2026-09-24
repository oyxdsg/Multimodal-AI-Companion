"""DeepSeek 网页版客户端（chat.deepseek.com 非官方接口）。

绕过官方付费 API，直接使用 DeepSeek 网页版的内部接口：
- PoW 工作量证明（DeepSeekHashV1，WASM 求解）
- 会话创建 + parent_message_id 多轮记忆
- 非流式获取完整回复（整个 SSE body 返回后解析）
"""

import base64
import json
import queue
import random
import struct
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path

from wasmtime import Store, Module, Instance
from curl_cffi import requests as cffi_requests

from ai.providers import ProviderError as _ProviderError

try:  # curl_cffi 的 multipart 支持（上传文件必需，requests 的 files= 不被支持）
    from curl_cffi import CurlMime
except ImportError:  # pragma: no cover - 旧版本兜底
    CurlMime = None

BASE_URL = "https://chat.deepseek.com"
# wasm 与客户端同目录（插件自带）
_WASM_PATH = Path(__file__).resolve().parent / "sha3_wasm_bg.wasm"

# 与网页端保持一致的客户端版本（实测浏览器为 2.4.0；过低可能被判定为旧客户端）
CLIENT_VERSION = "2.4.0"
# 端点路径（V4.1 实测未变，集中在此便于日后接口变动时统一修改）
COMPLETION_PATH = "/api/v0/chat/completion"
SESSION_CREATE_PATH = "/api/v0/chat_session/create"
POW_CHALLENGE_PATH = "/api/v0/chat/create_pow_challenge"
FILE_UPLOAD_PATH = "/api/v0/file/upload_file"

# 上传图片的长边上限（超过则等比缩小，控制 token 与带宽；单图约 ≤384 token）
IMAGE_MAX_SIDE = 1568
IMAGE_JPEG_QUALITY = 88

# SSE fragment 类型 → 归属（未列出的类型按 "other" 丢弃，避免混入正文）
_FRAGMENT_TYPES = {"RESPONSE": "content", "THINK": "thinking"}

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_SEC_CH_UA = (
    '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'
)


class DeepSeekError(_ProviderError):
    """基础异常，message 面向用户展示。"""


class TokenInvalidError(DeepSeekError):
    pass


class RateLimitError(DeepSeekError):
    pass


class MutedError(DeepSeekError):
    pass


class EmptyResponseError(DeepSeekError):
    pass


class WAFError(DeepSeekError):
    pass


class _WASMSolver:
    """基于 wasmtime 的 PoW 求解器（线程安全）。"""

    def __init__(self, wasm_bytes):
        self._lock = threading.Lock()
        self.store = Store()
        module = Module(self.store.engine, wasm_bytes)
        instance = Instance(self.store, module, [])
        exports = instance.exports(self.store)
        self.memory = exports["memory"]
        self.wasm_solve = exports["wasm_solve"]
        self.add_to_stack = exports["__wbindgen_add_to_stack_pointer"]
        self.malloc = exports["__wbindgen_export_0"]
        self._free = exports["__wbindgen_export_2"]
        self._allocations = []

    def _encode(self, s):
        data = s.encode("utf-8")
        ptr = self.malloc(self.store, len(data), 1)
        mem = self.memory.data_ptr(self.store)
        for i, b in enumerate(data):
            mem[ptr + i] = b
        self._allocations.append((ptr, len(data)))
        return ptr, len(data)

    def _free_allocations(self):
        for ptr, length in self._allocations:
            try:
                self._free(self.store, ptr, length, 1)
            except Exception:
                pass
        self._allocations.clear()

    def solve(self, challenge, salt, expire_at, difficulty):
        with self._lock:
            try:
                prefix = f"{salt}_{expire_at}_"
                stack_ptr = self.add_to_stack(self.store, -16)
                chal_ptr, chal_len = self._encode(challenge)
                prefix_ptr, prefix_len = self._encode(prefix)
                self.wasm_solve(self.store, stack_ptr, chal_ptr, chal_len,
                                prefix_ptr, prefix_len, float(difficulty))
                mem = self.memory.data_ptr(self.store)
                ret = int.from_bytes(bytes(mem[stack_ptr:stack_ptr + 4]),
                                     byteorder="little", signed=True)
                if ret == 0:
                    raise DeepSeekError("PoW 求解失败，请重试")
                result = struct.unpack("<d", bytes(mem[stack_ptr + 8:stack_ptr + 16]))[0]
                self.add_to_stack(self.store, 16)
                return int(result)
            finally:
                self._free_allocations()


def _parse_cookies(cookie_str):
    """把浏览器复制的 Cookie 字符串解析为 dict。"""
    out = {}
    if not cookie_str:
        return out
    jar = SimpleCookie()
    try:
        jar.load(cookie_str)
    except Exception:
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                k, _, v = part.partition("=")
                out[k.strip()] = v.strip()
        return out
    for morsel in jar.values():
        out[morsel.key] = morsel.value
    return out


def encode_image_for_upload(path, max_side=IMAGE_MAX_SIDE,
                            quality=IMAGE_JPEG_QUALITY):
    """读取本地图片并按需等比缩小，返回 (bytes, filename, content_type)。

    用 QImage 解码（PySide6 自带，避免额外依赖），QImage 可在工作线程使用。
    有透明通道的图保存为 PNG，否则用 JPEG（体积更小）。
    """
    from PySide6.QtCore import QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImage

    img = QImage(str(path))
    if img.isNull():
        raise DeepSeekError(f"无法读取图片：{Path(str(path)).name}")
    if max(img.width(), img.height()) > max_side:
        img = img.scaled(max_side, max_side, Qt.KeepAspectRatio,
                         Qt.SmoothTransformation)
    if img.hasAlphaChannel():
        fmt, ext, ctype = "PNG", "png", "image/png"
    else:
        fmt, ext, ctype = "JPEG", "jpg", "image/jpeg"
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    try:
        if fmt == "JPEG":
            img.save(buf, "JPEG", quality)
        else:
            img.save(buf, "PNG")
        data = bytes(buf.data())
    finally:
        buf.close()
    if not data:
        raise DeepSeekError("图片编码失败")
    return data, f"upload.{ext}", ctype


# ---- PoW 预取：提前解好「未使用」的挑战答案，请求时零等待 ----
# 实测 DeepSeek 拒绝「复用已使用过」的答案，但「未使用」的预取答案有效。
# 队列保证每个答案只被取用一次，后台线程持续补充。
_POW_Q = queue.Queue(maxsize=2)    # 待用的 X-DS-PoW-Response 值
_POW_REFILLING = threading.Lock()  # 防止并发补充


def _refill_pow():
    """队列不足时，后台补一个未使用的 PoW 答案。"""
    if _POW_Q.qsize() >= 1:
        return
    if not _POW_REFILLING.acquire(blocking=False):
        return
    threading.Thread(target=_prefetch_one, daemon=True).start()


def _prefetch_one():
    """预取线程：用独立 client 拿挑战并求解，答案放入队列。

    用后即弃的临时 client，避免全局持有 wasm solver 导致退出时析构警告。
    """
    try:
        from ai.credentials import load_credentials
        token, cookies = load_credentials()
        if not token:
            return
        pc = DeepSeekClient(token, cookies)
        c = pc._get_challenge()
        resp = pc._solve_pow(c)
        _POW_Q.put(resp, timeout=5)
    except Exception:
        pass
    finally:
        _POW_REFILLING.release()


class DeepSeekClient:
    """DeepSeek 网页版聊天客户端。

    用法：
        client = DeepSeekClient(token, cookies)
        client.verify()                 # 测试凭证是否有效
        content = client.chat("你好")    # 多轮：自动维护会话与 parent 链
        client.reset_thread()            # 开启新会话（清空记忆）
    """

    def __init__(self, token, cookies=""):
        self.token = self._normalize_token(token)
        self.cookie_dict = _parse_cookies(cookies)
        self._solver = None
        self.session_id = None
        self.parent_message_id = None
        self._system_prompt = None
        self._session = cffi_requests.Session(
            impersonate="chrome131", timeout=180,
        )
        self._apply_cookies()

    # ---------- 凭证 ----------

    @staticmethod
    def _normalize_token(token):
        s = (token or "").strip()
        if s.startswith("Bearer "):
            s = s[len("Bearer "):].strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                obj = json.loads(s)
                if isinstance(obj, dict) and isinstance(obj.get("value"), str):
                    return obj["value"]
            except (ValueError, TypeError):
                pass
        return s

    def _apply_cookies(self):
        host = "chat.deepseek.com"
        for k, v in self.cookie_dict.items():
            self._session.cookies.set(k, v, domain=host)

    @property
    def solver(self):
        if self._solver is None:
            with open(_WASM_PATH, "rb") as f:
                self._solver = _WASMSolver(f.read())
        return self._solver

    # ---------- PoW ----------

    def _base_headers(self):
        return {
            "User-Agent": DEFAULT_UA,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/",
            "Priority": "u=1, i",
            "Sec-Ch-Ua": DEFAULT_SEC_CH_UA,
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Client-Version": CLIENT_VERSION,
            "X-Client-Platform": "web",
            "X-Client-Locale": "zh_CN",
            "X-Client-Timezone-Offset": "28800",
            "x-client-bundle-id": "com.deepseek.chat",
        }

    @staticmethod
    def _detect_waf(status, headers):
        h = {k.lower(): v for k, v in headers.items()}
        waf = h.get("x-amzn-waf-action", "").lower()
        cf = h.get("cf-mitigated", "").lower()
        if status == 405 and waf == "captcha":
            return "AWS WAF 人机验证"
        if status == 202 and waf == "challenge":
            return "AWS WAF 挑战"
        if status in (403, 429) and cf == "challenge":
            return "Cloudflare 人机验证"
        return None

    def _check_resp(self, resp):
        kind = self._detect_waf(resp.status_code, resp.headers)
        if kind:
            raise WAFError(f"被{kind}拦截，请稍后重试")
        if resp.status_code in (401, 403):
            raise TokenInvalidError("登录已失效，请重新登录")
        resp.raise_for_status()

    def _get_challenge(self, target_path=COMPLETION_PATH):
        resp = self._session.post(
            f"{BASE_URL}{POW_CHALLENGE_PATH}",
            json={"target_path": target_path},
            headers=self._base_headers(),
        )
        self._check_resp(resp)
        data = resp.json()
        try:
            return data["data"]["biz_data"]["challenge"]
        except (KeyError, TypeError) as e:
            raise DeepSeekError(
                f"获取挑战失败：{data.get('code', 'unknown')} - {data.get('msg', str(e))}"
            )

    def _solve_pow(self, challenge_data):
        nonce = self.solver.solve(
            challenge=challenge_data["challenge"],
            salt=challenge_data["salt"],
            expire_at=challenge_data["expire_at"],
            difficulty=challenge_data["difficulty"],
        )
        raw = json.dumps({
            "algorithm": "DeepSeekHashV1",
            "challenge": challenge_data["challenge"],
            "salt": challenge_data["salt"],
            "answer": nonce,
            "signature": challenge_data["signature"],
            "target_path": challenge_data["target_path"],
        }, separators=(",", ":"))
        return base64.b64encode(raw.encode()).decode()

    def _pow_headers(self):
        # 优先取预取的未使用答案（零等待）；队列空则现场求解并触发后台补充
        resp = None
        try:
            resp = _POW_Q.get_nowait()
        except queue.Empty:
            pass
        headers = self._base_headers()
        if resp is not None:
            headers["X-DS-PoW-Response"] = resp
        else:
            time.sleep(random.uniform(0, 0.4))
            c = self._get_challenge()
            headers["X-DS-PoW-Response"] = self._solve_pow(c)
        _refill_pow()
        return headers

    # ---------- 会话与聊天 ----------

    def create_session(self):
        headers = self._pow_headers()
        resp = self._session.post(
            f"{BASE_URL}{SESSION_CREATE_PATH}",
            json={"target_path": COMPLETION_PATH},
            headers=headers,
        )
        self._check_resp(resp)
        data = resp.json()
        if data.get("code") != 0:
            raise DeepSeekError(f"创建会话失败：{data.get('msg') or data.get('code')}")
        biz = data["data"]["biz_data"]
        if "id" in biz:
            return biz["id"]
        return biz["chat_session"]["id"]

    def verify(self):
        """验证凭证是否有效（创建一个会话即成功）。"""
        self.reset_thread()
        return True

    def reset_thread(self):
        """重置会话：开启新会话并清空 parent 链（即清空历史记忆）。"""
        self.session_id = self.create_session()
        self.parent_message_id = None

    # ---------- 图片上传（V4.1 识图） ----------

    def upload_image_file(self, path):
        """把本地图片上传为 DeepSeek file_id（供 chat 的 ref_file_ids 引用）。

        实测（2026-09-10）：只需一次 multipart 上传即可，无需 fork_file_task；
        但 PoW 挑战的 target_path 必须是 /api/v0/file/upload_file，否则
        返回 40301 INVALID_POW_RESPONSE。
        """
        data, filename, ctype = encode_image_for_upload(path)
        return self.upload_image_bytes(data, filename, ctype)

    def upload_image_bytes(self, data, filename="upload.png",
                           content_type="image/png"):
        """上传图片字节，返回 file_id。"""
        if CurlMime is None:
            raise DeepSeekError("当前 curl_cffi 版本不支持文件上传，请升级 curl_cffi")
        headers = self._base_headers()
        headers.pop("Content-Type", None)   # multipart 会自带 boundary
        headers["X-DS-PoW-Response"] = self._solve_pow(
            self._get_challenge(FILE_UPLOAD_PATH))
        mime = CurlMime()
        mime.addpart(name="file", content_type=content_type,
                     filename=filename, data=data)
        try:
            resp = self._session.post(
                f"{BASE_URL}{FILE_UPLOAD_PATH}", multipart=mime, headers=headers)
        finally:
            mime.close()
        self._check_resp(resp)
        try:
            payload = resp.json()
        except ValueError:
            raise DeepSeekError("图片上传返回异常，请重试")
        if payload.get("code") != 0:
            raise DeepSeekError(
                f"图片上传失败：{payload.get('msg') or payload.get('code')}")
        biz = (payload.get("data") or {}).get("biz_data") or {}
        file_id = biz.get("id")
        if not file_id:
            raise DeepSeekError("图片上传失败：未获取到文件 ID")
        return file_id

    def _parse_sse(self, text):
        events = []
        current = ""
        for line in text.split("\n"):
            if line.startswith("event: "):
                current = line[7:]
            elif line.startswith("data: "):
                data_str = line[6:]
                if data_str:
                    try:
                        events.append((current, json.loads(data_str)))
                    except json.JSONDecodeError:
                        events.append((current, data_str))
                current = ""
        return events

    def _extract_content(self, events):
        """从 SSE 事件里提取最终文本与 reasoning。

        V4.1 起可能新增 fragment 类型（如搜索结果、工具调用）；未知类型一律
        归入 "other" 并丢弃，避免混进正文气泡。
        """
        content_parts = []
        thinking_parts = []
        frag_type = None
        message_id = None

        def _append(value):
            if not value:
                return
            if frag_type == "thinking":
                thinking_parts.append(value)
            elif frag_type == "other":
                return
            else:  # content / 未标记
                content_parts.append(value)

        for event_type, data in events:
            if not isinstance(data, dict):
                continue
            if event_type == "ready" and isinstance(data.get("response_message_id"), int):
                message_id = data["response_message_id"]
            p = data.get("p", "")
            o = data.get("o", "")
            v = data.get("v", "")

            if isinstance(v, dict) and "response" in v:
                r = v["response"]
                if isinstance(r.get("message_id"), int):
                    message_id = r["message_id"]
                fragments = r.get("fragments", [])
                if fragments:
                    ft = fragments[0].get("type", "")
                    frag_type = _FRAGMENT_TYPES.get(ft, "other")
                    _append(fragments[0].get("content", ""))
                continue

            if p == "response/fragments/-1/content" and o in ("APPEND", ""):
                _append(v)
                continue

            if p == "response/fragments" and o == "APPEND":
                if isinstance(v, list) and v:
                    frag_type = _FRAGMENT_TYPES.get(v[0].get("type", ""), "other")
                continue

            if p == "response/content" and o == "APPEND":
                _append(v)
                continue

            if "v" in data and "p" not in data and "o" not in data:
                token = data["v"]
                if isinstance(token, str):
                    _append(token)
                continue

        content = "".join(content_parts)
        thinking = "".join(thinking_parts)
        if message_id is not None:
            self.parent_message_id = message_id
        return content, thinking

    @staticmethod
    def _toast_or_hint_error(events):
        for event_type, data in events:
            if event_type not in ("toast", "hint") or not isinstance(data, dict):
                continue
            if str(data.get("type", "")).lower() != "error":
                continue
            content = data.get("content") or data.get("msg") or ""
            reason = data.get("finish_reason") or ""
            return content, reason
        return None

    def _is_muted(self, raw):
        if not isinstance(raw, dict):
            return None
        d = raw.get("data")
        if not isinstance(d, dict):
            return None
        biz_msg = str(d.get("biz_msg") or "")
        biz_code = d.get("biz_code")
        if biz_code == 5 or "mute" in biz_msg.lower():
            until = ""
            bd = d.get("biz_data")
            if isinstance(bd, dict) and bd.get("mute_until"):
                try:
                    until = "，解封时间 " + time.strftime(
                        "%Y-%m-%d %H:%M", time.localtime(float(bd["mute_until"]))
                    )
                except (TypeError, ValueError, OSError, OverflowError):
                    until = ""
            return f"DeepSeek 账号已被限制发言{until}"
        return None

    def chat(self, prompt, system_prompt=None, model_type="default",
             thinking_enabled=False, search_enabled=False, memory=True,
             ref_file_ids=None):
        """非流式聊天，返回 (content, thinking)。

        首次调用自动创建会话并把 system_prompt 作为首条消息注入；
        之后通过 parent_message_id 串接上下文实现多轮记忆。
        memory=False 时每次开启全新会话（无上下文）。
        若会话已失效（如重启后恢复的旧会话），自动重开会话重试一次。
        ref_file_ids：已上传图片的 file_id 列表（见 upload_image_file）。
        """
        if not memory:
            self.reset_thread()
        if self.session_id is None:
            self.reset_thread()
        try:
            return self._chat_once(prompt, system_prompt, model_type,
                                   thinking_enabled, search_enabled,
                                   ref_file_ids)
        except EmptyResponseError:
            # 持久化的会话可能已失效，重开会话重试一次
            self.reset_thread()
            return self._chat_once(prompt, system_prompt, model_type,
                                   thinking_enabled, search_enabled,
                                   ref_file_ids)

    def _chat_once(self, prompt, system_prompt, model_type,
                   thinking_enabled, search_enabled, ref_file_ids=None):
        if self.parent_message_id is None and system_prompt:
            prompt = f"{system_prompt}\n\n{prompt}"

        body = {
            "chat_session_id": self.session_id,
            "parent_message_id": self.parent_message_id,
            "model_type": model_type,
            "prompt": prompt,
            "ref_file_ids": list(ref_file_ids or []),
            "stream": False,
            "thinking_enabled": thinking_enabled,
            "search_enabled": search_enabled,
            "action": None,
            "preempt": False,
        }
        headers = self._pow_headers()
        resp = self._session.post(
            f"{BASE_URL}{COMPLETION_PATH}",
            json=body,
            headers=headers,
        )
        if resp.status_code == 422:
            # 恢复的旧会话已失效（如跨重启后会话过期/被替换）：
            # 由 chat() 捕获 EmptyResponseError 自动重开会话重试一次
            raise EmptyResponseError("会话已失效，正在重开新会话")
        self._check_resp(resp)

        text = resp.text
        if not text or not text.strip():
            raise EmptyResponseError("DeepSeek 返回了空响应，请重试")

        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            raw = None
        mute = self._is_muted(raw)
        if mute:
            raise MutedError(mute)

        events = self._parse_sse(text)
        err = self._toast_or_hint_error(events)
        if err:
            content, reason = err
            if reason == "rate_limit_reached" or "频繁" in (content or ""):
                raise RateLimitError("消息发送过于频繁，请稍后再试")
            raise DeepSeekError(f"DeepSeek 提示：{content or '请求被拒绝'}")

        content, thinking = self._extract_content(events)
        if not content and not thinking:
            raise EmptyResponseError("DeepSeek 返回了空内容，请重试")
        return content, thinking