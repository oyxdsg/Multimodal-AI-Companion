"""一键回滚设置对话框的 WorkBuddy 风格改版。

用法（在 desktop-pet 目录下）：
    python tools/restore_wbs_ui.py          # 列出可用备份并提示
    python tools/restore_wbs_ui.py 20260918_183542   # 按时间戳回滚

备份命名规则与本仓库既有约定一致：<原文件>.bak_<YYYYmmdd_HHMMSS>。
本脚本只做「从 .bak 复制回原文件」，不删任何东西。
注意：仓库当前工作区有大量未提交改动，**不要用 git checkout 回滚**，
那会连带丢掉其它未提交的工作。

不在回滚范围内：`config.VERSION`（本次 2.21.0 → 2.23.0）与
`core/icons.py` / `core/widgets.py` 的新增（惰性代码，主题还原后无人调用，不影响外观）。
如需连版本号一起还原，手动把 `config.py` 的 VERSION 改回 `2.21.0` 即可。
"""

import glob
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 本次改版涉及的文件（相对 desktop-pet/）
TARGETS = [
    r"core\theme.py",
    r"pet\settings_dialog.py",
]


def list_backups():
    found = {}
    for rel in TARGETS:
        pattern = os.path.join(BASE, rel) + ".bak_*"
        for path in sorted(glob.glob(pattern)):
            ts = path.rsplit(".bak_", 1)[1]
            found.setdefault(ts, []).append((rel, path))
    return found


def main():
    found = list_backups()
    if not found:
        print("没有找到任何 .bak_* 备份，无法回滚。")
        return 1

    if len(sys.argv) < 2:
        newest = max(found)
        print("可用备份（时间戳 -> 文件数）：")
        for ts in sorted(found, reverse=True):
            rels = ", ".join(r for r, _ in found[ts])
            mark = "  ← 最新，回滚 UI 改版请用这个" if ts == newest else ""
            print("  %s  %d 个文件  [%s]%s" % (ts, len(found[ts]), rels, mark))
        print("\n回滚命令：  python tools/restore_wbs_ui.py %s" % newest)
        print("注意：更早的时间戳会把那一轮之前的工作一并还原，别随手选旧的。")
        return 0

    ts = sys.argv[1]
    if ts not in found:
        print("没有该时间戳的备份：%s" % ts)
        print("可用的：%s" % ", ".join(sorted(found, reverse=True)))
        return 1

    newest = max(found)
    if ts != newest and "--force" not in sys.argv:
        print("⚠️  %s 不是最新备份（最新是 %s）。" % (ts, newest))
        print("    选更早的时间戳会把那一轮之前的改动一并还原掉。")
        print("    确认要这么做，就加 --force 重跑：")
        print("      python tools/restore_wbs_ui.py %s --force" % ts)
        return 2

    for rel, backup in found[ts]:
        target = os.path.join(BASE, rel)
        shutil.copy2(backup, target)
        print("已还原  %s  <-  %s" % (rel, os.path.basename(backup)))

    print("\n回滚完成。重启桌宠后生效。")
    print("提示：core/theme.py 里新增的 WBS_* 区块已被整体还原，")
    print("      不会残留半套令牌。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
