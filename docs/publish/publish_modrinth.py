#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把模组发布到 Modrinth（建项目 / 传版本 / 提审）。支持两个项目：

用法（token 从环境变量 MODRINTH_TOKEN 读取，或在 --token 传入）：
  python docs/publish/publish_modrinth.py create                     # 默认 smartmaid：建 draft + 传 0.1.0 + 传截图
  python docs/publish/publish_modrinth.py create --project deskpet-mod
  python docs/publish/publish_modrinth.py gallery                    # 只补传 gallery 截图
  python docs/publish/publish_modrinth.py submit                     # draft -> 提交审核/公开
  python docs/publish/publish_modrinth.py version --jar path/to.jar --number 0.1.1
  python docs/publish/publish_modrinth.py status

仅用标准库。网络默认走环境变量代理；连不上时自动回退 127.0.0.1:7890（Clash 常用端口）。
结果落盘 docs/publish/publish_modrinth_result.json（PowerShell 会吞 stdout，以文件为准）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid

BASE = "https://api.modrinth.com/v2"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))            # docs/publish -> 仓库根
MAID = r"C:\Users\86187\Desktop\女仆项目开发"
RESULT_FILE = os.path.join(HERE, "publish_modrinth_result.json")
# 已建项目的 id 台账：**草稿项目用 slug 查不到**（GET /project/<slug> 对 draft 一律 404），
# 所以建完必须记下 id，后续 gallery / submit / status 才有办法定位。
STATE_FILE = os.path.join(HERE, "publish_modrinth_state.json")
FALLBACK_PROXY = "http://127.0.0.1:7890"

PROJECTS = {
    # 女仆模组（当前主推）
    "smartmaid": {
        "slug": "smartmaid",
        "title": "Smart Maid",
        "summary": ("A rule-driven AI maid: she follows, fights, jumps gaps and really "
                    "does your chores. Optional AI chat & voice via the free DeskPet companion."),
        "categories": ["adventure", "mobs", "game-mechanics"],
        "game_versions": ["26.2"],
        "loaders": ["fabric"],
        "license_id": "MIT",
        "source_url": "https://github.com/oyxdsg/SmartMaid",
        "issues_url": "https://github.com/oyxdsg/SmartMaid/issues",
        "body": os.path.join(HERE, "modrinth-description-smartmaid.md"),
        "icon": os.path.join(MAID, "SmartMaid", "docs", "images", "smartmaid-icon.png"),
        "jar": os.path.join(MAID, "SmartMaid", "build", "libs", "smartmaid-0.1.0.jar"),
        "version": "0.1.0",
        "title_number": "Smart Maid 0.1.0",
        "changelog": ("First release: rule-driven AI maid for Minecraft 26.2 (Fabric) — "
                      "combat, jump pathfinding, chores, 41-slot inventory, wooden settings menu."),
        # fabric.mod.json 的 environment 是 "*"（main + client 入口点都有）→ 双端都要装
        "environment": "client_and_server",
        "gallery": [
            (os.path.join(MAID, "SmartMaid", "docs", "images", "smartmaid-inventory-gui.png"),
             "41-slot inventory", True),
            (os.path.join(MAID, "SmartMaid", "docs", "images", "smartmaid-settings-panel.png"),
             "Right-click menu — wooden GUI, no commands needed", False),
            (os.path.join(MAID, "SmartMaid", "docs", "images", "smartmaid-chat-bubble.png"),
             "In-game chat bubble (works with the optional DeskPet companion)", False),
        ],
    },
    # 桌宠联动模组（暂缓发布，物料保留）
    "deskpet-mod": {
        "slug": "deskpet-mod",
        "title": "DeskPet Mod",
        "summary": ("Companion mod for the DeskPet desktop AI pet - it lets your desktop pet "
                    "see what happens in your world."),
        "categories": ["utility", "decoration", "game-mechanics"],
        "game_versions": ["26.2"],
        "loaders": ["fabric"],
        "license_id": "MIT",
        "source_url": "https://github.com/oyxdsg/Multimodal-AI-Companion",
        "issues_url": "https://github.com/oyxdsg/Multimodal-AI-Companion/issues",
        "body": os.path.join(HERE, "modrinth-description.md"),
        "icon": os.path.join(REPO, "docs", "images", "deskpet-mod-icon.png"),
        "jar": os.path.join(REPO, "deskpet-mod", "build", "libs", "deskpet-mod-2.0.0.jar"),
        "version": "2.0.0",
        "title_number": "DeskPet Mod 2.0.0",
        "changelog": ("First release: game event collection + local building recognition "
                      "for Minecraft 26.2 (Fabric)."),
        "environment": "client_and_server",
        "gallery": [],
    },
}
DEFAULT_PROJECT = "smartmaid"


def load_body(path: str) -> str:
    """读取正文。文件格式：说明头 + ```markdown 围栏包住的真实正文。
    有围栏取围栏内内容（说明头是给人看的，不上传）；无围栏取全文。"""
    t = open(path, "r", encoding="utf-8").read()
    m = re.search(r"^```(?:markdown|md)\s*\n(.*?)\n```\s*$", t, re.S | re.M)
    return (m.group(1) if m else t).strip() + "\n"


def save_state(project: str, pid: str, slug: str) -> None:
    st = {}
    if os.path.isfile(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
    st[project] = {"id": pid, "slug": slug}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def load_state(project: str) -> dict:
    if not os.path.isfile(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f).get(project, {}) or {}
    except (OSError, ValueError):
        return {}


def resolve_project(token: str, cfg: dict, proxy, explicit_id: str | None = None):
    """定位项目。草稿项目 slug 查不到 → 退回台账里记的 id；也可用 --project-id 显式指定。"""
    pid = explicit_id or load_state(cfg["slug"]).get("id")
    if pid:
        proj = get_project(token, pid, proxy)
        if proj:
            return proj
        log("    台账里的 id=%s 查不到（token 是否有 PROJECT_READ 权限？）" % pid)
    return get_project(token, cfg["slug"], proxy)


def log(msg: str) -> None:
    print(msg, flush=True)


def finish(payload: dict, code: int = 0) -> int:
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log("结果已写入 %s" % RESULT_FILE)
    return code


def http(method: str, path: str, token: str, body: bytes | None = None,
         content_type: str | None = None, proxy: str | None = None,
         accept_404: bool = False):
    """返回 (status, json)。连接失败自动回退代理重试一次。"""
    last_err: Exception | None = None
    for attempt, px in enumerate(_proxy_chain(proxy)):
        url = BASE + path
        handlers = []
        if px:
            handlers.append(urllib.request.ProxyHandler({"http": px, "https": px}))
        opener = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(url, data=body, method=method)
        if token:
            req.add_header("Authorization", token)
        if content_type:
            req.add_header("Content-Type", content_type)
        req.add_header("User-Agent", "oyxdsg-publish/1.0 (github.com/oyxdsg)")
        try:
            with opener.open(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if e.code == 404 and accept_404:
                return 404, {}
            raise RuntimeError("HTTP %s %s -> %s\n%s" % (method, path, e.code, detail)) from None
        except Exception as e:  # URLError / timeout / proxy refused
            last_err = e
            log("  连接失败（%s），%s" % (e, "换代理重试" if attempt == 0 else "放弃"))
    raise RuntimeError("网络不可达：%s" % last_err)


def _proxy_chain(explicit: str | None):
    env = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") \
        or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    if explicit:
        return [explicit]
    if env:
        return [None]  # urllib 默认已读环境变量
    return [None, FALLBACK_PROXY]


def multipart(fields: dict, files: dict):
    """fields: name->str；files: name->(filename, bytes, content_type)。"""
    boundary = "----wbb" + uuid.uuid4().hex
    chunks: list[bytes] = []

    def add(s: str):
        chunks.append(s.encode("utf-8"))

    for name, val in fields.items():
        add("--" + boundary)
        add('Content-Disposition: form-data; name="%s"' % name)
        add("")
        add(val)
    for name, (fname, data, ctype) in files.items():
        add("--" + boundary)
        add('Content-Disposition: form-data; name="%s"; filename="%s"' % (name, fname))
        add("Content-Type: %s" % ctype)
        add("")
        chunks.append(data)
        chunks.append(b"\r\n")
    add("--" + boundary + "--")
    add("")
    return boundary, b"\r\n".join(chunks)


def send_form(method: str, path: str, token: str, fields: dict, files: dict,
              proxy: str | None):
    boundary, body = multipart(fields, files)
    return http(method, path, token, body,
                "multipart/form-data; boundary=" + boundary, proxy)


def get_project(token: str, slug_or_id: str, proxy):
    st, data = http("GET", "/project/" + slug_or_id, token, accept_404=True)
    return data if st == 200 else None


def project_id_of(token: str, slug: str, proxy) -> str | None:
    proj = get_project(token, slug, proxy)
    return proj.get("id") if proj else None


def do_create(args, token: str, cfg: dict) -> int:
    proxy = args.proxy
    if resolve_project(token, cfg, proxy, args.project_id):
        log("项目 %s 已存在，改用 `version` / `gallery` / `submit` 子命令。" % cfg["slug"])
        return finish({"error": "project exists", "slug": cfg["slug"]}, 1)

    body = load_body(args.body)
    with open(args.icon, "rb") as f:
        icon_bytes = f.read()

    project = {
        "slug": cfg["slug"],
        "title": cfg["title"],
        "description": cfg["summary"],
        "body": body,
        "categories": cfg["categories"],
        "client_side": "required",
        "server_side": "optional",
        "license_id": cfg["license_id"],
        "source_url": cfg["source_url"],
        "issues_url": cfg["issues_url"],
        "project_type": "mod",
        "is_draft": True,   # Modrinth 官方建议恒为 true：先草稿，确认后 submit
        # 该字段官方标注 Deprecated，但 schema 仍要求存在 → 传空数组，
        # 版本另行用 POST /version 上传（见 do_version_upload）。
        "initial_versions": [],
    }
    log("[1/4] 创建项目（draft）…… %s" % cfg["slug"])
    st, proj = send_form("POST", "/project", token,
                         {"data": json.dumps(project)},
                         {"icon": (os.path.basename(args.icon), icon_bytes, "image/png")},
                         proxy)
    pid = proj["id"]
    save_state(args.project, pid, proj.get("slug") or cfg["slug"])
    log("    项目 id=%s  slug=%s  status=%s" % (pid, proj.get("slug"), proj.get("status")))

    log("[2/4] 上传版本 %s ……" % args.number)
    ver = do_version_upload(token, pid, args, cfg, proxy)
    log("    版本 id=%s" % ver["id"])

    log("[3/4] 上传 gallery 截图 ……")
    gal = do_gallery(token, pid, cfg, proxy)
    log("    共 %d 张" % len(gal))

    log("[4/4] 完成。草稿地址（浏览器登录后可见）:")
    log("    https://modrinth.com/project/%s" % cfg["slug"])
    log("    确认页面没问题后运行: python %s submit --project %s"
        % (os.path.basename(__file__), args.project))
    return finish({"project": proj, "version": ver, "gallery": gal}, 0)


def do_version_upload(token: str, project_id: str, args, cfg: dict, proxy) -> dict:
    fa_id = project_id_of(token, "fabric-api", proxy)
    deps = []
    if fa_id:
        deps.append({"project_id": fa_id, "dependency_type": "required"})
    else:
        log("    ! 没拿到 fabric-api 的 project id，依赖关系需在网页上手动补")
    with open(args.jar, "rb") as f:
        jar_bytes = f.read()
    data = {
        "name": args.title or cfg["title_number"],
        "version_number": args.number,
        "changelog": args.changelog,
        "dependencies": deps,
        "game_versions": cfg["game_versions"],
        "version_type": "release",
        "loaders": cfg["loaders"],
        "featured": True,
        "status": "listed",
        "project_id": project_id,
        "file_parts": ["file"],
        "primary_file": "file",
        # 不传这个字段端侧会显示 unknown（页面上的「客户端/服务端」标签与筛选依赖它）
        "environment": cfg.get("environment", "client_and_server"),
    }
    st, ver = send_form("POST", "/version", token,
                        {"data": json.dumps(data)},
                        {"file": (os.path.basename(args.jar), jar_bytes,
                                  "application/java-archive")},
                        proxy)
    return ver


def do_gallery(token: str, project_id: str, cfg: dict, proxy) -> list:
    """上传 gallery 截图（POST /project/<id>/gallery，一次一张，multipart）。"""
    added: list = []
    for order, (path, title, featured) in enumerate(cfg.get("gallery") or []):
        if not os.path.isfile(path):
            log("    ! 跳过不存在的截图：%s" % path)
            continue
        with open(path, "rb") as f:
            img = f.read()
        _, item = send_form("POST", "/project/%s/gallery" % project_id, token,
                            {"featured": "true" if featured else "false",
                             "title": title,
                             "ordering": str(order)},
                            {"file": (os.path.basename(path), img, "image/png")},
                            proxy)
        log("    gallery[%d] %s -> %s" % (order, title, item.get("url")))
        added.append(item)
    return added


def do_version(args, token: str, cfg: dict) -> int:
    proxy = args.proxy
    proj = resolve_project(token, cfg, proxy, args.project_id)
    if not proj:
        log("项目不存在，先运行 create（草稿项目 slug 查不到，请用 --project-id 指定）。")
        return finish({"error": "no project"}, 1)
    ver = do_version_upload(token, proj["id"], args, cfg, proxy)
    log("版本已上传：id=%s  %s" % (ver["id"], ver.get("version_number")))
    return finish({"version": ver}, 0)


def do_gallery_cmd(args, token: str, cfg: dict) -> int:
    proxy = args.proxy
    proj = resolve_project(token, cfg, proxy, args.project_id)
    if not proj:
        log("项目不存在，先运行 create（草稿项目 slug 查不到，请用 --project-id 指定）。")
        return finish({"error": "no project"}, 1)
    items = do_gallery(token, proj["id"], cfg, proxy)
    log("共上传 %d 张截图。" % len(items))
    return finish({"project": proj, "gallery": items}, 0)


def do_submit(args, token: str, cfg: dict) -> int:
    proxy = args.proxy
    proj = resolve_project(token, cfg, proxy, args.project_id)
    if not proj:
        log("项目不存在（草稿项目 slug 查不到，请用 --project-id 指定）。")
        return finish({"error": "no project"}, 1)
    log("当前 status=%s，提交审核（draft -> approved）……" % proj.get("status"))
    body = json.dumps({"status": "approved"}).encode("utf-8")
    st, updated = http("PATCH", "/project/%s" % proj["id"], token, body, "application/json", proxy)
    log("    新 status=%s" % updated.get("status"))
    return finish({"project": updated}, 0)


def do_status(args, token: str, cfg: dict) -> int:
    proj = resolve_project(token, cfg, args.proxy, args.project_id)
    if not proj:
        log("项目不存在（草稿项目 slug 查不到，请用 --project-id 指定）。")
        return finish({"error": "no project"}, 1)
    log("title=%s status=%s downloads=%s" % (proj.get("title"), proj.get("status"),
                                             proj.get("downloads")))
    log("versions=%s game_versions=%s" % (proj.get("versions"), proj.get("game_versions")))
    return finish({"project": proj}, 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["create", "version", "gallery", "submit", "status"])
    ap.add_argument("--project", default=DEFAULT_PROJECT, choices=sorted(PROJECTS),
                    help="要发布的项目（默认 %s）" % DEFAULT_PROJECT)
    ap.add_argument("--token", default=os.environ.get("MODRINTH_TOKEN", ""),
                    help="Modrinth PAT（默认读环境变量 MODRINTH_TOKEN）")
    ap.add_argument("--jar", default=None, help="模组 jar 路径（默认取项目配置）")
    ap.add_argument("--number", default=None, help="版本号（默认取项目配置）")
    ap.add_argument("--title", default=None, help="版本标题（默认取项目配置）")
    ap.add_argument("--changelog", default=None)
    ap.add_argument("--icon", default=None)
    ap.add_argument("--body", default=None, help="项目描述 markdown 文件（默认取项目配置）")
    ap.add_argument("--proxy", default=None, help="强制代理，如 http://127.0.0.1:7890")
    ap.add_argument("--project-id", default=None,
                    help="项目 id（草稿项目用 slug 查不到时用它定位；create 成功后会自动记进台账）")
    args = ap.parse_args()

    cfg = PROJECTS[args.project]
    # 命令行覆盖默认配置
    args.jar = args.jar or cfg["jar"]
    args.number = args.number or cfg["version"]
    args.changelog = args.changelog or cfg["changelog"]
    args.icon = args.icon or cfg["icon"]
    args.body = args.body or cfg["body"]

    if not args.token:
        log("缺少 token：$env:MODRINTH_TOKEN=\"mrp_xxx\"（modrinth.com/settings/pats 生成，"
            "勾 PROJECT_CREATE + VERSION_CREATE）")
        return 2
    if not os.path.isfile(args.jar):
        log("找不到 jar：%s" % args.jar)
        return 2
    if not os.path.isfile(args.icon):
        log("找不到图标：%s" % args.icon)
        return 2
    if not os.path.isfile(args.body):
        log("找不到正文：%s" % args.body)
        return 2

    log("项目=%s slug=%s jar=%s v%s" % (args.project, cfg["slug"],
                                        os.path.basename(args.jar), args.number))
    try:
        return {"create": do_create, "version": do_version, "gallery": do_gallery_cmd,
                "submit": do_submit, "status": do_status}[args.command](args, args.token, cfg)
    except RuntimeError as e:
        msg = str(e)
        if "verify your email" in msg:
            log("失败：Modrinth 要求**先验证邮箱**才能发布（token 本身有效）。")
            log("  请到 https://modrinth.com/settings/account 点「Resend verification email」，")
            log("  或查收注册邮箱里 Modrinth 的验证邮件；验证后重跑本命令即可。")
        else:
            log("失败：%s" % e)
        return finish({"error": msg}, 1)


if __name__ == "__main__":
    sys.exit(main())
