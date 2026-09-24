"""宠物动画系统：素材加载 / 帧缓存 / 状态机 / 过渡链 / 巡逻 / 鼠标交互。

原为 pet/window.py 的一部分，现拆为独立 Mixin（PetAnimationMixin），
由 PetWindow 继承，行为与原先完全一致。
"""

import os
import json
import pickle
import random
import re

import numpy as np

from PySide6.QtCore import QRect, QTimer, Qt
from PySide6.QtGui import (
    QImage,
    QPixmap,
    QCursor,
    QTransform,
    QPainter,
)
from PySide6.QtWidgets import QApplication, QLabel

import config
from core.action_key import ActionKey

_NONZERO = re.compile(rb"[^\x00]")


def _load_analyze_cache(path):
    """读取素材分析缓存（bbox/脚心/亮度），失败返回空缓存。"""
    try:
        with open(path, "rb") as f:
            c = pickle.load(f)
            if isinstance(c, dict):
                return c
    except Exception:
        pass
    return {"boxes": {}, "feet": {}, "brights": {}, "meta": {}}


def _save_analyze_cache(path, cache):
    try:
        with open(path, "wb") as f:
            pickle.dump(cache, f)
    except Exception:
        pass


def _analyze_cache_valid(cache, key, files, path):
    """缓存的 bbox/脚心是否与当前素材文件完全一致（按大小+mtime 校验）。"""
    meta = cache.get("meta", {}).get(key)
    if not meta or len(meta) != len(files):
        return False
    for fn in files:
        try:
            st = os.stat(os.path.join(path, fn))
            if meta.get(fn) != (st.st_size, st.st_mtime):
                return False
        except OSError:
            return False
    return True


def _analyze_cache_get(cache, key, fn):
    """从缓存取 (box, foot_center_x)，缺失返回 (None, None)。"""
    box = cache.get("boxes", {}).get(key, {}).get(fn)
    foot = cache.get("feet", {}).get(key, {}).get(fn)
    if box is None or foot is None:
        return None, None
    return QRect(*box), foot


def _analyze_cache_set(cache, key, fn, box, foot, path):
    cache.setdefault("boxes", {}).setdefault(key, {})[fn] = (
        box.x(), box.y(), box.width(), box.height())
    cache.setdefault("feet", {}).setdefault(key, {})[fn] = foot
    try:
        st = os.stat(os.path.join(path, fn))
        cache.setdefault("meta", {}).setdefault(key, {})[fn] = (
            st.st_size, st.st_mtime)
    except OSError:
        pass


def _frame_sort_key(fn):
    m = re.search(r"(\d+)", fn)
    return int(m.group(1)) if m else 0


def _list_png(path):
    """安全列出目录下的 PNG（目录不存在 / 无权限返回 []，绝不抛）。"""
    try:
        return [f for f in os.listdir(path) if f.lower().endswith(".png")]
    except OSError:
        return []


def _row_bounds(row_alpha):
    """返回一行 alpha 数据的 [first, last] 非零索引，全零返回 None。"""
    m = _NONZERO.search(row_alpha)
    if not m:
        return None
    first = m.start()
    m2 = _NONZERO.search(row_alpha[::-1])
    last = len(row_alpha) - m2.start() - 1
    return first, last


class PetAnimationMixin:
    """动画 / 状态机 / 过渡链 / 巡逻 / 鼠标交互。由 PetWindow 继承。"""

    # ---------- 皮肤上下文 ----------

    def _init_skin_ctx(self):
        """按当前皮肤确定素材目录 / 动作映射 / 缓存路径。

        self._skin 由 PetWindow 在 __init__ 时先设置（皮肤切换时更新），
        未设置时回退内置皮肤（默认大肥鱼）。
        """
        from pet import skins
        self._skin = getattr(self, "_skin", None)
        if self._skin is None:
            self._skin = skins.BuiltinSkin()
        self._assets_dir = self._skin.assets_dir
        self._actions_map = self._skin.actions
        self._base_frames_dir = self._skin.base_frames_dir
        self._analyze_cache_path = self._skin.analyze_cache_path

    def _action_label(self, key):
        """当前皮肤下某动作的显示名（右键菜单 / AI 标签）。"""
        ent = self._actions_map.get(key)
        if ent:
            return ent.get("label") or key
        return config.ACTIONS.get(key, key)

    # ---------- 加载素材 ----------

    def _load_actions(self):
        self._init_skin_ctx()
        self.actions = {}
        # 懒加载状态：_lazy_loaded=已加载到 _frame_cache 的 lazy key；
        # _lazy_order=LRU 最近使用序（前=最近）；_frames_are_user=act["frames"] 已替换为用户帧的 key
        self._lazy_loaded = set()
        self._lazy_order = []
        self._frames_are_user = set()
        # B1 启动优化：优先读取磁盘上的「基础显示帧」缓存（已含统一亮度 gain/贴底/
        # 位移轨迹/镜像，但**不**含用户亮度·对比·饱和度）。命中则跳过「读 1600+ 张
        # 512×512 原图 + 逐帧缩放绘制」，启动大幅提速；用户改画面不触发此缓存失效。
        if self._base_frames_load():
            return
        cache = _load_analyze_cache(self._analyze_cache_path)

        # 第一遍：加载素材并计算每个动作的内容包围盒（优先用磁盘缓存）
        for key, ent in self._actions_map.items():
            folder = ent["dir"]
            path = os.path.join(self._assets_dir, folder)
            files = _list_png(path)
            files.sort(key=_frame_sort_key)
            act = {"images": [], "boxes": [], "frames": [], "mirrored": [],
                   "scale": 1.0, "size": (1, 1), "gb": None, "tops": [],
                   "feet": [], "traj": None}
            gb = None
            valid = _analyze_cache_valid(cache, key, files, path)
            for fn in files:
                img = QImage(os.path.join(path, fn))
                if img.isNull():
                    continue
                box = foot = None
                if valid:
                    box, foot = _analyze_cache_get(cache, key, fn)
                if box is None or foot is None:
                    box = self._content_box(img)
                    if key in config.PRE_ALIGNED:
                        foot = self._foot_center_x_narrow(img)
                    else:
                        foot = self._foot_center_x(img)
                    _analyze_cache_set(cache, key, fn, box, foot, path)
                if not box:
                    continue
                gb = box if gb is None else gb.united(box)
                act["images"].append(img)
                act["boxes"].append(box)
                act["feet"].append(foot)
            if not act["images"]:
                continue
            act["gb"] = gb
            self.actions[key] = act

        # 皮肤素材全部无效（帧缺失/损坏）→ 回退内置皮肤，避免空白/崩溃
        if not self.actions:
            from pet import skins
            if not isinstance(self._skin, skins.BuiltinSkin):
                self._skin = skins.BuiltinSkin()
                skins.apply_skin(self._skin)
                self._init_skin_ctx()
                return self._load_actions()
            # 内置皮肤也没有动画素材 → 静态模式（一张静态图，不崩、功能全保留）
            self._load_static_fallback()
            return

        # 全局包围盒决定统一窗口尺寸（固定，避免切换动作时 resize 闪烁）
        gb_all = None
        for act in self.actions.values():
            gb_all = act["gb"] if gb_all is None else gb_all.united(act["gb"])
        if gb_all is None:
            gb_all = QRect(0, 0, 1, 1)
        gw, gh = gb_all.width(), gb_all.height()
        scale = config.TARGET_HEIGHT / gh if gh >= gw else config.TARGET_HEIGHT / gw
        tw, th = max(1, round(gw * scale)), max(1, round(gh * scale))
        self._frame_size = (tw, th)

        # 第二遍：按统一比例生成每帧，内容在窗口内脚部贴底
        for key, act in self.actions.items():
            act["scale"] = scale
            act["size"] = (tw, th)
            # 加载位移动作的运动轨迹（每帧垂直偏移 dy，用于跳跃等再现真实位移）
            if key in config.PRE_ALIGNED:
                tp = os.path.join(self._assets_dir, self._actions_map[key]["dir"], "traj.json")
                if os.path.isfile(tp):
                    try:
                        with open(tp, encoding="utf-8") as _f:
                            _t = json.load(_f)
                        act["traj"] = _t if isinstance(_t, list) else None
                    except Exception:
                        act["traj"] = None

            for fi, (img, box, fc) in enumerate(zip(act["images"], act["boxes"], act["feet"])):
                crop = img.copy(box)
                sw = max(1, round(box.width() * scale))
                sh = max(1, round(box.height() * scale))
                scaled = crop.scaled(sw, sh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                px = round(tw / 2 - (fc - box.left()) * scale)
                py = th - scaled.height()
                # 位移动作：
                #   - 含 dx 的（打滚等）：内容中心放置 + (dx,dy) 中心轨迹，还原横移/起伏
                #   - 仅 dy 的（跳跃）：贴底 + 上移 dy
                rec = None
                if act.get("traj") and fi < len(act["traj"]):
                    rec = act["traj"][fi] if isinstance(act["traj"][fi], dict) else None
                if rec and "dx" in rec:
                    px = round(tw / 2 - (fc - box.left()) * scale) + round(rec["dx"] * scale)
                    py = round(th / 2 - scaled.height() / 2) + round(rec["dy"] * scale)
                elif rec:
                    py = th - scaled.height() - round(rec["dy"] * scale)
                px = max(0, min(px, tw - scaled.width()))
                py = max(0, min(py, th - scaled.height()))
                act["tops"].append(py)
                canvas = QImage(tw, th, QImage.Format_ARGB32)
                canvas.fill(0)
                painter = QPainter(canvas)
                painter.drawImage(px, py, scaled)
                painter.end()
                pm = QPixmap.fromImage(canvas)
                act["frames"].append(pm)
                if key in (ActionKey.SIDE.value, ActionKey.TURN.value):
                    act["mirrored"].append(pm.transformed(QTransform().scale(-1, 1)))
            act["mirrored"] = act["mirrored"] or None
            # 显示帧/镜像已生成，原始大图与逐帧分析中间数据不再需要，立即释放：
            # 常驻内存从 ~1.6GB(1600+张512×512) 降到 ~0.3GB，改善整体响应。
            # 素材仍在磁盘 assets/，改 TARGET_HEIGHT/scale 重建时重新读盘即可，不丢任何数据。
            act["images"] = []
            act["boxes"] = []
            act["feet"] = []
            act["gb"] = None

        _save_analyze_cache(self._analyze_cache_path, cache)

        # B1：把现场生成的基础帧落盘，供下次启动直接读取
        self._base_frames_save()

    def _load_static_fallback(self):
        """无任何有效动画帧时的**静态模式**：所有动作 key 指向同一张静态图（1 帧）。

        静态图来自 ``assets`` 扩展点（内置 assets-static 提供大肥鱼待机帧）；
        取不到时用程序绘制的极简占位。静态模式不崩、全功能可用，只是"不动"。
        """
        pm = None
        try:
            from plugin import host as plugin_host
            fig = plugin_host.static_figure()
        except Exception:
            fig = None
        if fig and os.path.isfile(fig):
            img = QImage(fig)
            if not img.isNull():
                pm = QPixmap.fromImage(img)
        if pm is None:
            pm = self._make_placeholder()
        tw, th = max(1, pm.width()), max(1, pm.height())
        self._frame_size = (tw, th)
        self._static_mode = True
        self._static_pixmap = pm          # _rebuild_frame_cache 的固定基础帧
        self.actions = {}
        for key in self._actions_map:
            self.actions[key] = {
                "images": [], "boxes": [], "frames": [pm], "mirrored": None,
                "scale": 1.0, "size": (tw, th), "gb": None, "tops": [0],
                "feet": [], "traj": None,
            }

    def _make_placeholder(self):
        """程序绘制的极简占位形象（连静态图都取不到时的最后兜底）。"""
        from PySide6.QtGui import QColor
        h = int(config.TARGET_HEIGHT)
        pm = QPixmap(h, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(120, 170, 220))
        p.setPen(Qt.NoPen)
        p.drawEllipse(int(h * 0.18), int(h * 0.18),
                      int(h * 0.64), int(h * 0.64))
        p.end()
        return pm

    def reload_skin(self, skin):
        """应用新皮肤并整体重载动作（素材目录 / 动作映射 / 帧缓存 / 窗口尺寸）。

        皮肤切换不改变人格体系，只换外观动作、动作显示名、（可选）台词/标签词/prompt。
        """
        from pet import skins
        skins.apply_skin(skin)
        self._skin = skin
        self._load_actions()
        self._init_ui()
        self._rebuild_frame_cache()
        self._place_random()
        if hasattr(self, "state"):
            self._seq_active = False
            self._seq_playing = None
            self._request_state(self.STATE_IDLE)

    # ---------- B1 基础帧磁盘缓存 ----------

    def _base_frames_save(self):
        """把当前内存里的基础帧（act['frames']/'mirrored'，未套用户画面）落盘。
        命中条件：素材未变 + 未改画面设置（画面走运行时 _rebuild_frame_cache 的 numpy，
        不入此缓存）→ 因此改画面不会触发重新读大图。异常安全：失败仅跳过缓存。"""
        try:
            fc = self._base_frames_dir
            assets, tops = {}, {}
            for key, act in self.actions.items():
                if not act.get("frames"):
                    continue
                folder = self._actions_map[key]["dir"]
                path = os.path.join(self._assets_dir, folder)
                files = sorted(
                    _list_png(path),
                    key=_frame_sort_key)
                sub = {}
                for fn in files:
                    try:
                        st = os.stat(os.path.join(path, fn))
                        sub[fn] = (st.st_size, st.st_mtime)
                    except OSError:
                        sub[fn] = None
                assets[key] = sub
                tops[key] = list(act.get("tops") or [])
                d = os.path.join(fc, key)
                os.makedirs(d, exist_ok=True)
                for i, pm in enumerate(act["frames"]):
                    if i < len(files):
                        pm.toImage().save(os.path.join(d, files[i]))
                if act.get("mirrored"):
                    dm = os.path.join(fc, key + "_m")
                    os.makedirs(dm, exist_ok=True)
                    for i, pm in enumerate(act["mirrored"]):
                        if i < len(files):
                            pm.toImage().save(os.path.join(dm, files[i]))
            index = {
                "version": config.BASE_FRAMES_VERSION,
                "target_height": config.TARGET_HEIGHT,
                "frame_size": list(self._frame_size),
                "scale": self.actions.get(next(iter(self.actions)), {}).get("scale", 1.0),
                "assets": assets,
                "tops": tops,
            }
            with open(os.path.join(fc, "index.json"), "w", encoding="utf-8") as fp:
                json.dump(index, fp, ensure_ascii=False)
        except Exception:
            pass   # 缓存写失败不影响功能，下次启动重新现场生成

    def _base_frames_load(self):
        """尝试从磁盘读取基础帧缓存；成功则填好 self.actions/_frame_size 返回 True。
        任一素材变化/帧缺失/版本不符即返回 False，交由现场生成路径兜底。"""
        try:
            fc = self._base_frames_dir
            ipath = os.path.join(fc, "index.json")
            if not os.path.isfile(ipath):
                return False
            with open(ipath, encoding="utf-8") as fp:
                index = json.load(fp)
            if index.get("version") != config.BASE_FRAMES_VERSION:
                return False
            if index.get("target_height") != config.TARGET_HEIGHT:
                return False
            fs = index.get("frame_size")
            if not fs or len(fs) != 2 or fs[0] <= 0 or fs[1] <= 0:
                return False
            # 校验素材未变化（size + mtime）
            for key in self._actions_map:
                folder = self._actions_map[key]["dir"]
                path = os.path.join(self._assets_dir, folder)
                files = sorted(
                    _list_png(path),
                    key=_frame_sort_key)
                sub = index.get("assets", {}).get(key)
                if not sub or len(sub) != len(files):
                    return False
                fd = os.path.join(fc, key)
                for fn in files:
                    rec = sub.get(fn)
                    if rec is None:
                        return False
                    try:
                        st = os.stat(os.path.join(path, fn))
                        if st.st_size != rec[0] or st.st_mtime != rec[1]:
                            return False
                    except OSError:
                        return False
                    if not os.path.isfile(os.path.join(fd, fn)):
                        return False
                if key in (ActionKey.SIDE.value, ActionKey.TURN.value):
                    if not os.path.isdir(os.path.join(fc, key + "_m")):
                        return False
            # 通过：加载基础帧
            self._frame_size = (int(fs[0]), int(fs[1]))
            all_tops = index.get("tops") or {}
            for key in self._actions_map:
                folder = self._actions_map[key]["dir"]
                path = os.path.join(self._assets_dir, folder)
                files = sorted(
                    _list_png(path),
                    key=_frame_sort_key)
                act = {"images": [], "boxes": [], "frames": [], "mirrored": [],
                       "scale": index.get("scale", 1.0), "size": tuple(fs),
                       "gb": None, "tops": list(all_tops.get(key) or []),
                       "feet": [], "traj": None}
                # 懒加载动作不预读帧（播放时按需从磁盘加载），核心动作常驻
                if key not in config.LAZY_ACTIONS:
                    d = os.path.join(fc, key)
                    for fn in files:
                        act["frames"].append(QPixmap(os.path.join(d, fn)))
                    if key in (ActionKey.SIDE.value, ActionKey.TURN.value):
                        md = os.path.join(fc, key + "_m")
                        m = []
                        for fn in files:
                            p = os.path.join(md, fn)
                            if os.path.isfile(p):
                                m.append(QPixmap(p))
                        if m:
                            act["mirrored"] = m
                act["mirrored"] = act["mirrored"] or None
                self.actions[key] = act
            return True
        except Exception:
            return False

    def _base_frames_from_disk(self, key, mirrored=False):
        """从 .base_frames/<key> 读该动作的基础帧 QPixmap 列表（未套用户画面设置）。
        懒加载动作首次播放、以及重建用户帧缓存时需要。
        QImage 读盘 + fromImage 转 QPixmap 比直接 QPixmap(path) 快（免 DIB 转换）。"""
        d = os.path.join(self._base_frames_dir, key + ("_m" if mirrored else ""))
        if not os.path.isdir(d):
            return []
        files = sorted(_list_png(d),
                       key=_frame_sort_key)
        return [QPixmap.fromImage(QImage(os.path.join(d, f))) for f in files]

    def _ensure_lazy_loaded(self, key):
        """懒加载动作按需构建用户帧并放入 _frame_cache（LRU 维护）。
        核心动作 / 已加载动作零开销；静态模式下帧已常驻，直接返回。"""
        if getattr(self, "_static_mode", False):
            return
        if key not in config.LAZY_ACTIONS:
            return
        if key in self._lazy_loaded:
            self._touch_lazy(key)
            return
        bf = self._base_frames_from_disk(key)
        if not bf:
            # 磁盘缓存缺失兜底：退化为已有 idle 帧，避免空白
            self._frame_cache[key] = self._frame_cache.get(ActionKey.IDLE.value) or []
            self._lazy_loaded.add(key)
            self._touch_lazy(key)
            return
        self._frame_cache[key] = [self._apply_user_settings(pm) for pm in bf]
        self._lazy_loaded.add(key)
        self._touch_lazy(key)
        self._evict_lazy()

    def _touch_lazy(self, key):
        if key in self._lazy_order:
            self._lazy_order.remove(key)
        self._lazy_order.insert(0, key)

    def _evict_lazy(self):
        """超出 LAZY_CACHE_ACTIONS 上限时，驱逐最久未用且非当前播放的懒加载动作。"""
        limit = config.LAZY_CACHE_ACTIONS
        while len(self._lazy_loaded) > limit:
            evicted = None
            # _lazy_order[0]=最近使用，从最旧（尾部）开始找可驱逐项
            for k in reversed(self._lazy_order):
                if k != getattr(self, "state", None):
                    evicted = k
                    break
            if evicted is None:
                break
            self._lazy_order.remove(evicted)
            self._lazy_loaded.discard(evicted)
            self._frame_cache.pop(evicted, None)
            self._frame_cache.pop(evicted + "_m", None)

    def _rebuild_frame_cache(self):
        """按当前亮度/对比度/饱和度重建显示帧缓存（设置变化时才调用，动画播放阶段零计算开销）。

        核心动作：常驻 _frame_cache，重建后释放基础帧（仅保留用户帧一份）；
        懒加载动作：已加载的从磁盘基础帧重建，未加载的留待播放时按需构建。
        """
        cache = {}
        static_pm = getattr(self, "_static_pixmap", None)
        for key, act in self.actions.items():
            if static_pm is not None:
                # 静态模式：所有动作共用静态图，从固定基础帧重建（无叠加）
                cache[key] = [self._apply_user_settings(static_pm)]
                act["frames"] = cache[key]
                act["mirrored"] = []
                continue
            if key in config.LAZY_ACTIONS:
                # 懒加载动作不常驻：释放基础帧；已加载的用磁盘基础帧重建
                if key in self._lazy_loaded:
                    bf = self._base_frames_from_disk(key)
                    if bf:
                        cache[key] = [self._apply_user_settings(pm) for pm in bf]
                act["frames"] = []
                act["mirrored"] = []
                continue
            # 核心动作：优先用内存基础帧，被替换过（_frames_are_user）则从磁盘重载
            bf = act["frames"]
            if key in self._frames_are_user:
                bf = self._base_frames_from_disk(key)
            cache[key] = [self._apply_user_settings(pm) for pm in bf]
            if act.get("mirrored"):
                bfm = act["mirrored"]
                if key in self._frames_are_user:
                    bfm = self._base_frames_from_disk(key, mirrored=True)
                cache[key + "_m"] = [self._apply_user_settings(pm) for pm in bfm]
            # 方案1：基础帧使命完成，替换为构建结果，释放旧基础帧内存（常驻仅剩一份用户帧）
            act["frames"] = cache[key]
            act["mirrored"] = cache.get(key + "_m", [])
        self._frames_are_user = set(cache.keys())
        self._frame_cache = cache
        # 让当前播放的帧列表指向新缓存（state 未初始化时跳过）
        if hasattr(self, "state"):
            self.cur_frames = self._current_frames()
            self.frame_i = min(self.frame_i, len(self.cur_frames) - 1)

    @staticmethod
    def _image_to_bgra(img):
        """把 QImage 转成可写的 BGRA numpy 数组 (h, w, 4)。"""
        img = img.convertToFormat(QImage.Format_ARGB32)
        w, h = img.width(), img.height()
        bpl = img.bytesPerLine()
        data = img.bits().tobytes()
        arr = np.ascontiguousarray(
            np.frombuffer(data, np.uint8).reshape(h, bpl)[:, :w * 4]
        ).reshape(h, w, 4)
        return arr, w, h

    @staticmethod
    def _foot_center_x(img):
        """计算角色双脚的像素水平中心（原始像素坐标）；
        取底部 20% 高度非透明像素加权中心。"""
        w0, h0 = img.width(), img.height()
        if w0 == 0 or h0 == 0:
            return 0.0
        small = img.scaled(160, 160, Qt.KeepAspectRatio, Qt.FastTransformation)
        if small.format() != QImage.Format_ARGB32:
            small = small.convertToFormat(QImage.Format_ARGB32)
        data = small.bits().tobytes()
        sw, sh = small.width(), small.height()
        stride = small.bytesPerLine()
        arr = np.frombuffer(data, np.uint8).reshape(sh, stride)
        alpha = arr[:, 3:sw * 4:4].astype(np.float32)
        region = alpha[int(sh * 0.80):, :]
        mass = float(region.sum())
        if mass == 0:
            return 0.0
        sx = float((region * np.arange(sw, dtype=np.float32)).sum())
        return sx / mass * (w0 / sw)

    @staticmethod
    def _foot_center_x_narrow(img):
        """用于 PRE_ALIGNED 动作：这类素材已在制作时整体平移，把站立中轴固定在
        素材画布中心，因此直接返回素材画布宽度中心作为脚心 —— 桌宠据此把素材
        整体位置映射到画面中心，而不逐帧算质心（那样会被尾巴/裙摆拖偏、动作内漂移）。"""
        return img.width() / 2

    @staticmethod
    def _content_box(img):
        """估算图像的透明包围盒（先缩小分析，再映射回原图坐标）。"""
        w0, h0 = img.width(), img.height()
        if w0 == 0 or h0 == 0:
            return None
        small = img.scaled(96, 96, Qt.KeepAspectRatio, Qt.FastTransformation)
        if small.format() != QImage.Format_ARGB32:
            small = small.convertToFormat(QImage.Format_ARGB32)
        data = small.bits().tobytes()
        sw, sh = small.width(), small.height()
        stride = small.bytesPerLine()
        arr = np.frombuffer(data, np.uint8).reshape(sh, stride)
        alpha = arr[:, 3:sw * 4:4]
        ys, xs = np.nonzero(alpha)
        if xs.size == 0:
            return None
        sx = w0 / sw
        sy = h0 / sh
        x = int(xs.min() * sx)
        y = int(ys.min() * sy)
        w = int((xs.max() - xs.min() + 1) * sx)
        h = int((ys.max() - ys.min() + 1) * sy)
        return QRect(x, y, w, h)

    def _init_ui(self):
        tw, th = getattr(self, "_frame_size",
                         (config.TARGET_HEIGHT, config.TARGET_HEIGHT))
        self.setFixedSize(tw, th)
        self.label = QLabel(self)
        self.label.setGeometry(0, 0, tw, th)
        self.label.setStyleSheet("background: transparent;")

    # ---------- 状态与动画 ----------

    def _current_frames(self):
        self._ensure_lazy_loaded(getattr(self, "state", ActionKey.IDLE.value))
        c = self._frame_cache
        if self.state == self.STATE_TURN:
            return (c.get(ActionKey.TURN.value if self.turn_right else ActionKey.TURN.value + "_m")
                    or c.get(ActionKey.TURN.value) or c.get(ActionKey.IDLE.value))
        if self.state in (self.STATE_WALK, self.STATE_DRAG):
            # 侧面素材角色默认朝左：向左走用原图，向右走用镜像
            left = self.walking_left if self.state == self.STATE_WALK else self._drag_left
            return (c.get(ActionKey.SIDE.value if left else ActionKey.SIDE.value + "_m")
                    or c.get(ActionKey.SIDE.value) or c.get(ActionKey.IDLE.value))
        return c.get(self.state) or c.get(ActionKey.IDLE.value)

    def _set_state(self, state, walking_left=None, turn_right=None):
        if state == self.STATE_WALK and walking_left is not None:
            self.walking_left = walking_left
        if state == self.STATE_DRAG:
            self._drag_left = walking_left if walking_left is not None else self.walking_left
        if state == self.STATE_TURN and turn_right is not None:
            self.turn_right = turn_right
        self.state = state
        self.frame_i = 0
        self.cur_frames = self._current_frames()
        self._show_frame()
        self.anim_timer.start(config.ANIM_INTERVAL)
        if state == self.STATE_IDLE:
            self._schedule_walk()
            self._schedule_chatline()
        else:
            self._unschedule_walk()
            self._chatline_timer.stop()
            if state == self.STATE_ANGRY:
                self._say_random("angry")
            elif state == self.STATE_BACK:
                self._say_random("back")
            elif state == self.STATE_SIT:
                self._say_random("sit")
            elif state == self.STATE_RISE:
                self._say_random("rise")

    def _posture_of(self, state):
        if state in self._SIT_POSTURE:
            return "sit"
        if state in self._STAND_POSTURE:
            return "stand"
        return None

    def _request_state(self, target, **kw):
        """请求切换到目标状态，规划过渡链（坐站过渡 / 正面侧面转向）并依次执行。"""
        # 目标为懒加载动作时预先加载帧（避免过渡动作播完才等磁盘读盘）
        self._ensure_lazy_loaded(target)
        self._chain = self._build_chain(target, **kw)
        self._run_chain()

    def _build_chain(self, target, **kw):
        """构建从当前状态到目标状态的过渡步骤链。"""
        steps = []
        cur = self.state
        cur_p = self._posture_of(cur)
        tgt_p = self._posture_of(target)

        if cur_p is None:
            # 当前正处于过渡中，直接切到目标
            return [("state", target, kw)]

        # 若从侧面(walk)出发且目标不是 walk，先转向回正面
        if cur == self.STATE_WALK and target != self.STATE_WALK:
            steps.append(("action", self.STATE_TURN, {"turn_right": self.walking_left}))
            cur = self.STATE_FRONT
            cur_p = "stand"

        # 坐姿/站姿组间过渡
        if cur_p != tgt_p:
            if cur_p == "sit":
                steps.append(("action", self.STATE_RISE, {}))
                cur_p = "stand"
                cur = self.STATE_FRONT
            else:
                steps.append(("action", self.STATE_SIT, {}))
                cur_p = "sit"
                cur = self.STATE_IDLE

        # 站姿内部：正面 -> 侧面（走路）需要转向
        if (cur_p == "stand" and cur == self.STATE_FRONT
                and target == self.STATE_WALK):
            steps.append(("action", self.STATE_TURN,
                          {"turn_right": not kw.get("walking_left", False)}))

        steps.append(("state", target, kw))
        return steps

    def _run_chain(self):
        if not self._chain:
            return
        step_type, state, kw = self._chain[0]
        self._chain = self._chain[1:]
        if step_type == "state":
            self._set_state(state, **kw)
            if state == self.STATE_WALK:
                self.walk_timer.start(config.WALK_STEP_MS)
        else:
            # 播放过渡动作（sit / rise / turn）
            self._set_state(state, **kw)

    def _show_frame(self):
        if self.cur_frames:
            base = self.cur_frames[self.frame_i % len(self.cur_frames)]
            self.label.setPixmap(base)   # 帧已预缓存，直接显示
        if getattr(self, "_bubble", None) is not None and self._bubble.isVisible():
            self._position_bubble()

    def _apply_user_settings(self, pm):
        """按用户设置的亮度/对比度/饱和度调整显示帧。默认值时短路，零开销。"""
        b_, c_, s_ = self.brightness, self.contrast, self.saturation
        if b_ == 0 and c_ == 1.0 and s_ == 1.0:
            return pm
        arr, w, h = self._image_to_bgra(
            pm.toImage().convertToFormat(QImage.Format_ARGB32))
        f = arr[:, :, :3].astype(np.float32)          # B,G,R
        if c_ != 1.0 or b_ != 0:
            # 对比度 + 亮度：f = f*c + (128*(1-c) + b)（合并常数，省一次 (f-128)*c）
            f = f * c_ + (128.0 * (1.0 - c_) + b_)
        if s_ != 1.0:
            gray = (0.299 * f[:, :, 2] + 0.587 * f[:, :, 1]
                    + 0.114 * f[:, :, 0])[..., None]  # Y
            f = f * s_ + gray * (1.0 - s_)            # 饱和度（避免中间 (f-gray) 大数组）
        np.clip(f, 0, 255, out=f)
        out = np.empty((h, w, 4), np.uint8)
        out[:, :, 0:3] = f.astype(np.uint8)
        out[:, :, 3] = arr[:, :, 3]
        return QPixmap.fromImage(
            QImage(out.data, w, h, w * 4, QImage.Format_ARGB32).copy())

    # ---------- 动画推进 ----------

    def _advance_frame(self):
        n = len(self.cur_frames)
        if not n:
            return
        self.frame_i = (self.frame_i + 1) % n
        self._show_frame()
        if self.frame_i != 0:
            return
        if self.state in (self.STATE_SIT, self.STATE_RISE, self.STATE_TURN):
            # 过渡动作播完，继续执行过渡链下一步
            self._run_chain()
            return
        if self._seq_active:
            if self.state == self._seq_playing:
                self._loops_left -= 1
                if self._loops_left <= 0:
                    self._play_seq_next()
            return
        if self.state in self._ONESHOT:
            self._request_state(self.STATE_IDLE)

    def _play_oneshot(self, state):
        self._seq_active = False
        self._seq_playing = None
        self._request_state(state)

    # ---------- 动作序列 ----------

    # 一套连贯动作：正面 -> 坐下 -> 坐地上生气 -> 起身 -> 正面
    ACTION_SEQUENCE = [
        (ActionKey.FRONT.value, 2),
        (ActionKey.ANGRY.value, 1),
        (ActionKey.FRONT.value, 2),
    ]

    def _play_sequence(self, seq=None):
        self._seq = seq or self.ACTION_SEQUENCE
        self._seq_i = 0
        self._seq_active = True
        self.walk_timer.stop()
        self._play_seq_next()

    def _play_seq_next(self):
        if self._seq_i < len(self._seq):
            act, loops = self._seq[self._seq_i]
            self._seq_i += 1
            self._loops_left = loops
            self._seq_playing = act
            self._request_state(act)
        else:
            self._seq_active = False
            self._seq_playing = None
            self._request_state(self.STATE_IDLE)

    # ---------- 巡逻 ----------

    def _schedule_walk(self):
        delay = random.randint(config.IDLE_MIN_MS, config.IDLE_MAX_MS)
        self.idle_timer.start(delay)

    def _unschedule_walk(self):
        self.idle_timer.stop()

    def _start_walk(self):
        # 游戏模式下宠物原地不动，避免干扰玩家操作（Minecraft / 修仙皆然）
        if self._game_type in (1, 2) or self.state != self.STATE_IDLE:
            return
        left = random.choice([True, False])
        dist = random.randint(config.WALK_MIN_DIST, config.WALK_MAX_DIST)
        screen = self._screen_geometry()
        if left:
            target = self.x() - dist
            if target < screen.left():
                target = screen.left()
        else:
            target = self.x() + dist
            if target + self.width() > screen.right():
                target = screen.right() - self.width()
        if target == self.x():
            self._schedule_walk()
            return
        self.walk_target_x = target
        self._say_random("walk")
        self._request_state(self.STATE_WALK, walking_left=left)

    def _walk_tick(self):
        if self.state != self.STATE_WALK:
            self.walk_timer.stop()
            return
        step = config.WALK_SPEED
        if not self.walking_left:
            step = -step
        nx = self.x() - step
        done = False
        if self.walking_left and nx <= self.walk_target_x:
            nx, done = self.walk_target_x, True
        elif not self.walking_left and nx >= self.walk_target_x:
            nx, done = self.walk_target_x, True
        self.move(nx, self.y())
        if done:
            self.walk_timer.stop()
            self._say_random("walk_back")
            self._request_state(self.STATE_IDLE)

    # ---------- 屏幕工具 ----------

    def _keep_topmost(self):
        """周期强制置顶：WindowStaysOnTopHint 之外，再用原生 API 设为 TOPMOST。

        使用 NOACTIVATE 不抢焦点，避免游戏模式下打断玩家操作。
        注意：独占全屏游戏（如 MC 默认全屏）会覆盖一切窗口，需将游戏设为
        「窗口化全屏 / 无边框」才能让桌宠显示在其上层。
        """
        if not self.isVisible():
            return
        try:
            self.raise_()
            import ctypes
            hwnd = int(self.winId())
            ctypes.windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0010)   # NOSIZE|NOMOVE|NOACTIVATE
        except Exception:
            pass

    def _screen_geometry(self):
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        return screen.availableGeometry()

    def _place_random(self):
        screen = self._screen_geometry()
        x = random.randint(screen.left(), max(screen.left(), screen.right() - self.width()))
        y = random.randint(screen.top() + screen.height() // 3,
                           max(screen.top(), screen.bottom() - self.height()))
        self.move(x, y)

    # ---------- 鼠标交互 ----------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_pos = event.globalPosition().toPoint()
            self._press_moved = False
            self._set_state(self.STATE_DRAG,
                            walking_left=self._press_pos.x() < self.frameGeometry().center().x())

    def mouseMoveEvent(self, event):
        if self.state != self.STATE_DRAG:
            return
        if event.buttons() & Qt.LeftButton:
            pos = event.globalPosition().toPoint()
            if (pos - self._press_pos).manhattanLength() > 4:
                self._press_moved = True
            new_left = pos.x() - self._drag_offset.x()
            if new_left < self.x():
                self._drag_left = True
            elif new_left > self.x():
                self._drag_left = False
            self.move(pos - self._drag_offset)
            self.cur_frames = self._current_frames()
            self.frame_i = min(self.frame_i, len(self.cur_frames) - 1)
            self._show_frame()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.state == self.STATE_DRAG:
            if not self._press_moved:
                self._on_click()
            else:
                self._say_random("drag")
                self._request_state(self.STATE_IDLE)

    def _on_click(self):
        self._say_random("click")
        if random.random() < 0.3:
            self._play_sequence()
        else:
            reaction = random.choice(["front", "angry", "back"])
            self._play_oneshot(reaction)

    # ---------- 状态归位 ----------

    def _go_idle(self):
        self._seq_active = False
        self._seq_playing = None
        self._request_state(self.STATE_IDLE)

    def _hide_then_show(self):
        self._say_random("hide")
        self.hide()
        QTimer.singleShot(5000, self._show_after_hide)

    def _show_after_hide(self):
        self.show()
        self._say_random("welcome", force=True)