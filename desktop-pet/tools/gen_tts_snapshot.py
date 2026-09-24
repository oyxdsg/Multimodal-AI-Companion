# -*- coding: utf-8 -*-
"""生成 `voice/tts_catalog.snapshot.json`（随包快照，离线首选用）。

维护者工具：跟官方文档同步音色/模型清单。

    python tools/gen_tts_snapshot.py                  # 从官方文档抓
    python tools/gen_tts_snapshot.py --from-file x.html   # 用本地 HTML（离线/回归）

跑完请把 `voice/tts_catalog.snapshot.json` 一起提交。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from voice import tts_catalog as tc  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-file", default="",
                    help="改用本地 HTML 而不是联网抓取")
    ap.add_argument("--key", default="", help="百炼 Key（可选，仅用于记录账户清单）")
    a = ap.parse_args()

    if a.from_file:
        with open(a.from_file, "r", encoding="utf-8") as f:
            html = f.read()
        live = ()
        if a.key:
            live = tc._live_models(a.key)
        cat = tc.parse_doc(html, live_models=live)
        cat = tc.Catalog(fetched_at=cat.fetched_at, source="doc",
                         models=cat.models, voices=cat.voices, live_models=live)
    else:
        cat = tc.fetch_catalog(api_key=a.key)

    if not cat.voices:
        print("FAIL: 解析结果为空")
        return 1

    ok = tc._write_json(tc._SNAPSHOT_PATH, cat.to_json())
    print("音色 %d 个 / 模型 %d 个 / 账户可见 TTS 模型 %d 个"
          % (len(cat.voices), len(cat.models), len(cat.live_models)))
    print("模型清单：%s" % (list(cat.models),))
    print("前 5 个音色：")
    for v in cat.voices[:5]:
        print("  %-14s %s%s" % (v.id, v.label(),
                                ("  ♪" if v.sample else "")))
    print("默认模型 = %s   默认音色 = %s"
          % (cat.default_model(), cat.default_voice(cat.default_model())))
    print("已写入 %s（%s）"
          % (os.path.relpath(tc._SNAPSHOT_PATH), "OK" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
