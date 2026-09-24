# -*- coding: utf-8 -*-
"""抢救脚本：把 latest.log 里旧版 mod 的 [building] place 方块记录，导出为持久化 jsonl，
避免随日志轮转/清理丢失。输出到 <mc>/deskpet/blocks/backup_<date>.jsonl。

用法：python rescue_build_log.py [mc目录] [--all-log]
  --all-log 扫描 logs/ 下所有 .log / .gz（默认只 latest.log）
"""
import argparse
import io
import json
import os
import re
import sys
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import _mc

# 默认候选 .minecraft 目录（可传参覆盖；顺序见 _mc.py：环境变量 → mc_path.txt → %APPDATA%）
DEFAULT_MC = _mc.candidates()
RE = re.compile(r"\[(\d{2}):(\d{2}):(\d{2})\].*?\[building\] place (\S+) at \((-?\d+),(-?\d+),(-?\d+)\) id=(-?\d+)")
DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def parse_ts(logdir, line, mtime):
    """用日志行时间 + 文件名/修改日期构造近似毫秒时间戳。"""
    hh, mm, ss = line.group(1), line.group(2), line.group(3)
    # 优先从 latest.log 文件名没有日期，用 mtime 的日期
    d = datetime.fromtimestamp(mtime)
    return int(datetime(d.year, d.month, d.day, int(hh), int(mm), int(ss)).timestamp() * 1000)


def locate_mc(explicit):
    cands = []
    if explicit:
        cands.append(os.path.abspath(explicit))
    for x in DEFAULT_MC:
        if x and x not in cands:
            cands.append(x)
    return cands


def log_files(mc, all_log):
    logs = os.path.join(mc, "logs")
    if not os.path.isdir(logs):
        return []
    if not all_log:
        p = os.path.join(logs, "latest.log")
        return [p] if os.path.isfile(p) else []
    files = []
    for f in os.listdir(logs):
        if f.endswith(".log"):
            files.append(os.path.join(logs, f))
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mc_dir", nargs="?")
    ap.add_argument("--all-log", action="store_true")
    args = ap.parse_args()

    mc = None
    for m in locate_mc(args.mc_dir):
        if os.path.isdir(m):
            mc = m
            break
    if mc is None:
        print("未找到 .minecraft")
        return

    for f in log_files(mc, args.all_log):
        if not os.path.isfile(f):
            continue
        mtime = os.path.getmtime(f)
        rows = 0
        out_lines = []
        try:
            text = _mc.read_text(f)
        except OSError:
            continue
        for line in text.splitlines():
            m = RE.search(line)
            if not m:
                continue
            ts = parse_ts(None, m, mtime)
            rec = {
                "ts": ts, "player": m.group(4),
                "x": int(m.group(5)), "y": int(m.group(6)), "z": int(m.group(7)),
                "id": int(m.group(8)), "action": "place", "by_player": True,
                "project": "", "world": "", "block": "", "source": "log_rescue",
            }
            out_lines.append(json.dumps(rec, ensure_ascii=False))
            rows += 1
        if not rows:
            print(f"{os.path.basename(f)}: 无 [building] 记录")
            continue
        outdir = os.path.join(mc, "deskpet", "blocks")
        os.makedirs(outdir, exist_ok=True)
        day = time.strftime("%Y%m%d", time.localtime(mtime))
        out = os.path.join(outdir, f"backup_{day}.jsonl")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out_lines) + "\n")
        print(f"{os.path.basename(f)}: 提取 {rows} 条  ->  {out}")


if __name__ == "__main__":
    main()
