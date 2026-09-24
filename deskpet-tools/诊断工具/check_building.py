# -*- coding: utf-8 -*-
"""监测脚本：检查建筑识别感知包（deskpet/buildings/latest.json）。

用法：
  python check_building.py [.minecraft路径]        # 打印最新感知包
  python check_building.py --watch [.minecraft路径] # 每 3 秒轮询，检测到新窗口就打印

触发方式：游戏中连续放置 ≥4 个相邻方块激活建筑模式，之后每 20 秒窗口
（有新放置）会输出一次感知包；60 秒无操作输出 final 后休眠。
"""
import argparse
import io
import json
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import _mc

# 默认候选 .minecraft 目录（可传参覆盖；顺序见 _mc.py：环境变量 → mc_path.txt → %APPDATA%）
DEFAULT_CANDIDATES = _mc.candidates()


def find_latest(mc_dir):
    return os.path.join(mc_dir, "deskpet", "buildings", "latest.json")


def print_package(pkg, path):
    print("=" * 62)
    print(f"最新感知包: {path}")
    print("=" * 62)
    dim = pkg.get("dimensions") or {}
    enc = pkg.get("enclosure") or {}
    sym = pkg.get("symmetry") or {}
    contents = pkg.get("contents") or {}
    walls = pkg.get("walls") or {}
    rooms = pkg.get("rooms") or []
    ts = (pkg.get("ts") or 0) / 1000   # 毫秒 → 秒
    print(f"  时间: {time.strftime('%H:%M:%S', time.localtime(ts))}"
          f"  窗口#{pkg.get('window')}  reason={pkg.get('reason')}")
    print(f"  分类: {pkg.get('classification')}   (category={pkg.get('category')}"
          f", 规模={pkg.get('scale')}, 风格={pkg.get('style')}, needs_ai={pkg.get('needs_ai')})")
    print(f"  尺寸: {dim.get('width')}×{dim.get('depth')}×{dim.get('height')}"
          f"  方块数: {dim.get('block_count')}"
          f"  Y:{dim.get('min_y')}~{dim.get('max_y')}")
    print(f"  密闭: {enc.get('level')}  (墙{enc.get('wall') or 0:.0%}"
          f"/顶{enc.get('roof') or 0:.0%}/地{enc.get('floor') or 0:.0%})")
    print(f"  墙: 外{walls.get('outer')} 内{walls.get('inner')}"
          f" 独立{walls.get('standalone')}")
    if walls.get("list"):
        for w in walls["list"][:6]:
            print(f"     - {w.get('material')} 高{w.get('height')}"
                  f" 长{w.get('length')} {w.get('dir')}向 {w.get('type')}")
    print(f"  房间: {len(rooms)}  空腔: {pkg.get('cavities')}"
          f"  柱: {pkg.get('pillars')}  梁: {pkg.get('beams')}")
    if rooms:
        for rm in rooms[:4]:
            print(f"     - 体积{rm.get('volume')} 用途:{rm.get('purpose') or '-'}"
                  f" Y:{rm.get('floor_y')}~{rm.get('ceil_y')}")
    print(f"  对称: X={sym.get('x') or 0:.0%} Z={sym.get('z') or 0:.0%}"
          f"  悬空率: {pkg.get('hang_ratio') or 0:.0%}")
    if contents:
        print(f"  功能物件: " + ", ".join(f"{k}×{v}" for k, v in contents.items()))
    else:
        print("  功能物件: (无)")
    print(f"  水体: {pkg.get('water')}  地表Y: {pkg.get('ground_y')}")
    print(f"  材质: {pkg.get('materials')}  复杂度: {pkg.get('material_complexity')}"
          f"  实心度: {pkg.get('solidity') or 0:.0%}")
    print(f"  形态: 截面={pkg.get('section_shape')} 剪影={pkg.get('silhouette')}"
          f" 起伏={pkg.get('roughness')} 表面比={pkg.get('surface_ratio')}")
    cen = pkg.get("centroid_offset") or {}
    print(f"  质心偏移: ({cen.get('dx'):+.2f},{cen.get('dy'):+.2f},{cen.get('dz'):+.2f})"
          f" 层方差={pkg.get('layer_area_var')} 突起={pkg.get('protrusions')}")
    print(f"  顶/底材质: {pkg.get('top_material')} / {pkg.get('base_material')}"
          f"  分层切换:{pkg.get('material_layer_changes')}次")
    print(f"  色彩: {pkg.get('color_count')}种[{pkg.get('color_richness')}] "
          f"主色:{pkg.get('dominant_color')} 次色:{pkg.get('secondary_color')} "
          f"暖:{pkg.get('warm_ratio') or 0:.0%} 亮:{pkg.get('brightness') or 0:.2f} "
          f"饱和:{pkg.get('saturation') or 0:.2f} 色板:{pkg.get('palette')}")
    print(f"  锚点: {pkg.get('anchor')}  世界: {pkg.get('world')}")


def locate():
    """返回 (mc_dir, latest_path)；未找到返回 (None, None)。"""
    mc = args_mc_dir()
    for m in mc_candidates(mc):
        if not os.path.isdir(m):
            continue
        path = find_latest(m)
        if os.path.isfile(path):
            return m, path
    return None, None


def mc_candidates(explicit):
    dirs = []
    if explicit:
        dirs.append(os.path.abspath(explicit))
    for d in DEFAULT_CANDIDATES:
        if d and d not in dirs:
            dirs.append(d)
    return dirs


def args_mc_dir():
    global _explicit
    return _explicit


_explicit = None


def watch(path):
    last = -1
    last_mtime = 0
    print(f"监控中: {path}  (每 3 秒检查一次，Ctrl+C 退出)")
    try:
        while True:
            try:
                mtime = os.path.getmtime(path)
                if mtime != last_mtime:
                    last_mtime = mtime
                    try:
                        with open(path, encoding="utf-8") as f:
                            pkg = json.load(f)
                        win = pkg.get("window")
                        if win != last:
                            last = win
                            print_package(pkg, path)
                            print()
                    except (ValueError, TypeError, OSError):
                        pass
            except OSError:
                pass
            time.sleep(3)
    except KeyboardInterrupt:
        print("\n已退出")


def main():
    global _explicit
    parser = argparse.ArgumentParser(description="检查建筑识别感知包")
    parser.add_argument("mc_dir", nargs="?", help=".minecraft 目录路径（可选）")
    parser.add_argument("--watch", action="store_true", help="轮询监控模式")
    args = parser.parse_args()
    _explicit = args.mc_dir

    mc, path = locate()
    if not path:
        print("未找到最新感知包。请先启动游戏并连续放置 ≥4 个相邻方块，")
        print("建筑识别每 20 秒窗口（有新放置时）写入一次。候选目录：")
        for m in mc_candidates(_explicit):
            print("  -", m, "(存在)" if os.path.isdir(m) else "(不存在)")
        return

    if args.watch:
        watch(path)
        return
    try:
        with open(path, encoding="utf-8") as f:
            pkg = json.load(f)
        print_package(pkg, path)
    except (ValueError, TypeError):
        print(f"(文件 {path} 内容损坏/为空)")


if __name__ == "__main__":
    main()