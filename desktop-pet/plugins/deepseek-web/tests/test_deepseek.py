# -*- coding: utf-8 -*-
"""deepseek-web 插件测试（SSE 容错 / 图片上传 / 请求体 / 编码）。

原在 desktop-pet/tests/test_reconstruction.py，随插件迁出。
运行：python plugins/deepseek-web/tests/test_deepseek.py
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # plugins/deepseek-web
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(os.path.dirname(BASE)))  # desktop-pet
sys.stdout.reconfigure(encoding="utf-8")

import deepseek_client as ds  # noqa: E402


class _FakeResp:
    def __init__(self, payload=None, text="", status=200):
        self._payload = payload
        self.text = text
        self.status_code = status
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload

    def raise_for_status(self):
        pass


def test_sse_tolerance():
    """V4.1 可能新增 fragment 类型；未知类型必须丢弃，不能混进正文。"""
    c = ds.DeepSeekClient("dummy-token", "")

    ev = [("", {"v": {"response": {"message_id": 2, "fragments": [
        {"type": "RESPONSE", "content": "你好"}]}}})]
    content, thinking = c._extract_content(ev)
    assert content == "你好" and thinking == "", (content, thinking)
    assert c.parent_message_id == 2

    ev = [("", {"v": {"response": {"message_id": 5, "fragments": [
        {"type": "THINK", "content": "推理中"}]}}})]
    content, thinking = c._extract_content(ev)
    assert content == "" and thinking == "推理中", (content, thinking)

    ev = [("", {"v": {"response": {"message_id": 6, "fragments": [
        {"type": "SEARCH", "content": "网页噪声"}]}}}),
          ("", {"p": "response/fragments", "o": "APPEND",
                "v": [{"type": "RESPONSE"}]}),
          ("", {"p": "response/fragments/-1/content", "o": "APPEND",
                "v": "真实回答"})]
    content, thinking = c._extract_content(ev)
    assert content == "真实回答", content
    assert "噪声" not in content

    ev = [("", {"p": "response/content", "o": "APPEND", "v": "A"}),
          ("", {"p": "response/content", "o": "APPEND", "v": "B"})]
    content, _ = c._extract_content(ev)
    assert content == "AB", content

    assert ds.CLIENT_VERSION == "2.4.0", ds.CLIENT_VERSION
    assert ds.FILE_UPLOAD_PATH.endswith("upload_file")
    print("[ok] DeepSeek SSE 解析容错 / 版本常量")


def test_upload_and_body():
    """上传走 FILE_UPLOAD_PATH 且 PoW target_path 正确；请求体带 ref_file_ids。"""
    c = ds.DeepSeekClient("dummy-token", "")
    posted = {}
    pow_targets = []

    def fake_post(url, **kw):
        posted["url"] = url
        posted["json"] = kw.get("json")
        if url.endswith(ds.FILE_UPLOAD_PATH):
            return _FakeResp({"code": 0,
                              "data": {"biz_data": {"id": "file-abc"}}})
        return _FakeResp(text=(
            'event: ready\n'
            'data: {"request_message_id":1,"response_message_id":2}\n\n'
            'data: {"v":{"response":{"message_id":2,'
            '"fragments":[{"type":"RESPONSE","content":"好的"}]}}}\n'))

    c._session.post = fake_post

    def fake_challenge(target_path=ds.COMPLETION_PATH):
        pow_targets.append(target_path)
        return {"challenge": "c", "salt": "s", "expire_at": 1,
                "difficulty": 1, "signature": "sig",
                "target_path": target_path}

    c._get_challenge = fake_challenge
    c._solve_pow = lambda ch: "POW"

    fid = c.upload_image_bytes(b"\x89PNG", "a.png", "image/png")
    assert fid == "file-abc", fid
    assert posted["url"].endswith(ds.FILE_UPLOAD_PATH), posted["url"]
    assert pow_targets and pow_targets[0] == ds.FILE_UPLOAD_PATH, pow_targets

    c.session_id = "sess-1"
    c.parent_message_id = None
    content, _ = c._chat_once("看看这张图", None, "default", False, False,
                              ["file-abc"])
    assert content == "好的", content
    assert posted["json"]["ref_file_ids"] == ["file-abc"], posted["json"]
    assert posted["json"]["model_type"] == "default"
    assert posted["json"]["action"] is None
    assert posted["url"].endswith(ds.COMPLETION_PATH)
    print("[ok] DeepSeek 图片上传 + 请求体（ref_file_ids / action）")


def _ensure_qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.instance() or QGuiApplication([])
    except Exception as e:
        print("[warn] Qt 初始化失败：%s" % e)
        return None


def _tiny_png(w=32, h=32, rgb=(30, 120, 220)):
    import struct
    import zlib

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    raw = b""
    row = bytes(rgb) * w
    for _ in range(h):
        raw += b"\x00" + row
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def test_encode_image():
    # QImage 可独立于 QGuiApplication 使用（不需要创建 app 实例）
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.png")
        with open(p, "wb") as f:
            f.write(_tiny_png())
        data, filename, ctype = ds.encode_image_for_upload(p, max_side=64)
        assert data, "编码结果不能为空"
        assert filename.endswith((".png", ".jpg"))
        assert ctype in ("image/png", "image/jpeg"), ctype
    print("[ok] 图片编码 encode_image_for_upload")


def main():
    print("=" * 62)
    print("DeepSeek 网页版插件测试")
    print("=" * 62)
    test_sse_tolerance()
    test_upload_and_body()
    test_encode_image()
    print("\n全部通过")


if __name__ == "__main__":
    main()
