# -*- coding: utf-8 -*-
"""功能一：把绿幕角色视频转化为标准化桌宠动作。

算法按《视频处理成图片成为宠物动作.md》实现：
色度键抠图 → 去绿溢色 → 水印/绿影清理 → 站姿大小统一
→ 锚点对齐 →（可选）位移轨迹提取。

动作类型（motion）：
- "static"   站姿动作（思考/开心/哭泣等）：无位移轨迹
- "vertical" 位移·垂直：只有垂直轨迹 traj.json（仅 dy）——典型「跳跃」
- "dual"     位移·双向：水平+垂直轨迹 traj.json（dx, dy）——典型「打滚」

锚点（anchor，仅位移动作生效，站姿固定脚底）：
- "foot"    脚底贴地窄带中心：贴地不动点，垂直 dy=脚离地高度（跳跃类）
- "center"  内容包围盒中心：中心不动点，dx/dy=中心偏移（打滚/起伏类）

输出结构：
  <动作目录>/
    frame_0000.png ...
    traj.json            # 仅位移动作
"""
import os
import json

import numpy as np
import cv2
from PIL import Image

# ---- 参数（与桌宠渲染约定一致，见 md 文档） ----
STAND_H = 480          # 站姿内容高度基准（像素）
CANVAS = 512           # 输出画布边长
FOOT_TARGET = 209.6    # 站姿脚底水平基准（对应桌宠"正面"站位）
LOW, HIGH = 35, 95     # 色度键软阈值（d<LOW 全透明，d>HIGH 全不透明）
GREEN_BIAS = 6         # 水印/绿影判定：G 显著高于 R/B 的余量
MIN_FILL_AREA = 2000   # 主体连通域最小面积（去掉水印文字/噪点）
ALPHA_SEED = 0.4       # 前景种子阈值
BODY_FILL = 0.4        # 主体连通域强制不透明的 alpha 阈值

MOTIONS = ("static", "vertical", "dual")
ANCHORS = ("foot", "center")


class ActionError(Exception):
    """处理过程中的用户可读错误。"""


def _frames_from_video(video_path):
    """读取视频全部帧（BGR），失败/为空抛 ActionError。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ActionError(f"无法打开视频：{video_path}\n请确认是 mp4 等可用格式")
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    if not frames:
        raise ActionError(f"视频中没有读取到任何帧：{video_path}")
    return frames


def _chroma_key(bgr):
    """单帧色度键抠图 → RGBA numpy 数组 (h,w,4)。

    步骤：背景色估计 → 软 alpha → 连通域主体保护 → 去绿溢色
    → 水印/绿影清理 → 连通域面积过滤 → 边缘羽化。
    """
    h, w = bgr.shape[:2]
    b = bgr[:, :, 0].astype(np.float32)
    g = bgr[:, :, 1].astype(np.float32)
    r = bgr[:, :, 2].astype(np.float32)

    # 3.1 顶部 10% 条带中位数作为背景绿
    top = bgr[:max(1, h // 10), :, :].reshape(-1, 3)
    bg = np.median(top, axis=0).astype(np.float32)  # [B,G,R]

    # 3.2 色差 → 软 alpha
    d = np.sqrt((r - bg[2]) ** 2 + (g - bg[1]) ** 2 + (b - bg[0]) ** 2)
    alpha = np.clip((d - LOW) / (HIGH - LOW), 0.0, 1.0)

    # 3.3 连通域保护：主体恒不透明
    seed = (alpha > ALPHA_SEED).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    seed = cv2.morphologyEx(seed, cv2.MORPH_OPEN, k)
    seed = cv2.morphologyEx(seed, cv2.MORPH_CLOSE, k)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(seed, 8)
    if n > 1:
        biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        body = (labels == biggest)
        alpha = np.maximum(alpha, np.where(body, BODY_FILL, 0.0))

    # 3.4 去绿溢色：仅对边缘（alpha 小）扣绿
    spill = np.clip(g - np.maximum(r, b), 0, None)
    g2 = g - spill * (1.0 - alpha)

    # 3.5 水印 / 地面阴影清理：绿色显著占优 → 压透明
    residual = (g > b + GREEN_BIAS) & (g > r + GREEN_BIAS)
    alpha = np.where(residual, alpha * 0.04, alpha)

    # 3.6 连通域面积过滤（去掉小块噪点/水印字）
    amask = (alpha > 0.35).astype(np.uint8)
    n2, labels2, stats2, _ = cv2.connectedComponentsWithStats(amask, 8)
    for i in range(1, n2):
        if stats2[i, cv2.CC_STAT_AREA] < MIN_FILL_AREA:
            alpha[labels2 == i] = 0.0

    # 3.6 边缘羽化
    alpha = cv2.GaussianBlur(alpha, (0, 0), 1.0)

    rgba = np.dstack((np.clip(r, 0, 255).astype(np.uint8),
                      np.clip(g2, 0, 255).astype(np.uint8),
                      np.clip(b, 0, 255).astype(np.uint8),
                      np.clip(alpha * 255, 0, 255).astype(np.uint8)))
    return rgba


def _content_bbox(rgba):
    """内容包围盒 (l, t, r, b)；全透明返回 None。"""
    a = rgba[:, :, 3]
    ys, xs = np.nonzero(a)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _foot_anchor(rgba, box):
    """脚底贴地窄带中心（内容 bbox 底端 4% 高度的非零像素加权质心）。"""
    l, t, r, b = box
    band_top = b - max(1, int((b - t) * 0.04))
    a = rgba[band_top:b, l:r, 3].astype(np.float32)
    mass = a.sum()
    if mass == 0:
        return (l + r) / 2.0, b
    ys, xs = np.nonzero(a)
    vals = a[ys, xs]
    cx = float((vals * (xs + l)).sum()) / mass
    cy = float((vals * (ys + band_top)).sum()) / mass
    return cx, cy


def _content_center(rgba, box):
    """内容包围盒中心。"""
    l, t, r, b = box
    return (l + r) / 2.0, (t + b) / 2.0


def _draw_canvas(rgba, box, dx, dy, scale, place="bottom"):
    """把内容按 scale 缩放后放到 512 画布。

    place="bottom"：脚底贴画布底（内容 bbox 左上映射到 (l*scale+dx, t*scale+dy)）；
    place="center"：内容中心对齐画布中心（供位移动作使用）。
    返回 (PIL.Image, 内容左上角在画布坐标)。
    """
    l, t, r, b = box
    crop = rgba[t:b, l:r]
    pil = Image.fromarray(crop, "RGBA")
    cw = max(1, round((r - l) * scale))
    ch = max(1, round((b - t) * scale))
    pil = pil.resize((cw, ch), Image.LANCZOS)
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    if place == "center":
        px = round(CANVAS / 2 - cw / 2)
        py = round(CANVAS / 2 - ch / 2)
    else:
        px = round(l * scale + dx)
        py = round(t * scale + dy)
    canvas.paste(pil, (px, py), pil)
    return canvas, (px, py)


def process_video(video_path, out_dir, motion="static", anchor="foot",
                  progress=None):
    """处理一段绿幕视频为标准化动作帧图。

    motion: "static" 站姿 | "vertical" 位移·垂直 | "dual" 位移·双向
    anchor: "foot" 脚底窄带（贴地）| "center" 内容中心（仅位移动作生效）
    progress: 可选回调 progress(done, total, msg)
    返回 (帧数, traj 条数)；traj 写入 out_dir/traj.json（仅位移动作）。
    """
    if not os.path.isfile(video_path):
        raise ActionError(f"视频文件不存在：{video_path}")
    if motion not in MOTIONS:
        raise ActionError(f"未知动作类型：{motion}（可选 {MOTIONS}）")
    if anchor not in ANCHORS:
        raise ActionError(f"未知锚点：{anchor}（可选 {ANCHORS}）")
    os.makedirs(out_dir, exist_ok=True)

    frames = _frames_from_video(video_path)
    total = len(frames)
    if progress:
        progress(0, total, f"读取到 {total} 帧")

    # 色度键抠图
    rgbs = []
    boxes = []
    for i, f in enumerate(frames):
        rgba = _chroma_key(f)
        box = _content_bbox(rgba)
        if box is None:
            continue
        rgbs.append(rgba)
        boxes.append(box)
    if not rgbs:
        raise ActionError("抠图后没有有效内容帧——视频背景不是绿色绿幕？")
    if progress:
        progress(0, total, f"抠图完成 {len(rgbs)}/{total} 帧有内容")

    # 站姿固定脚底锚点；位移动作按用户选择
    if motion == "static":
        anchor = "foot"
    anchor_fn = _foot_anchor if anchor == "foot" else _content_center
    place = "bottom" if anchor == "foot" else "center"

    # 首帧站立高度 → 统一缩放
    first = rgbs[0]
    fbox = boxes[0]
    h0 = fbox[3] - fbox[1]
    if h0 <= 0:
        raise ActionError("首帧站立高度异常")
    scale = STAND_H / h0

    # 锚点：首帧基准 + 整体平移量（让首帧锚点对齐目标位）
    a0x, a0y = anchor_fn(first, fbox)
    if anchor == "foot":
        dx = FOOT_TARGET - a0x * scale
        # 用「bbox 底」贴画布底而非锚点质心：锚点质心位于脚底窄带中部、比 bbox 底略高，
        # 若按锚点贴底会把 bbox 底部挤出画布，裁掉脚尖/鞋底（脚被截断）。
        # bbox 底贴底 → 脚完整显示，水平定位仍由锚点 x（dx）保证站姿对齐。
        dy = CANVAS - fbox[3] * scale
    else:
        dx = 0.0
        dy = 0.0

    # 位移动作：记录相对首帧锚点的轨迹
    traj = None
    if motion in ("vertical", "dual"):
        traj = []
        for rgba, box in zip(rgbs, boxes):
            ax, ay = anchor_fn(rgba, box)
            rec = {}
            if motion == "dual":
                rec["dx"] = float(round((ax - a0x) * scale, 1))
            rec["dy"] = float(round((a0y - ay) * scale, 1))
            traj.append(rec)

    # 逐帧生成画布并落盘
    n_out = 0
    for i, (rgba, box) in enumerate(zip(rgbs, boxes)):
        canvas, _ = _draw_canvas(rgba, box, dx, dy, scale, place=place)
        path = os.path.join(out_dir, f"frame_{i:04d}.png")
        canvas.save(path)
        n_out += 1
        if progress and i % 5 == 0:
            progress(i + 1, len(rgbs), f"生成帧图 {i + 1}/{len(rgbs)}")

    if traj is not None:
        with open(os.path.join(out_dir, "traj.json"), "w", encoding="utf-8") as f:
            json.dump(traj, f, ensure_ascii=False)
    if progress:
        progress(len(rgbs), len(rgbs),
                 f"完成：{n_out} 帧输出到 {out_dir}"
                 + ("（含 traj.json）" if traj is not None else ""))
    return n_out, len(traj or [])


if __name__ == "__main__":
    # 命令行：python video_to_action.py 视频.mp4 输出目录 [static|vertical|dual] [foot|center]
    import sys
    if len(sys.argv) < 3:
        print("用法：python video_to_action.py 视频.mp4 输出目录 "
              "[static|vertical|dual] [foot|center]")
        sys.exit(1)
    try:
        n, t = process_video(
            sys.argv[1], sys.argv[2],
            motion=sys.argv[3] if len(sys.argv) > 3 else "static",
            anchor=sys.argv[4] if len(sys.argv) > 4 else "foot",
            progress=lambda d, total, m: print(f"[{d}/{total}] {m}"))
        print(f"OK：{n} 帧，轨迹 {t} 条")
    except ActionError as e:
        print(f"错误：{e}")
        sys.exit(1)