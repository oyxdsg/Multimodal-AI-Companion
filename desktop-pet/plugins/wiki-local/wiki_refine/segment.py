# -*- coding: utf-8 -*-
"""Wiki 页面 → 候选句：按小节链切分、清洗，产出 (小节链, 句子)。

数据源：wiki_data/mcwiki.db（wiki_build.py 构建，文本已清洗）。
"""
import os
import re
import sqlite3

# 二级小节 ==X== 与三级小节 ===X=== / ====X====
_SEC_RE = re.compile(r"^==\s*([^=]+?)\s*==\s*$", re.M)
_SUB_RE = re.compile(r"^={3,}\s*([^=]+?)\s*=*\s*$", re.M)

# 模板/公式残骸：这类句在清洗后残留，信息不可用
_JUNK_RE = re.compile(
    r"\\frac|\\[a-zA-Z]+|{{|}}|__|&[a-z]+;|<ref|/\*|\*/")

# 数据表噪音（wiki_build 把表格压成单行文本）
_NOISE_RE = re.compile(
    r"\[数据表\]"
    r"|数量范围\s*/\s*掉落概率"
    r"|概率\s*/\s*平均掉落数量"
    r"|无抢夺"
    r"|表为|如下表|下表")

# 碎片句：长度过短或指向性残句
_FRAG_RE = re.compile(
    r"详见下文|见下文|见上文|详见上方|见下$|^\s*[（(].{0,12}[）)]\s*$")


def _db_path():
    here = os.path.dirname(os.path.abspath(__file__))       # nlu/wiki_refine
    root = os.path.dirname(os.path.dirname(here))           # desktop-pet
    return os.path.join(root, "wiki_data", "mcwiki.db")


def connect():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _clean(t):
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


def _split_sents(seg):
    out = []
    for s in re.split(r"(?<=[。！？])", seg):
        s = (s or "").strip()
        if s:
            out.append(s)
    return out


def page_sections(text):
    """文本 → [(小节链, 正文)]，三级标题并入父节。返回列表。"""
    out = []
    pos = 0
    chain = ["引言"]
    secs = sorted(
        [(m.start(), m.end(), m.group(1).strip(), 2)
         for m in _SEC_RE.finditer(text)]
        + [(m.start(), m.end(), m.group(1).strip(), 3)
           for m in _SUB_RE.finditer(text)],
        key=lambda x: x[0])
    for st, en, name, lvl in secs:
        body = text[pos:st].strip()
        if body:
            out.append((list(chain), body))
        if lvl == 2:
            chain = [name]
        else:
            chain = chain[:1] + [name]
        pos = en
    body = text[pos:].strip()
    if body:
        out.append((list(chain), body))
    return out


def page_candidates(title_or_text, text=None):
    """页面文本 → 候选句列表：[{"sec": "小节链/", "text": 句子}]。"""
    if text is None:
        conn = connect()
        row = conn.execute(
            "SELECT title, text FROM pages WHERE title=?", (title_or_text,)).fetchone()
        conn.close()
        if not row:
            return []
        text = row["text"]
    out = []
    for chain, body in page_sections(text):
        sec = "/".join(chain)
        for s in _split_sents(_clean(body)):
            out.append({"sec": sec, "text": s})
    return out


def is_junk(s):
    """模板/公式/数据表/碎片 残骸。"""
    if not s or len(s) < 8:
        return True
    if _JUNK_RE.search(s) or _NOISE_RE.search(s) or _FRAG_RE.search(s):
        return True
    # 句子以非中文/数字结尾的模板尾巴（如“==”残留）
    if re.search(r"[=|{}\[\]]$", s):
        return True
    return False
