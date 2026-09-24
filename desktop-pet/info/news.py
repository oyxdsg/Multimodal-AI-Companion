"""新闻模块：抓取并解析 RSS 源，汇总标题供桌宠播报。

网络请求应在后台线程调用（fetch_feed），失败返回 None 静默降级。
"""

import html
import re
import urllib.request
import xml.etree.ElementTree as ET

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DesktopPet/1.0"}
_TIMEOUT = 10


def _http_get(url, timeout=_TIMEOUT):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def _clean(text):
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def parse_rss(xml_text):
    """解析 RSS/Atom 文本，返回标题列表（按出现顺序）；解析失败返回 []。"""
    titles = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    # RSS 2.0: channel/item/title
    for item in root.iter("item"):
        title = item.findtext("title")
        if title:
            titles.append(_clean(title))
    # Atom: feed/entry/title（兜底）
    if not titles:
        for entry in root.iter("entry"):
            title = entry.findtext("title")
            if title:
                titles.append(_clean(title))
    return titles


def fetch_feed(url):
    """拉取单个 RSS 源，返回标题列表；失败返回 None。"""
    try:
        return parse_rss(_http_get(url))
    except Exception:
        return None