# -*- coding: utf-8 -*-
"""定位 .minecraft 目录（纯标准库，供「诊断工具」下各脚本共用）。

解析顺序：
  1. 环境变量 ``DESKPET_MC``
  2. 本目录下的 ``mc_path.txt``（首行；``#`` 开头视为注释；**该文件不入库**）
  3. ``%APPDATA%\\.minecraft``
  4. 当前工作目录下的 ``.minecraft``

都找不到时返回最佳猜测（调用方自行判断目录是否存在）。
所有脚本也都支持命令行传参覆盖。
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_PATH_FILE = os.path.join(_HERE, "mc_path.txt")


def _from_file():
    """读本目录 mc_path.txt 的第一行有效路径。"""
    try:
        with open(_PATH_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip().strip('"').strip("'")
                if line and not line.startswith("#"):
                    return os.path.expanduser(line)
    except OSError:
        pass
    return None


def candidates():
    """按优先级返回候选 .minecraft 目录（已去重）。"""
    out = []
    env = os.environ.get("DESKPET_MC")
    if env:
        out.append(os.path.expanduser(env))
    from_file = _from_file()
    if from_file:
        out.append(from_file)
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(os.path.join(appdata, ".minecraft"))
    out.append(os.path.join(os.getcwd(), ".minecraft"))

    seen, uniq = set(), []
    for d in out:
        key = os.path.normcase(os.path.abspath(d))
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    return uniq


def first():
    """第一个真实存在的候选；都不存在时返回第一个候选（便于打印报错提示）。"""
    cands = candidates()
    for d in cands:
        if os.path.isdir(d):
            return d
    return cands[0] if cands else os.path.join(".", ".minecraft")


def read_text(path):
    """读文本文件，自动在 UTF-8 / GBK 之间回退。

    **必须用它读 Minecraft 的日志**：中文 Windows 上 `latest.log` 是 **GBK** 编码，
    用 UTF-8 硬读会把中文聊天内容变成乱码（工具的核心输出就废了）。
    """
    with open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")
