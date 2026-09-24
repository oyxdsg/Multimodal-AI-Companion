"""维基文本 → 纯文本解析（mwparserfromhell）。

提供：
- wikitext_to_plain(text): 维基文本转纯文本
- split_sections(text): 按 == 章节 == 拆分，返回 [{"title", "body"}]
- parse_page(pageid, title, text): 返回 {"pageid", "title", "text", "sections"}
- parse_file(fname): 从 data/raw/{fname} 解析一页
"""

import os
import re

import mwparserfromhell

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
PROCESSED = os.path.join(ROOT, "data", "processed")


def wikitext_to_plain(text):
    """维基文本 → 纯文本（去掉模板/链接/表格等）"""
    if not text:
        return ""
    parsed = mwparserfromhell.parse(text)
    return parsed.strip_code().strip()


def split_sections(text):
    """按 == 章节 == 标题拆分。

    用 mwparserfromhell.get_sections(flat=True) 逐级获取章节节点，
    每个节点独立成一段（含引言）。返回 [{"title": str, "body": str}]。
    """
    parsed = mwparserfromhell.parse(text)
    sections = []
    for node in parsed.get_sections(include_lead=True, flat=True):
        heads = node.filter_headings()
        title = ""
        if heads:
            title = heads[0].title.strip_code().strip()
        body = node.strip_code().strip()
        # 去掉章节标题自身的重复文本（标题会出现在正文开头）
        if title:
            body = body.replace(title, "", 1)
        body = body.strip()
        if body:
            sections.append({"title": title, "body": body})
    return sections


def parse_page(pageid, title, text):
    """完整解析：返回可写入 processed 的字典。"""
    plain = wikitext_to_plain(text)
    sections = split_sections(text)
    return {"pageid": pageid, "title": title, "text": plain, "sections": sections}


def parse_file(fname):
    """从 data/raw/{fname} 解析一页。fname 形如 {pageid}_{标题}.txt"""
    path = os.path.join(RAW, fname)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"^(\d+)_(.*)\.txt$", fname)
    pageid = int(m.group(1)) if m else 0
    title = m.group(2) if m else fname
    return parse_page(pageid, title, text)


if __name__ == "__main__":
    import sys
    fname = sys.argv[1] if len(sys.argv) > 1 else "10047_圆石.txt"
    page = parse_file(fname)
    print(f"== {page['title']} (id={page['pageid']}) ==")
    print(f"纯文本长度: {len(page['text'])}")
    print(f"章节数: {len(page['sections'])}")
    for s in page["sections"][:10]:
        print(f"  - [{s['title'] or '<引言>'}] {len(s['body'])}字")
