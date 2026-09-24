# -*- coding: utf-8 -*-
"""桌宠性能与延迟监测工具。

对桌宠（desktop-pet）的启动、首次拖动、首次点击、首次右键菜单、语音预热、
懒加载动作预加载等环节做分项延迟测量，用于排查「第一次操作卡顿」类问题。

用法：
    python tools/perf_monitor.py             # 完整性能监测（offscreen，不弹窗）
    python tools/perf_monitor.py --smoke     # 冒烟：真实启动桌宠 3 秒自动退出（同 main.py --smoke）

说明：
- 监测会构造完整 PetWindow；若动作素材有变更（base_frames 缓存失效），首次会现场
  重建基础帧缓存（较慢一次，之后秒开），报告中「启动」一项会包含该耗时。
- 分项若超过阈值（默认 200ms）会以 [慢] 标注，便于快速定位卡顿环节。
"""
import os
import subprocess
import sys
import time

# 监测模式用离屏平台，避免弹窗、可自动运行
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

SLOW_MS = 200   # 单项延迟超过该值标注 [慢]


def _fmt(ms):
    return f"{ms:8.1f} ms"


def _line(title, ms, extra=""):
    mark = ""
    if ms > SLOW_MS:
        mark = "  [慢]"
    print(f"  {title:<34}{_fmt(ms)}{mark}{extra}")
    return ms


def measure_all():
    from PySide6.QtWidgets import QApplication

    app = QApplication([])

    print("=" * 72)
    print("桌宠性能与延迟监测")
    print("=" * 72)

    # ---------- 启动 ----------
    print("\n[1] 启动")
    t0 = time.perf_counter()
    from pet.window import PetWindow
    pet = PetWindow()
    _line("PetWindow 构造(含加载全部动作)", (time.perf_counter() - t0) * 1000)

    mode = str(pet._store.value("ai_voice_mode", "off") or "off")
    print(f"    语音模式: {mode!r}    画面设置: "
          f"b={pet.brightness} c={pet.contrast} s={pet.saturation}")

    # ---------- 语音预热前后对比 ----------
    print("\n[2] 语音（首次说话是否卡顿，语音关闭时跳过）")
    if mode in ("edge", "local", "cosy"):
        t0 = time.perf_counter()
        pet._say_random("click")
        _line("预热前首次 _say_random(click)", (time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        pet._warm_voice()
        _line("_warm_voice 预热语音", (time.perf_counter() - t0) * 1000,
              "  (后台已完成则≈0)")
        t0 = time.perf_counter()
        pet._say_random("click")
        _line("预热后首次 _say_random(click)", (time.perf_counter() - t0) * 1000)
    else:
        print("    语音未开启，跳过语音预热测量")

    # ---------- 首次拖动 ----------
    print("\n[3] 首次拖动")
    t0 = time.perf_counter()
    pet._set_state(pet.STATE_DRAG, walking_left=False)
    _line("按下 _set_state(STATE_DRAG)", (time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    pet.cur_frames = pet._current_frames()
    pet._show_frame()
    _line("拖动移动取帧+显示", (time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    pet._request_state(pet.STATE_IDLE)
    _line("松手回待机", (time.perf_counter() - t0) * 1000)

    # ---------- 首次点击 ----------
    print("\n[4] 首次点击(未移动松手)")
    t0 = time.perf_counter()
    pet._on_click()
    _line("_on_click(台词+动作/序列)", (time.perf_counter() - t0) * 1000)

    # ---------- 首次右键菜单 + 懒加载预加载 ----------
    print("\n[5] 首次右键菜单与懒加载动作")
    from PySide6.QtWidgets import QMenu
    t0 = time.perf_counter()
    menu = QMenu(pet)
    pet._build_action_menu(menu)
    _line("构建右键动作菜单", (time.perf_counter() - t0) * 1000)

    print("    —— 菜单打开期间预加载的懒加载动作 ——")
    pet._menu_preload_keys = [
        k for k in pet._MENU_PRELOAD_ORDER
        if k in pet._actions_map and k not in pet._lazy_loaded]
    for k in list(pet._menu_preload_keys):
        t0 = time.perf_counter()
        pet._ensure_lazy_loaded(k)
        _line(f"首次加载 {k}", (time.perf_counter() - t0) * 1000)
    if not pet._menu_preload_keys:
        print("    (全部已在启动预加载中就绪，0ms)")

    # ---------- 首次播放懒加载动作 ----------
    print("\n[6] 首次播放懒加载动作(模拟点菜单项)")
    from core.action_key import ActionKey
    for k in (ActionKey.THINK.value, ActionKey.HAPPY.value,
              ActionKey.JUMP.value, ActionKey.DANCE.value):
        if k not in pet._actions_map:
            continue
        t0 = time.perf_counter()
        pet._request_state(getattr(pet, "STATE_" + k.upper()))
        _line(f"首次请求 {k}", (time.perf_counter() - t0) * 1000)

    # ---------- 启动预加载状态 ----------
    print("\n[7] 启动后空闲期预加载(新增: 解决首次右键/播放卡顿)")
    print(f"    常用懒加载动作: {pet._MENU_PRELOAD_ORDER}")
    print(f"    已加载: {sorted(pet._lazy_loaded)}")
    pet._start_lazy_warm()
    while pet._lazy_warm_keys:
        pet.state = pet.STATE_IDLE
        t0 = time.perf_counter()
        pet._lazy_warm_tick()
        _line(f"空闲期预加载 {pet._lazy_warm_keys and '下一动作' or ''}",
              (time.perf_counter() - t0) * 1000,
              f"  (剩余 {len(pet._lazy_warm_keys)})")
    print("    启动空闲预加载完成:", sorted(pet._lazy_loaded))

    pet.close()
    print("\n" + "=" * 72)
    print("监测完成。单项 > %dms 即为明显卡顿，优先优化该类环节。" % SLOW_MS)
    print("=" * 72)


def run_smoke():
    """真实冒烟：启动桌宠 3 秒自动退出，验证可正常启动运行。"""
    print("冒烟：python main.py --smoke（启动桌宠 3 秒自动退出）…")
    t0 = time.perf_counter()
    rc = subprocess.call(
        [sys.executable, os.path.join(BASE, "main.py"), "--smoke"],
        cwd=BASE)
    dt = (time.perf_counter() - t0) * 1000
    print(f"冒烟结束 rc={rc}，总耗时 {dt / 1000:.1f}s "
          f"{'[OK]' if rc == 0 else '[失败]'}")
    return rc


def main():
    if "--smoke" in sys.argv:
        sys.exit(run_smoke() or 0)
    measure_all()


if __name__ == "__main__":
    main()
