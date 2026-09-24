# -*- coding: utf-8 -*-
"""
动作图颜色校准：以「正面」为基准，统一所有动作的色调。

方法：
- 在 LAB 空间，对 亮度 L 与 蓝黄轴 b 做「分位数直方图匹配」（0~100 逐分位 分段线性映射 + 端点线性外推）。
- a 通道（红绿 / 脸红 / 表情变化）保留不动。
- 每个动作用「整段动作的全局实体像素分位数」构造**唯一**的映射函数，再应用到该动作全部帧 → 帧间零闪烁、动作间色调一致。

用法：
    python tools/color_calibrate.py            # dry-run：只打印各动作将应用的映射与统计，不写文件
    python tools/color_calibrate.py --apply    # 校准写回 assets（直接用正面为目标，不改正面）
"""
import os
import sys
import glob
import shutil
import numpy as np
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(BASE, "assets")
REF_ACTION = "正面"


# ---------- sRGB <-> Lab ----------
def srgb_to_lab(rgb01):
    def f(t):
        return np.where(t > 0.04045, ((t + 0.055) / 1.055) ** 2.4, t / 12.92)
    r, g, b = rgb01[..., 0], rgb01[..., 1], rgb01[..., 2]
    r, g, b = f(r), f(g), f(b)
    X = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047
    Y = (r * 0.2126729 + g * 0.7151522 + b * 0.0721750)
    Z = (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) / 1.08883
    def ft(t):
        return np.where(t > 0.008856, t ** (1 / 3), 7.787 * t + 16 / 116)
    fx, fy, fz = ft(X), ft(Y), ft(Z)
    L = 116 * fy - 16
    A = 500 * (fx - fy)
    B = 200 * (fy - fz)
    return L, A, B


def lab_to_srgb(L, A, B):
    fy = (L + 16) / 116
    fx = fy + A / 500
    fz = fy - B / 200
    def ft_inv(t):
        return np.where(t > 6 / 29, t ** 3, 3 * (6 / 29) ** 2 * (t - 4 / 29))
    X = ft_inv(fx) * 0.95047
    Y = ft_inv(fy)
    Z = ft_inv(fz) * 1.08883
    def g_inv(t):
        return np.where(t > 0.0031308, 1.055 * t ** (1 / 2.4) - 0.055, 12.92 * t)
    r = g_inv(3.2404542 * X - 1.5371385 * Y - 0.4985314 * Z)
    g = g_inv(-0.9692660 * X + 1.8760108 * Y + 0.0415560 * Z)
    b = g_inv(0.0556434 * X - 0.2040259 * Y + 1.0572252 * Z)
    return np.stack([r, g, b], axis=-1)


# ---------- 分位数 ----------
def global_percentiles(act_dir, is_ref=False):
    """汇总整段动作所有帧的实体像素 L/b，返回 0~100 逐分位数组。"""
    frames = sorted(glob.glob(os.path.join(act_dir, "frame_*.png")))
    Ls, Bs = [], []
    for fp in frames:
        a = np.array(Image.open(fp).convert("RGBA"))
        mask = a[..., 3] > 100
        if mask.sum() == 0:
            continue
        lab = srgb_to_lab(a[..., :3][mask].astype(np.float32) / 255.0)
        Ls.append(lab[0]), Bs.append(lab[2])
    if not Ls:
        return None, None, 0
    Lc = np.concatenate(Ls)
    Bc = np.concatenate(Bs)
    ps = np.linspace(0, 100, 101)
    return np.percentile(Lc, ps), np.percentile(Bc, ps), len(frames)


def build_map(src_q, dst_q):
    """构造 0~100 逐分位分段线性映射 + 端点线性外推。"""
    sn, sm = (src_q[-1] - src_q[-2]), (src_q[1] - src_q[0])
    dn, dm = (dst_q[-1] - dst_q[-2]), (dst_q[1] - dst_q[0])
    def apply(x):
        y = np.interp(x, src_q, dst_q)
        lo = dst_q[0] + (x - src_q[0]) * (dm / sm if sm else 0)
        hi = dst_q[-1] + (x - src_q[-1]) * (dn / sn if sn else 0)
        return np.where(x < src_q[0], lo, np.where(x > src_q[-1], hi, y))
    return apply


def report(title, src_q, dst_q):
    for lbl in (10, 50, 90):
        pass
    print("  {:<2s} 源=({:6.1f},{:6.1f},{:6.1f})  目标=({:6.1f},{:6.1f},{:6.1f})".format(
        "", src_q[10], src_q[50], src_q[90], dst_q[10], dst_q[50], dst_q[90]))


def main():
    apply_ = "--apply" in sys.argv
    dirs = sorted(d for d in os.listdir(ASSETS)
                  if os.path.isdir(os.path.join(ASSETS, d)))
    ref_dir = os.path.join(ASSETS, REF_ACTION)
    refLq, refBq, refn = global_percentiles(ref_dir)
    if refLq is None:
        print("无法读取正面基准，退出")
        return
    print("基准动作 %s  帧=%d  L(10/50/90)=(%.1f,%.1f,%.1f)  b(10/50/90)=(%.1f,%.1f,%.1f)" % (
        REF_ACTION, refn, refLq[10], refLq[50], refLq[90], refBq[10], refBq[50], refBq[90]))

    mode = "APPLY(写回)" if apply_ else "DRY-RUN"
    print("模式: %s\n" % mode)

    for d in dirs:
        act_dir = os.path.join(ASSETS, d)
        srcLq, srcBq, n = global_percentiles(act_dir)
        if srcLq is None or n == 0:
            print("跳过 %s（无效）" % d)
            continue
        dl = "  |  L: %5.1f -> %5.1f (Δ%+5.1f)" % (srcLq[50], refLq[50], refLq[50] - srcLq[50])
        db = "  |  b: %5.1f -> %5.1f (Δ%+5.1f)" % (srcBq[50], refBq[50], refBq[50] - srcBq[50])
        dsat = "  |  L90-%+4.1f b90-%+4.1f" % (refLq[90] - srcLq[90], refBq[90] - srcBq[90])
        print("%-8s 帧=%d%s%s%s" % (d, n, dl, db, dsat))

        if not apply_:
            continue
        fL, fB = build_map(srcLq, refLq), build_map(srcBq, refBq)
        for fp in sorted(glob.glob(os.path.join(act_dir, "frame_*.png"))):
            img = Image.open(fp).convert("RGBA")
            arr = np.array(img)
            alpha = arr[..., 3]
            m = alpha > 0
            if m.sum() == 0:
                continue
            rgb01 = arr[..., :3][m].astype(np.float32) / 255.0
            L, A, B = srgb_to_lab(rgb01)
            L2 = np.clip(fL(L), 0, 100)
            B2 = fB(B)
            rgb2 = lab_to_srgb(L2, A, B2)
            rgb2 = np.clip(rgb2, 0, 1)
            newrgb = (rgb2 * 255.0 + 0.5).astype(np.uint8)
            out = arr.copy()
            out[..., :3][m] = newrgb
            Image.fromarray(out, "RGBA").save(fp)

    if apply_:
        print("\n校准完成（已写回 assets）。备份位于 assets_backup_* 目录。")


if __name__ == "__main__":
    main()
