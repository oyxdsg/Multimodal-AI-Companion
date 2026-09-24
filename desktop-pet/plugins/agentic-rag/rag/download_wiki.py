import argparse
import json
import os
import re
import time

from playwright.sync_api import sync_playwright

BASE = "https://zh.minecraft.wiki"
API = BASE + "/api.php"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
TITLES_FILE = os.path.join(ROOT, "data", "titles.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0")

SAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

FETCH_ALL_JS = """
(() => {
window.fetchAll = async (urls, concurrency) => {
    const results = new Array(urls.length);
    let idx = 0;
    async function worker() {
        while (idx < urls.length) {
            const i = idx++;
            const url = urls[i];
            try {
                const r = await fetch(url);
                results[i] = { status: r.status, text: await r.text() };
            } catch (e) {
                results[i] = { status: 0, text: String(e) };
            }
        }
    }
    const workers = [];
    for (let k = 0; k < concurrency; k++) workers.push(worker());
    await Promise.all(workers);
    return results;
};
})()
"""


def qs(**params):
    from urllib.parse import urlencode
    return API + "?" + urlencode(params)


def sanitize(name):
    return SAFE.sub("_", name).strip(" .") or "untitled"


def load_done_titles():
    done = set()
    for fn in os.listdir(RAW):
        m = re.match(r"^(\d+)_", fn)
        if m:
            done.add(int(m.group(1)))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="仅测试：只下载前 N 个页面的内容")
    args = ap.parse_args()
    os.makedirs(RAW, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        page.goto(BASE + "/wiki/Minecraft", timeout=90000)
        page.wait_for_timeout(5000)
        page.evaluate(FETCH_ALL_JS)

        # ---------- Step 1: all page titles (ns=0) ----------
        titles = {}
        cont = None
        while True:
            params = dict(action="query", list="allpages", ns="0",
                          aplimit="500", apfilterredir="nonredirects",
                          format="json", formatversion="2")
            if cont:
                params["apcontinue"] = cont
            (resp,) = page.evaluate("a => window.fetchAll(a[0], a[1])", [[qs(**params)], 1])
            data = json.loads(resp["text"])
            for p_ in data["query"]["allpages"]:
                titles[p_["pageid"]] = p_["title"]
            cont = data.get("continue", {}).get("apcontinue")
            print(f"[titles] 累计 {len(titles)} 页面", flush=True)
            if not cont:
                break
            time.sleep(0.3)

        with open(TITLES_FILE, "w", encoding="utf-8") as f:
            json.dump(titles, f, ensure_ascii=False, indent=1)
        print(f"[titles] 完成，共 {len(titles)} 个页面 -> data/titles.json", flush=True)

        # ---------- Step 2: fetch wikitext in batches ----------
        items = list(titles.items())
        done = load_done_titles()
        pending = [it for it in items if it[0] not in done]
        if args.limit:
            pending = pending[:args.limit]
        print(f"[content] 待下载 {len(pending)} 页面（已存在 {len(done)}）", flush=True)

        batch_size = 50
        for bstart in range(0, len(pending), batch_size):
            batch = pending[bstart:bstart + batch_size]
            tstr = "|".join(t for _, t in batch)
            url = qs(action="query", prop="revisions", rvprop="content",
                     rvslots="main", formatversion="2", format="json",
                     titles=tstr)
            (resp,) = page.evaluate("a => window.fetchAll(a[0], a[1])", [[url], 1])
            if resp["status"] != 200:
                print(f"[content] 批次失败 status={resp['status']}，重试跳过: {tstr[:60]}", flush=True)
                continue
            data = json.loads(resp["text"])
            for p_ in data["query"]["pages"]:
                pid = p_["pageid"]
                title = p_.get("title", "")
                content = ""
                if "revisions" in p_:
                    content = p_["revisions"][0]["slots"]["main"]["content"]
                fname = f"{pid}_{sanitize(title)}.txt"
                with open(os.path.join(RAW, fname), "w", encoding="utf-8") as f:
                    f.write(content)
            print(f"[content] 批次完成 {bstart + len(batch)}/{len(pending)}", flush=True)
            time.sleep(0.3)

        browser.close()

    print("[done] 全部完成", flush=True)


if __name__ == "__main__":
    main()
