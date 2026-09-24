# -*- coding: utf-8 -*-
"""千问（百炼）语音合成目录：**模型清单与音色清单都从官方文档实时抓取**。

为什么不再硬编码
----------------
原来 `ai/qwen.py` 里写死 `TTS_MODEL = "qwen3-tts-flash"` 和两个音色
（`["Chelsie", "Cherry"]`），而百炼实际提供 **48 个音色**、多个 TTS 模型代数
（flash / instruct-flash / qwen-tts，各带日期快照）。硬编码的结果是：
用户只能用到其中 4%，而且文档新增音色后必须改代码。

数据来源（两级，都有实证）
--------------------------
1. **音色 + 模型**：官方文档
   https://help.aliyun.com/zh/model-studio/qwen-tts-voice-list
   —— **服务端渲染的真 `<table>`**（实测 768 个 `<td>`，不需要 JS），
   4 列：`voice参数 | 详情(音色名/描述) | 支持语种 | 支持模型`。
   文档里有两张同构表：**实时（realtime，走 WebSocket）** 与 **非实时（HTTP）**，
   本模块**只取非实时表**——实时表用同一套 API 端点会直接 400
   （实测：`current user api does not support http call`）。
2. **账户可用性**：`GET /compatible-mode/v1/models`（实测返回 256 个模型）。
   仅作参考存进快照，**不用它过滤音色模型** —— 因为该清单是"对话模型"清单，
   完整函数式 TTS 模型（`qwen-tts-latest`）实测可调用却不在其中，
   拿它当过滤器会误杀。

三级数据源（离线可用）
----------------------
``voice/voice_cache/tts_catalog.json``（运行时缓存，gitignored）
  → ``voice/tts_catalog.snapshot.json``（随包快照，生成工具
    ``tools/gen_tts_snapshot.py``）
  → 空目录（调用方自行回退到 ``ai/qwen.py`` 的兜底默认值）

**零新增依赖**：解析用标准库 ``html.parser``；网络用项目已有的 ``curl_cffi``
（惰性导入，导入本模块不需要网络也不需要 Qt）。
"""

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser

# ---------------------------------------------------------------- 常量

#: 官方音色文档（服务端渲染，可直接解析）
DOC_URL = "https://help.aliyun.com/zh/model-studio/qwen-tts-voice-list"
#: Omni（原生语音）音色文档：**按模型族**给表，默认音色也在这里
OMNI_DOC_URL = "https://help.aliyun.com/zh/model-studio/omni-voice-list"
#: 百炼 OpenAI 兼容模型清单（账户视角，仅作参考）
MODELS_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/models"

_HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE_DIR = os.path.join(_HERE, "voice_cache")
_CACHE_PATH = os.path.join(_CACHE_DIR, "tts_catalog.json")
_SNAPSHOT_PATH = os.path.join(_HERE, "tts_catalog.snapshot.json")

#: 缓存多久算过期（秒）。文档变动不频繁，7 天足够；用户也可手动刷新。
TTL_SECONDS = 7 * 24 * 3600
#: 抓取超时
FETCH_TIMEOUT = 30

#: 兜底默认（目录为空时用；这两个值实测可用）
FALLBACK_MODEL = "qwen3-tts-flash"
FALLBACK_VOICE = "Chelsie"

#: 模型 id 里出现这些片段的一律不要：实时走 WebSocket、vc/vd 需要额外建音色
_EXCLUDE_PARTS = ("realtime", "-vc-", "-vd-", "-vc", "-vd")

#: 模型 id 抽取。**点号与连字符必须能交替**（`qwen3.5-omni-flash`）——
#: 旧写法 `(?:-[a-z0-9]+)*(?:\.[a-z0-9]+)*` 会在点号处提前收尾，
#: 把 `qwen3.5-omni-flash` 截成 `qwen3.5`（TTS 文档里没有带点 id，所以一直没暴露）。
_MODEL_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])([a-z][a-z0-9]*(?:[.\-][a-z0-9]+)*)")


# ---------------------------------------------------------------- 数据结构

@dataclass(frozen=True)
class Voice:
    """一个音色。`models` 是文档声明的"这个音色能被哪些 TTS 模型使用"。"""

    id: str
    name: str = ""          # 音色名（千雪）
    desc: str = ""          # 描述（二次元虚拟女友（女性））
    langs: tuple = ()       # 支持语种
    models: tuple = ()      # 支持的 TTS 模型 id
    sample: str = ""        # 官方示例音频 URL（文档只给了少量音色）

    def label(self):
        """下拉框显示文本：`千雪（Chelsie）· 二次元虚拟女友（女性）`。"""
        parts = []
        if self.name and self.name != self.id:
            parts.append("%s（%s）" % (self.name, self.id))
        else:
            parts.append(self.id)
        if self.desc:
            parts.append("· " + self.desc)
        return " ".join(parts)

    def supports(self, model):
        """该音色是否被指定模型支持。没声明适配关系的音色一律视为不支持。"""
        if not model:
            return True
        return (not self.models) or (model in self.models)


def _voice_json(v):
    return {"id": v.id, "name": v.name, "desc": v.desc,
            "langs": list(v.langs), "models": list(v.models),
            "sample": v.sample}


def _voice_obj(d):
    return Voice(id=str(d.get("id") or ""),
                 name=str(d.get("name") or ""),
                 desc=str(d.get("desc") or ""),
                 langs=tuple(d.get("langs") or ()),
                 models=tuple(d.get("models") or ()),
                 sample=str(d.get("sample") or ""))


@dataclass(frozen=True)
class Catalog:
    """一次抓取的完整结果。"""

    fetched_at: float = 0.0          # unix 时间戳
    source: str = "empty"            # doc / cache / snapshot / empty
    models: tuple = ()               # 非实时 TTS 模型 id（有序）
    voices: tuple = ()               # Voice
    live_models: tuple = ()          # /models 的账户清单（参考用）
    doc_url: str = DOC_URL
    #: Omni 原生语音：按模型给的音色表（条目很少，线性扫描即可，保持 frozen 语义）
    omni: tuple = ()
    omni_url: str = OMNI_DOC_URL

    @property
    def fetched_at_text(self):
        if not self.fetched_at:
            return "未知"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.fetched_at))

    @property
    def age(self):
        return (time.time() - self.fetched_at) if self.fetched_at else 1e18

    def is_stale(self):
        return self.age > TTL_SECONDS

    def model_ids(self):
        return tuple(self.models)

    def voices_for(self, model=None):
        """按模型过滤音色；模型为空或音色没声明适配关系时不筛。"""
        if not model:
            return tuple(self.voices)
        return tuple(v for v in self.voices if v.supports(model))

    def voice(self, vid):
        for v in self.voices:
            if v.id == vid:
                return v
        return None

    def omni_for(self, model):
        """某模型的原生语音音色记录；没有返回 None（= 该模型不出音频）。

        存的是**节标题里的 token**（可能是具体 id，也可能是族名如 `qwen3.5-omni`），
        所以这里做"精确命中 or 族前缀命中"，并**取最具体的一个**——
        文档里既有 `qwen-omni-turbo` 小节（4 个音色），也有泛泛的 `qwen-omni` 词串
        （55 个），不按具体度排序就会把 3.5 的音色表错给 turbo。
        """
        mid = str(model or "").lower()
        best = None
        for ov in self.omni:
            tok = ov.model
            if mid == tok or mid.startswith(tok + "-"):
                if best is None or len(tok) > len(best.model):
                    best = ov
        return best

    def default_model(self):
        return self.models[0] if self.models else FALLBACK_MODEL

    def default_voice(self, model=None):
        """默认音色：优先 Chelsie（虚拟女友设定），否则该模型下第一个。"""
        pool = self.voices_for(model)
        for v in pool:
            if v.id == FALLBACK_VOICE:
                return v.id
        return pool[0].id if pool else FALLBACK_VOICE

    def to_json(self):
        return {
            "fetched_at": self.fetched_at,
            "source": self.source,
            "doc_url": self.doc_url,
            "omni_url": self.omni_url,
            "models": list(self.models),
            "live_models": list(self.live_models),
            "voices": [_voice_json(v) for v in self.voices],
            "omni": [
                {"model": ov.model, "default": ov.default,
                 "voices": [_voice_json(v) for v in ov.voices]}
                for ov in self.omni
            ],
        }

    @classmethod
    def from_json(cls, data, source="cache"):
        return cls(
            fetched_at=float(data.get("fetched_at") or 0),
            source=source,
            models=tuple(data.get("models") or ()),
            voices=tuple(_voice_obj(d) for d in (data.get("voices") or ())
                         if d.get("id")),
            live_models=tuple(data.get("live_models") or ()),
            doc_url=str(data.get("doc_url") or DOC_URL),
            omni=tuple(
                OmniVoices(
                    model=str(o.get("model") or ""),
                    default=str(o.get("default") or ""),
                    voices=tuple(_voice_obj(d)
                                 for d in (o.get("voices") or ()) if d.get("id")),
                )
                for o in (data.get("omni") or ())
                if o.get("model")
            ),
            omni_url=str(data.get("omni_url") or OMNI_DOC_URL),
        )


# ---------------------------------------------------------------- 解析（纯函数）

class _TableGrab(HTMLParser):
    """把文档里所有 ``<table>`` 抽成 ``list[list[list[str]]]``（表 → 行 → 单元格）。

    同时记录：
    * 每行里 ``<audio src=...>`` 的地址（官方给的音色试听）
    * **每张表之前的那段正文**（`labels`）—— 音色文档和 omni 文档都靠
      "本节标题 + 默认音色为：X"这段文字来标明"这张表属于哪些模型"，
      不看它就只能靠表序号猜，而实测**表序号与节的顺序并不一一对应**
      （文档里夹着 LiveTranslate 等无关小节）。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.labels = []         # 与 tables 同序：该表之前累积的正文
        self.audio = {}          # (表序号, 行序号) -> src
        self._stack = []
        self._row = None
        self._cell = None
        self._row_audio = None
        self._depth = 0          # <table> 嵌套深度：>0 时正文不记入 label
        self._pending = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._depth += 1
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._row = []
            self._row_audio = ""
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "audio":
            src = a.get("src") or ""
            if src:
                if self._row_audio is not None:
                    self._row_audio = src
                if self._cell is not None:
                    self._cell.append("\x00AUDIO:%s\x00" % src)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            txt = "".join(self._cell)
            txt = re.sub(r"\x00AUDIO:[^\x00]*\x00", "", txt)
            self._row.append(re.sub(r"\s+", " ", txt).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._stack:
            if any(c for c in self._row):
                idx = len(self._stack[-1])
                if self._row_audio:
                    self.audio[(len(self.tables), idx)] = self._row_audio
                self._stack[-1].append(self._row)
            self._row = None
            self._row_audio = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())
            self.labels.append(re.sub(r"\s+", " ", "".join(self._pending)).strip())
            self._pending = []
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        elif self._depth == 0 and data.strip():
            self._pending.append(data)


def _model_ids(text, keep_realtime=False):
    """从"支持模型"单元格里抽出模型 id。

    文档格式：``Qwen3-TTS-Flash：qwen3-tts-flash、qwen3-tts-flash-2025-11-27``
    组名是 TitleCase、模型 id 是全小写 —— 用"小写开头 + 前面不是字母数字"的
    正则即可把组名排除（实测 ``Qwen3-TTS-Flash`` 不会误匹配出 ``wen3-``）。
    只保留含 ``tts`` 的 token，避免混入描述里的普通英文词。

    ``keep_realtime=True`` 时**不**剔除 realtime/vc/vd —— 识别"哪张表是实时表"
    需要看到它们，否则筛选后实时表会变成空集、判定退化成死代码。
    """
    out = []
    for m in _MODEL_ID_RE.finditer(text or ""):
        mid = m.group(1)
        if "tts" not in mid:
            continue
        if not keep_realtime and any(p in mid for p in _EXCLUDE_PARTS):
            continue
        if mid not in out:
            out.append(mid)
    return out


def _parse_detail(text):
    """``音色名：千雪描述：二次元虚拟女友（女性）`` → ``("千雪", "二次元虚拟女友（女性）")``。"""
    t = text or ""
    name = desc = ""
    m = re.search(r"音色名\s*[:：]\s*(.*?)(?=描述\s*[:：]|$)", t)
    if m:
        name = m.group(1).strip()
    m = re.search(r"描述\s*[:：]\s*(.*)$", t)
    if m:
        desc = m.group(1).strip()
    if not name and not desc:
        # 文档结构变了：退化成整段当描述，至少不丢信息
        desc = t.strip()
    return name, desc


def _sort_key(mid):
    """无日期的主线模型排在前（用户最可能选），日期快照排在后。"""
    dated = 1 if re.search(r"\d{4}-\d{2}-\d{2}$", mid) else 0
    if mid == FALLBACK_MODEL:
        rank = 0
    elif mid.startswith("qwen3-tts"):
        rank = 1
    elif mid.startswith("qwen-tts"):
        rank = 2
    else:
        rank = 3
    return (dated, rank, mid)


def parse_doc(html_text, live_models=()):
    """解析音色文档 HTML → Catalog。**纯函数**，网络与文件都在调用方。

    只取"非实时"那张表（模型 id 里不含 realtime）。文档里若出现格式变化，
    返回空 Catalog 而不是抛异常 —— 调用方会保留上一份缓存。
    """
    g = _TableGrab()
    try:
        g.feed(html_text or "")
    except Exception:
        return Catalog(source="empty")

    chosen = None
    chosen_ti = -1
    for ti, table in enumerate(g.tables):
        rows = [r for r in table if r and len(r) >= 4]
        if not rows:
            continue
        head = (rows[0][0] or "").strip().replace(" ", "")
        if head != "voice参数":
            continue
        # 表识别：这一张表的模型 id **全是 realtime** → 是实时表，跳过
        # （同一端点调实时模型会 400：current user api does not support http call）
        raw = set()
        for r in rows[1:]:
            raw.update(_model_ids(r[3], keep_realtime=True))
        if not raw:
            continue
        if all("realtime" in i for i in raw):
            continue
        # 通过识别后，再用**剔除 realtime/vc/vd** 的清单去建音色
        if not any(_model_ids(r[3]) for r in rows[1:]):
            continue
        chosen = rows
        chosen_ti = ti
        break

    if not chosen:
        return Catalog(source="empty")

    voices = []
    models = []
    for ri, r in enumerate(chosen[1:]):
        vid = (r[0] or "").strip()
        if not vid or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 ]{0,24}", vid):
            continue
        name, desc = _parse_detail(r[1])
        langs = tuple(x.strip() for x in (r[2] or "").split("、") if x.strip())
        vmodels = _model_ids(r[3])
        for mid in vmodels:
            if mid not in models:
                models.append(mid)
        voices.append(Voice(id=vid, name=name, desc=desc, langs=langs,
                            models=tuple(vmodels),
                            sample=g.audio.get((chosen_ti, ri + 1), "")))

    return Catalog(
        fetched_at=time.time(),
        source="doc",
        models=tuple(sorted(models, key=_sort_key)),
        voices=tuple(voices),
        live_models=tuple(live_models or ()),
    )


# ---------------------------------------------------------------- Omni 原生语音音色

@dataclass(frozen=True)
class OmniVoices:
    """某个 Omni 模型可用的原生语音音色（模型自己出音频，不调 TTS）。

    与 `Voice.models` 是**反方向**的映射：`Voice` 说"这个音色能被哪些 TTS 模型用"，
    这里说"这个模型能用哪些音色"——因为原生语音的音色集是**按模型（族）给的**，
    实测差异很大（3.5 系 50+ 个、qwen-omni-turbo 只有 4 个）。
    """

    model: str
    default: str = ""
    voices: tuple = ()


def _label_default_voice(label):
    """从"默认音色为：Cherry"里取默认音色。

    取**最后一次**命中：labels 是"上一张表之后到本表之前"的全部正文，
    可能连着上一节的注记；离本表最近的才是本节自己的默认音色。
    """
    hits = re.findall(r"默认音色\s*[为是]?\s*[:：]?\s*([A-Za-z][A-Za-z ]{0,20})",
                      label or "")
    return hits[-1].strip() if hits else ""


#: 节标题里的"模型名 token"。**大小写无关**：实测同一个文档里两种写法并存 ——
#: 有的节写 id（`qwen3-omni-flash-2025-12-01、…`），有的写显示名（`Qwen-Omni-Turbo、…`、
#: `Qwen3.5-Omni、…`）。所以先按"英文词串"抽出来再统一小写，不能只认小写 id。
#:
#: ⚠️ 分隔符**只允许 `-`，不能含空格**。踩过：标题里
#: `Qwen3-Omni-Flash-Realtime qwen3-omni-flash-2025-12-01`（显示名与 id 之间就一个空格）
#: 被空格粘成一个长 token，再被"含 realtime 就丢弃"的规则整条扔掉，
#: 于是真正的 id 一起消失、该节归错模型。
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9.]*(?:-[A-Za-z0-9.]+)*)")


def parse_omni_doc(html_text):
    """解析 omni 音色文档 → ``{模型名 token: OmniVoices}``。**纯函数**。

    实测该文档有**两种列布局并存**，都要认：

    * ``voice参数 | 详情 | 支持语种`` → 音色名/描述塞在"详情"里（与 TTS 文档同格式）
    * ``音色名 | voice参数 | 音色效果 | 描述`` → 拆成独立三列

    归属靠**节标题正文**（`_TableGrab.labels`）而不是表序号：文档里夹着
    LiveTranslate 等无关小节，表序号与节的顺序并不一一对应。

    ⚠️ 标题里既可能是**具体 id**（`qwen3-omni-flash`），也可能是**族名**
    （`qwen3.5-omni`、`qwen-omni-turbo`）。所以这里**按 token 存**，
    由 `Catalog.omni_for()` 做"精确或族前缀匹配，最具体者胜"。
    """
    g = _TableGrab()
    try:
        g.feed(html_text or "")
    except Exception:
        return {}

    out = {}
    for ti, table in enumerate(g.tables):
        rows = [r for r in table if r]
        if len(rows) < 2:
            continue
        head = [(c or "").strip() for c in rows[0]]
        h0 = head[0] if head else ""
        if h0 == "voice参数":
            vcol, ncol, dcol, named = 0, 1, 1, False
        elif "音色名" in h0:
            vcol, ncol, dcol, named = 1, 0, 3, True
        else:
            continue
        label = g.labels[ti] if ti < len(g.labels) else ""
        tokens = _label_tokens(label)
        if not tokens:
            continue
        default = _label_default_voice(label)
        voices = []
        for r in rows[1:]:
            if len(r) <= max(vcol, ncol):
                continue
            vid = (r[vcol] or "").strip()
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 ]{0,24}", vid):
                continue
            if named:
                name = (r[ncol] or "").strip()
                desc = (r[dcol] if len(r) > dcol else "").strip()
            else:
                name, desc = _parse_detail(r[dcol])
            voices.append(Voice(id=vid, name=name, desc=desc))
        if not voices:
            continue
        for tok in tokens:
            out[tok] = OmniVoices(model=tok, default=default,
                                  voices=tuple(voices))
    return out


def _label_tokens(label):
    """从节标题里抽"模型名 token"（小写、保序去重）。

    只留**以 qwen 开头且含 omni** 的词串，并排除 realtime（那条链路是 WebSocket）。
    这样页面标题/面包屑之类的英文（`Omni-modal`、`Voice list`）会被自然滤掉。
    """
    out = []
    for m in _TOKEN_RE.finditer(label or ""):
        tok = m.group(1).lower().rstrip(".")
        if not tok.startswith("qwen") or "omni" not in tok:
            continue
        if "realtime" in tok:
            continue
        if tok not in out:
            out.append(tok)
    return out


# ---------------------------------------------------------------- 网络

def _fetch(url, timeout=FETCH_TIMEOUT):
    from curl_cffi import requests as cffi          # 惰性：导入本模块不需要它
    s = cffi.Session(impersonate="chrome131", timeout=timeout)
    r = s.get(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/131.0.0.0 Safari/537.36"),
    })
    if r.status_code != 200:
        raise RuntimeError("HTTP %s" % r.status_code)
    return r


def _live_models(api_key, timeout=FETCH_TIMEOUT):
    """账户可见模型清单（仅作参考）。失败返回空 tuple，不影响主流程。"""
    if not api_key:
        return ()
    try:
        from curl_cffi import requests as cffi
        s = cffi.Session(impersonate="chrome131", timeout=timeout)
        r = s.get(MODELS_URL, headers={"Authorization": "Bearer " + api_key})
        if r.status_code != 200:
            return ()
        ids = [str(m.get("id")) for m in r.json().get("data", []) if m.get("id")]
        return tuple(sorted(i for i in ids if "tts" in i.lower()))
    except Exception:
        return ()


def fetch_catalog(api_key="", timeout=FETCH_TIMEOUT):
    """联网抓取 + 解析。

    * **TTS 文档**失败 → 抛异常（调用方决定回退策略）
    * **omni 文档**失败 → 只降级为"没有原生语音音色表"，不影响 TTS 功能
      （两条链路的重要性不对等，不该让一个页面拖垮另一个）
    """
    r = _fetch(DOC_URL, timeout=timeout)
    cat = parse_doc(r.text, live_models=_live_models(api_key, timeout))
    if not cat.voices:
        raise RuntimeError("解析结果为空（文档结构可能已变）")
    omni = ()
    try:
        ro = _fetch(OMNI_DOC_URL, timeout=timeout)
        parsed = parse_omni_doc(ro.text)
        omni = tuple(parsed[k] for k in sorted(parsed))
    except Exception:
        omni = ()
    return Catalog(fetched_at=cat.fetched_at, source=cat.source,
                   models=cat.models, voices=cat.voices,
                   live_models=cat.live_models, doc_url=cat.doc_url,
                   omni=omni)


# ---------------------------------------------------------------- 缓存 / 快照

def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def save_cache(cat):
    """写运行时缓存（失败静默：缓存写不进去不该影响功能）。"""
    return _write_json(_CACHE_PATH, cat.to_json())


_live = {"cat": None}
_lock = threading.RLock()


def catalog(reload=False):
    """当前目录（进程内记忆）。**不联网**：缓存 → 快照 → 空。"""
    with _lock:
        have = _live["cat"]
        if have is not None and not reload and have.voices:
            return have
        data = _read_json(_CACHE_PATH)
        src = "cache"
        if not (data and data.get("voices")):
            data = _read_json(_SNAPSHOT_PATH)
            src = "snapshot"
        elif not data.get("omni"):
            # 缓存很可能是**旧版本写的**（那时还没有原生语音音色表）→ 该字段整块缺失。
            # 按字段回落快照：一份旧缓存不该把新增字段顶成空，
            # 否则界面会误报"当前模型不支持原生语音"（实测踩过）。
            snap = _read_json(_SNAPSHOT_PATH) or {}
            if snap.get("omni"):
                data["omni"] = snap["omni"]
                if not data.get("omni_url"):
                    data["omni_url"] = snap.get("omni_url", "")
                src = "cache+snapshot"
        if data and data.get("voices"):
            cat = Catalog.from_json(data, source=src)
        else:
            cat = Catalog(source="empty")
        # 空结果不缓存，避免一次失败把好数据顶掉
        if cat.voices:
            _live["cat"] = cat
        return cat


def refresh(api_key="", timeout=FETCH_TIMEOUT):
    """同步刷新（联网）。成功则更新缓存与进程内记忆并返回新目录。"""
    cat = fetch_catalog(api_key=api_key, timeout=timeout)
    save_cache(cat)
    with _lock:
        _live["cat"] = cat
    return cat


def refresh_async(api_key="", on_done=None, timeout=FETCH_TIMEOUT):
    """后台线程刷新，`on_done(ok, catalog, error)` 在**该线程**里回调。

    UI 调用方需自己把回调切回主线程（Qt 里用信号/``QTimer.singleShot``）。
    """

    def _run():
        try:
            cat = refresh(api_key=api_key, timeout=timeout)
            if on_done:
                on_done(True, cat, "")
        except Exception as e:
            if on_done:
                on_done(False, catalog(), "%s" % e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def is_stale():
    return catalog().is_stale()


# ---------------------------------------------------------------- 便捷查询

def models():
    return catalog().model_ids()


def voices(model=None):
    return catalog().voices_for(model)


def voice(vid):
    return catalog().voice(vid)


def default_model():
    return catalog().default_model()


def default_voice(model=None):
    return catalog().default_voice(model)


def omni_voices(model):
    """某模型的原生语音音色：``(default, tuple[Voice])``；无数据返回 ``("", ())``。"""
    ov = catalog().omni_for(model)
    if ov is None:
        return "", ()
    return ov.default, ov.voices


def has_omni(model):
    """该模型是否有原生语音音色数据（= 能不能让模型自己出语音）。"""
    return catalog().omni_for(model) is not None
