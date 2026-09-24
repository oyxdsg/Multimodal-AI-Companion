#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 deskpet-mod 发布到 Modrinth（建项目 / 传版本 / 提审）。

用法（token 从环境变量 MODRINTH_TOKEN 读取，或在 --token 传入）：
  python docs/publish/publish_modrinth.py create     # 建 draft 项目 + 传 2.0.0 版本
  python docs/publish/publish_modrinth.py submit     # draft -> 提交审核/公开
  python docs/publish/publish_modrinth.py version --jar path/to/x.jar --number 2.0.1
  python docs/publish/publish_modrinth.py status     # 查看当前项目状态

仅用标准库。网络默认走环境变量代理；连不上时自动回退 127.0.0.1:7890（Clash 常用端口）。
结果落盘 docs/publish/publish_modrinth_result.json（PowerShell 会吞 stdout，以文件为准）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = "https://api.modrinth.com/v2"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))            # docs/publish -> 仓库根
RESULT_FILE = os.path.join(HERE, "publish_modrinth_result.json")
DEFAULT_BODY = os.path.join(HERE, "modrinth-description.md")
DEFAULT_ICON = os.path.join(REPO, "docs", "images", "deskpet-mod-icon.png")
DEFAULT_JAR = os.path.join(REPO, "deskpet-mod", "build", "libs", "deskpet-mod-2.0.0.jar")
FALLBACK_PROXY = "http://127.0.0.1:7890"

SLUG = "deskpet-mod"
TITLE = "DeskPet Mod"
SUMMARY = ("Companion mod for the DeskPet desktop AI pet - it lets your desktop pet "
           "see what happens in your world.")
CATEGORIES = ["utility", "decoration", "game-mechanics"]
GAME_VERSIONS = ["26.2"]
LOADERS = ["fabric"]
LICENSE_ID = "MIT"
SOURCE_URL = "https://github.com/oyxdsg/Multimodal-AI-Companion"
ISSUES_URL = "https://github.com/oyxdsg/Multimodal-AI-Companion/issues"
CHANGELOG = ("First release: game event collection + local building recognition "
             "for Minecraft 26.2 (Fabric).")


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
        req.add_header("User-Agent", "deskpet-publish/1.0 (github.com/oyxdsg)")
        try:
            with opener.open(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if e.code == 404 and accept_404:
                return 404, {}
            try:
                parsed = json.loads(detail)
            except Exception:
                parsed = {"raw": detail}
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


def fabric_api_id(token: str, proxy) -> str | None:
    proj = get_project(token, "fabric-api", proxy)
    return proj.get("id") if proj else None


def do_create(args, token: str) -> int:
    proxy = args.proxy
    if get_project(token, SLUG, proxy):
        log("项目 %s 已存在，改用 `version` / `submit` 子命令。" % SLUG)
        return finish({"error": "project exists", "slug": SLUG}, 1)

    with open(args.body, "r", encoding="utf-8") as f:
        body = f.read().strip()
    with open(args.icon, "rb") as f:
        icon_bytes = f.read()

    project = {
        "slug": SLUG,
        "title": TITLE,
        "description": SUMMARY,
        "body": body,
        "categories": CATEGORIES,
        "client_side": "required",
        "server_side": "optional",
        "license_id": LICENSE_ID,
        "source_url": SOURCE_URL,
        "issues_url": ISSUES_URL,
        "project_type": "mod",
        "is_draft": True,   # Modrinth 官方建议恒为 true：先草稿，确认后 submit
    }
    log("[1/3] 创建项目（draft）……")
    st, proj = send_form("POST", "/project", token,
                         {"data": json.dumps(project)},
                         {"icon": (os.path.basename(args.icon), icon_bytes, "image/png")},
                         proxy)
    pid = proj["id"]
    log("    项目 id=%s  slug=%s  status=%s" % (pid, proj.get("slug"), proj.get("status")))

    log("[2/3] 上传版本 %s ……" % args.number)
    ver = do_version_upload(token, pid, args, proxy)
    log("    版本 id=%s" % ver["id"])

    log("[3/3] 完成。草稿地址（浏览器登录后可见）:")
    log("    https://modrinth.com/project/%s" % SLUG)
    log("    确认页面没问题后运行: python %s submit" % os.path.basename(__file__))
    return finish({"project": proj, "version": ver}, 0)


def do_version_upload(token: str, project_id: str, args, proxy) -> dict:
    fa_id = fabric_api_id(token, proxy)
    deps = []
    if fa_id:
        deps.append({"project_id": fa_id, "dependency_type": "required"})
    else:
        log("    ! 没拿到 fabric-api 的 project id，依赖关系需在网页上手动补")
    with open(args.jar, "rb") as f:
        jar_bytes = f.read()
    data = {
        "name": "DeskPet Mod %s" % args.number,
        "version_number": args.number,
        "changelog": args.changelog,
        "dependencies": deps,
        "game_versions": GAME_VERSIONS,
        "version_type": "release",
        "loaders": LOADERS,
        "featured": True,
        "status": "listed",
        "project_id": project_id,
        "file_parts": ["file"],
        "primary_file": "file",
        "environment": "client_only_server_optional",
    }
    st, ver = send_form("POST", "/version", token,
                        {"data": json.dumps(data)},
                        {"file": (os.path.basename(args.jar), jar_bytes,
                                  "application/java-archive")},
                        proxy)
    return ver


def do_version(args, token: str) -> int:
    proxy = args.proxy
    proj = get_project(token, SLUG, proxy)
    if not proj:
        log("项目不存在，先运行 create。")
        return finish({"error": "no project"}, 1)
    ver = do_version_upload(token, proj["id"], args, proxy)
    log("版本已上传：id=%s  %s" % (ver["id"], ver.get("version_number")))
    return finish({"version": ver}, 0)


def do_submit(args, token: str) -> int:
    proxy = args.proxy
    proj = get_project(token, SLUG, proxy)
    if not proj:
        log("项目不存在。")
        return finish({"error": "no project"}, 1)
    log("当前 status=%s，提交审核（draft -> approved）……" % proj.get("status"))
    body = json.dumps({"status": "approved"}).encode("utf-8")
    st, updated = http("PATCH", "/project/%s" % proj["id"], token, body, "application/json", proxy)
    log("    新 status=%s" % updated.get("status"))
    return finish({"project": updated}, 0)


def do_status(args, token: str) -> int:
    proj = get_project(token, SLUG, args.proxy)
    if not proj:
        log("项目不存在。")
        return finish({"error": "no project"}, 1)
    log("title=%s status=%s downloads=%s" % (proj.get("title"), proj.get("status"),
                                             proj.get("downloads")))
    log("versions=%s game_versions=%s" % (proj.get("versions"), proj.get("game_versions")))
    return finish({"project": proj}, 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["create", "version", "submit", "status"])
    ap.add_argument("--token", default=os.environ.get("MODRINTH_TOKEN", ""),
                    help="Modrinth PAT（默认读环境变量 MODRINTH_TOKEN）")
    ap.add_argument("--jar", default=DEFAULT_JAR, help="模组 jar 路径")
    ap.add_argument("--number", default="2.0.0", help="版本号")
    ap.add_argument("--changelog", default=CHANGELOG)
    ap.add_argument("--icon", default=DEFAULT_ICON)
    ap.add_argument("--body", default=DEFAULT_BODY, help="项目描述 markdown 文件")
    ap.add_argument("--proxy", default=None, help="强制代理，如 http://127.0.0.1:7890")
    args = ap.parse_args()

    if not args.token:
        log("缺少 token：$env:MODRINTH_TOKEN=\"mrp_xxx\"（modrinth.com/settings/pats 生成，"
            "勾 PROJECT_CREATE + VERSION_CREATE）")
        return 2
    if not os.path.isfile(args.jar):
        log("找不到 jar：%s" % args.jar)
        return 2

    try:
        return {"create": do_create, "version": do_version,
                "submit": do_submit, "status": do_status}[args.command](args, args.token)
    except RuntimeError as e:
        log("失败：%s" % e)
        return finish({"error": str(e)}, 1)


if __name__ == "__main__":
    sys.exit(main())
