"""语义切分：按章节 + 段落，而不是固定字符数。

chunk_page(page) -> list[dict]:
    每个 chunk 形如 {"pageid", "title", "section", "text"}
    可选 overlap 用于跨块保留上下文。
"""

import re

DEFAULT_MAX_CHARS = 800
DEFAULT_OVERLAP = 100

# 句子边界（中文句号/问号/叹号 + 英文句点）
_SENT_BOUND = re.compile(r"(?<=[。！？!?])\s*")


def _split_long_paragraph(para, max_chars, overlap):
    """超长段落按句子切，末尾句子带 overlap 重叠保留。"""
    sentences = [s for s in _SENT_BOUND.split(para) if s.strip()]
    chunks = []
    buf = ""
    for s in sentences:
        if len(buf) + len(s) <= max_chars:
            buf += s
        else:
            if buf:
                chunks.append(buf)
            if len(s) > max_chars:
                # 单句超长：硬切
                for i in range(0, len(s), max_chars):
                    chunks.append(s[i:i + max_chars])
                buf = ""
            else:
                buf = s[-overlap:] + s
    if buf:
        chunks.append(buf)
    return chunks


def chunk_page(page, max_chars=DEFAULT_MAX_CHARS, overlap=DEFAULT_OVERLAP):
    """按章节+段落切分一页。返回 chunk 字典列表。"""
    chunks = []
    for section in page["sections"]:
        title = section["title"]
        paragraphs = [p.strip() for p in section["body"].split("\n\n") if p.strip()]
        buf = ""
        for para in paragraphs:
            if len(buf) + len(para) + 2 <= max_chars:
                buf = (buf + "\n\n" + para).strip()
            else:
                if buf:
                    chunks.append(_mk(page, title, buf))
                    buf = ""
                if len(para) > max_chars:
                    for piece in _split_long_paragraph(para, max_chars, overlap):
                        chunks.append(_mk(page, title, piece))
                else:
                    buf = para
        if buf:
            chunks.append(_mk(page, title, buf))
    return chunks


def _mk(page, section, text):
    return {
        "pageid": page["pageid"],
        "title": page["title"],
        "section": section,
        "text": text,
    }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, __file__.rsplit("\\", 1)[0])
    from .parse import parse_file

    fname = sys.argv[1] if len(sys.argv) > 1 else "10047_圆石.txt"
    page = parse_file(fname)
    chunks = chunk_page(page)
    print(f"页面: {page['title']}, 共 {len(chunks)} 个 chunk")
    for c in chunks[:6]:
        print(f"  [{c['section'] or '<引言>'}] {len(c['text'])}字: {c['text'][:50]}...")
