# -*- coding: utf-8 -*-
"""建筑识别 · 感知包 + 方块数据 体检/监测脚本。

用途：建完建筑后跑一下，快速定位"是分类误判、评分异常、还是数据没写入 / 结构检测问题"。

用法：
  python build_diag.py              # 单次体检（自动探测 .minecraft）
  python build_diag.py --watch      # 实时监测（窗口/阶段变化即打印，Ctrl+C 退出）
  python build_diag.py <mc目录>     # 指定 .minecraft 目录
  python build_diag.py --watch <mc目录>

要点：
  感知包   = <mc>/deskpet/buildings/latest.json   （分类/意图/评分/状态，覆盖写）
  方块数据 = <mc>/deskpet/blocks/*.jsonl          （玩家放置/破坏持久化，追加，永不清理）
"""
import argparse
import io
import json
import math
import os
import sys
import time
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import _mc

# 默认候选 .minecraft 目录（可传参覆盖；顺序见 _mc.py：环境变量 → mc_path.txt → %APPDATA%）
DEFAULT_MC = _mc.candidates()


def locate_mc(explicit):
    cands = []
    if explicit:
        cands.append(os.path.abspath(explicit))
    for d in DEFAULT_MC:
        if d and d not in cands:
            cands.append(d)
    return cands


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except OSError:
        return None, f"无法读取 {path}（文件不存在或无权限）"
    except ValueError as e:
        return None, f"JSON 解析失败（可能写入一半/损坏）：{e}"


# ---------- 感知包诊断 ----------
def diag_pkg(pkg):
    """返回 (ok字段简报, 问题列表)。ok 为真表示数据可读且基本完整。"""
    problems = []
    if not isinstance(pkg, dict):
        return False, ["感知包不是对象（内容为空或格式错误）"]

    got = lambda k: pkg.get(k)
    btn = int(got("window") or 0)
    ts = got("ts")
    reason = got("reason")
    state = got("state")

    if not ts:
        problems.append("缺少 ts（时间戳）——感知包可能来自旧版程序")
    if btn == 0:
        problems.append("window=0（窗口序号缺失，去重逻辑会失效）")

    # 分类/意图
    cls = (got("classification") or "").strip()
    intent = got("intent") or {}
    if not cls:
        problems.append("classification 为空 —— 分类/意图没有生成")
    if not intent:
        problems.append("intent 缺失 —— 意图预测未生效（进行中建筑应含 intent）")
    else:
        icat = intent.get("category")
        iphase = intent.get("phase")
        icomf = intent.get("confidence")
        if not icat:
            problems.append("intent.category 为空")
        if not iphase:
            problems.append("intent.phase 为空")
        if icomf is None or icomf < 0 or icomf > 1:
            problems.append(f"intent.confidence 异常：{icomf}")

    # 评分
    score = got("score") or {}
    if not score:
        problems.append("score 缺失 —— 建筑评分器未运行")
    else:
        for k in ("structure", "decorate", "color"):
            v = score.get(k)
            if v is None or v < -1 or v > 101:
                problems.append(f"score.{k} 异常：{v}")
        if "total" in score and score["total"] < 0:
            problems.append("score.total 为负 —— 评分计算有 bug")

    # 数值健壮性
    def finite(bad, name):
        try:
            v = float(pkg.get(name))
        except (TypeError, ValueError):
            problems.append(f"{name} 非数值：{pkg.get(name)}")
            return
        if math.isinf(v) or math.isnan(v):
            problems.append(f"{name} 出现 NaN/Inf：{v}")

    for name in ("solidity", "aspect_height", "aspect_length", "hang_ratio",
                 "warm_ratio", "brightness", "saturation"):
        finite(False, name)
    for name in ("solidity", "hang_ratio", "warm_ratio", "brightness", "saturation"):
        try:
            v = float(pkg.get(name) or 0)
            if v > 1.0001:   # 覆盖率/比率类不应 >1
                problems.append(f"{name}={v} 超出 [0,1] —— 计算可能有误")
        except (TypeError, ValueError):
            pass

    # 尺寸/方块数
    dim = pkg.get("dimensions") or {}
    bcount = dim.get("block_count")
    if bcount is None:
        problems.append("dimensions.block_count 缺失")
    elif int(bcount) <= 0:
        problems.append(f"block_count={bcount} ≤ 0 —— 快照为空，扫描未生效")

    return (len(problems) == 0), problems


# ---------- 打印感知包 ----------
def print_pkg(pkg, path, show_all=False):
    btn = pkg.get("window")
    ts = (pkg.get("ts") or 0) / 1000
    dim = pkg.get("dimensions") or {}
    intent = pkg.get("intent") or {}
    score = pkg.get("score") or {}
    enc = pkg.get("enclosure") or {}
    print("=" * 66)
    print(f"感知包: {path}")
    print(f"  window #{btn}   reason={pkg.get('reason')}   state={pkg.get('state')}"
          f"   {time.strftime('%H:%M:%S', time.localtime(ts))}")
    print(f"  分类   : {pkg.get('classification')}")
    print(f"  category: {pkg.get('category')}  scale={pkg.get('scale')}  style={pkg.get('style')}"
          f"  roof={pkg.get('roof_type')}  needs_ai={pkg.get('needs_ai')}")
    print(f"  尺寸   : {dim.get('width')}x{dim.get('depth')}x{dim.get('height')}"
          f"  方块 {dim.get('block_count')}  Y:{dim.get('min_y')}~{dim.get('max_y')}")
    print(f"  密闭   : {enc.get('level')} (墙{enc.get('wall') or 0:.0%}"
          f"/顶{enc.get('roof') or 0:.0%}/地{enc.get('floor') or 0:.0%})")
    if intent:
        print(f"  意图   : {intent.get('label')}/{intent.get('category')} "
              f"phase={intent.get('phase')} 完成度{intent.get('completion'):.2f} "
              f"置信{intent.get('confidence'):.2f}")
        print(f"    hint : {intent.get('hint')}")
    if score:
        print(f"  评分   : 结构{score.get('structure')} 装饰{score.get('decorate')} "
              f"配色{score.get('color')} 总分{score.get('total')} 等级{score.get('grade')}")
        print(f"    评价 : {score.get('comment')}")
        for sg in (score.get("suggestions") or [])[:3]:
            print(f"    建议 : {sg}")
    if not show_all:
        return
    # 详细指标
    sym = pkg.get("symmetry") or {}
    walls = pkg.get("walls") or {}
    print("  ---- 详细指标 ----")
    print(f"    实心度 {pkg.get('solidity'):.2f}  高宽比 {pkg.get('aspect_height'):.2f}"
          f"  长宽比 {pkg.get('aspect_length'):.2f}  悬空 {pkg.get('hang_ratio'):.0%}")
    print(f"    形态 截面{pkg.get('section_shape')} 剪影{pkg.get('silhouette')}"
          f" 起伏{pkg.get('roughness'):.2f} 表面比{pkg.get('surface_ratio'):.2f}")
    print(f"    对称 X{sym.get('x') or 0:.0%} Z{sym.get('z') or 0:.0%} 突起 {pkg.get('protrusions')}")
    print(f"    色彩 {pkg.get('color_count')}种[{pkg.get('color_richness')}] 主色{pkg.get('dominant_color')}"
          f" 暖{pkg.get('warm_ratio') or 0:.0%} 亮{pkg.get('brightness'):.2f}")
    print(f"    材质 {pkg.get('materials')} 复杂度{pkg.get('material_complexity')}"
          f" 非完整块 {pkg.get('non_full_count') if 'non_full_count' in pkg else 'N/A'}")
    if walls.get("outer") is not None:
        print(f"    外墙 {walls.get('outer')} 内墙 {walls.get('inner')}")


# ---------- 方块数据（持久化）诊断 ----------
def diag_blocks(mc_dir):
    bdir = os.path.join(mc_dir, "deskpet", "blocks")
    if not os.path.isdir(bdir):
        return None, ["deskpet/blocks/ 目录不存在 —— 持久化记录器未运行（旧版 mod）"]
    files = sorted(f for f in os.listdir(bdir) if f.lower().endswith(".jsonl"))
    if not files:
        return None, ["deskpet/blocks/ 下没有 .jsonl —— 还没写过方块数据"]
    today = time.strftime("%Y%m%d")
    total = 0
    actions = Counter()
    problems = []
    last = None
    for f in files:
        p = os.path.join(bdir, f)
        try:
            cnt = 0
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except ValueError:
                        problems.append(f"{f} 中存在损坏行（写入中断）")
                        continue
                    cnt += 1
                    actions[o.get("action")] += 1
                    if not o.get("by_player"):
                        problems.append(f"{f} 中有一条 by_player=False（过滤标记失效）")
                    last = o
            total += cnt
        except OSError as e:
            problems.append(f"读取 {f} 失败：{e}")
    info = f"blocks/ 共 {len(files)} 个文件、{total} 条记录；动作分布：{dict(actions)}"
    if last:
        info += f"；最近一条：{last.get('action')} {last.get('block')} @({last.get('x')},{last.get('y')},{last.get('z')})"
    if total == 0:
        problems.append("blocks/ 有文件但 0 条记录 —— 放置方块时未写入（BlockRecorder 异常）")
    return info, problems


# ---------- 监测 ----------
def watch(mc_dir):
    pkg_path = os.path.join(mc_dir, "deskpet", "buildings", "latest.json")
    last_win = None
    last_mtime = 0
    print(f"监测中: {pkg_path}  (每 2 秒，Ctrl+C 退出)")
    try:
        while True:
            try:
                mtime = os.path.getmtime(pkg_path)
                if mtime != last_mtime:
                    last_mtime = mtime
                    with open(pkg_path, encoding="utf-8") as f:
                        pkg = json.load(f)
                    w = pkg.get("window")
                    if w != last_win:
                        last_win = w
                        print_pkg(pkg, pkg_path, show_all=True)
                        ok, problems = diag_pkg(pkg)
                        if problems:
                            print("  [问题] " + " | ".join(problems))
                        print()
            except (OSError, ValueError, TypeError):
                pass
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n已退出监测")


def main():
    ap = argparse.ArgumentParser(description="建筑识别感知包/方块数据体检")
    ap.add_argument("mc_dir", nargs="?", help=".minecraft 目录")
    ap.add_argument("--watch", action="store_true", help="实时监测")
    ap.add_argument("--detail", action="store_true", help="打印全部详细指标")
    args = ap.parse_args()

    mc = None
    pkg_path = None
    for m in locate_mc(args.mc_dir):
        sp = os.path.join(m, "deskpet", "buildings", "latest.json")
        if os.path.isdir(m):
            mc = m
            if os.path.isfile(sp):
                pkg_path = sp
            break
    if mc is None:
        print("未找到任何 .minecraft 目录。候选：")
        for m in locate_mc(args.mc_dir):
            print("  -", m, "(存在)" if os.path.isdir(m) else "(不存在)")
        return

    print(f"检测到 .minecraft: {mc}")
    print()

    # 1) 感知包
    print("---- ① 感知包 (buildings/latest.json) ----")
    pkg, err = read_json(pkg_path) if pkg_path else (None, "buildings/latest.json 不存在")
    if err:
        print("  [错] " + err)
    else:
        print_pkg(pkg, pkg_path, show_all=args.detail)
        _, problems = diag_pkg(pkg)
        if problems:
            print("  [发现 " + str(len(problems)) + " 个问题]")
            for p in problems:
                print("    - " + p)
        else:
            print("  [OK] 感知包字段完整、数值合理")
    print()

    # 2) 方块数据持久化
    print("---- ② 方块数据持久化 (deskpet/blocks/) ----")
    info, bproblems = diag_blocks(mc)
    if info:
        print("  " + info)
    if bproblems:
        for p in bproblems:
            print("  [发现] " + p)
    else:
        print("  [OK] 方块数据持久化正常")
    print()

    # 3) 结论
    print("---- ③ 结论 ----")
    allbad = (err is not None)
    if err:
        print("  · 尚无感知包：请进世界放 ≥4 个相邻方块激活建筑模式，等待 20s 窗口输出。")
    else:
        _, pp = diag_pkg(pkg)
        if pp:
            print("  · 感知包存在但有问题，见上面 ①。最常见：")
            print("      - intent/score 缺失 → 旧 mod jar，未打包最新类；")
            print("      - NaN/比率>1 → 结构检测或评分计算异常（看 ① 数值）；")
            print("      - classification 为空 → 该窗口 reason/规模被跳过。")
        else:
            print("  · 感知包正常。若分类/评分不合预期，用 --detail 看指标，或把本输出发我。")
    if info is None:
        print("  · 方块持久化未生效：确认 .minecraft/mods/ 用的是最新 deskpet-mod jar 并重启游戏。")
    elif any("0 条记录" in p for p in bproblems):
        print("  · 你放了方块但 blocks/ 无记录 → 放置事件未落盘，检查 BlockRecorder。")
    if args.watch:
        watch(mc)


if __name__ == "__main__":
    main()
