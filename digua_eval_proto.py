#!/workspace/scrapling-env/bin/python
"""PROTOTYPE (eval only, not final) - 地瓜搜 dg.digua.fit (uni-app/Vue SPA, nginx) netdisk search.

Access: plain HTTP JSON API that the page's own JS calls (assets/Tag.*.js), no login/token/captcha seen:
  GET http://dg.digua.fit/api/digua/url?search=<kw>&tagId=&cateId=&page=N   (20 items/page, empty list = end)
      item: id, title, share_url (real pan link; Baidu/Xunlei carry ?pwd=), pan_type (10 夸克 30 百度 40 迅雷),
            pan_type_text, submit_time (Beijing time), time, vip_level, auth_text (UI hides the link when non-empty
            -> we skip such items, never use their share_url).
  GET /api/digua/urldetail?id=<id>  (detail: share_url, publish_time)
robots.txt: "User-agent: * / Disallow:" (everything allowed).
Network: site is HTTP-only; the box's direct egress timed out, so this goes through the stable CONNECT proxy
  (SAND_EGRESS_TUNNEL_PROXY_ADDR, default 127.0.0.1:8791) with HTTP proxy tunnelling.
Polite: 1.5-2.5 s random delay; default <= 3 pages per run.
Usage: digua_eval_proto.py KEYWORD [--pages N] [--out DIR]
"""
import os, sys, json, time, random, datetime, argparse, csv
from urllib.parse import quote
from zoneinfo import ZoneInfo
from curl_cffi import requests, CurlOpt

BASE = "http://dg.digua.fit"
PT = ZoneInfo("America/Los_Angeles")
PROXY = None
_s = requests.Session(impersonate="chrome")
_last = [0.0]

def get_json(path):
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    for attempt in (1, 2):
        try:
            r = _s.get(BASE + path, timeout=40, headers={"Referer": BASE + "/"})
        except Exception as e:
            if attempt == 1:
                print(f"[warn] {e} - retry in 10 s", file=sys.stderr); time.sleep(10); continue
            raise
        _last[0] = time.time()
        if r.status_code == 200:
            j = r.json()
            if j.get("code") != 1:
                raise RuntimeError(f"API code {j.get('code')}: {j.get('msg')}")
            return j
        if r.status_code in (429, 500, 502, 503, 504) and attempt == 1:
            print(f"[warn] HTTP {r.status_code} - retry in 10 s", file=sys.stderr); time.sleep(10); continue
        raise RuntimeError(f"HTTP {r.status_code} on {path}")

def search(kw, pages=3):
    rows, skipped = [], 0
    for p in range(1, pages + 1):
        lst = get_json(f"/api/digua/url?search={quote(kw)}&tagId=&cateId=&page={p}")["data"]["list"]
        if not lst:
            break
        for it in lst:
            if it.get("auth_text"):      # link gated by the site -> do not use
                skipped += 1; continue
            u = it["share_url"]
            pwd = u.split("pwd=")[1].split("&")[0] if "pwd=" in u else ""
            rows.append({"id": it["id"], "title": it["title"], "pan": it["pan_type_text"], "url": u, "pwd": pwd,
                         "submit_time_cst": it["submit_time"], "page": p})
    return rows, skipped

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("keyword"); ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--out", default="/workspace/scrapes/out"); a = ap.parse_args()
    rows, skipped = search(a.keyword, max(1, min(a.pages, 20)))
    os.makedirs(a.out, exist_ok=True)
    fn = os.path.join(a.out, f"digua_{a.keyword}.csv")
    with open(fn, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["id", "title", "pan", "url", "pwd", "submit_time_cst", "page"]); w.writeheader(); w.writerows(rows)
    print(f"{datetime.datetime.now(PT):%Y-%m-%d %H:%M %Z} 地瓜搜 '{a.keyword}': {len(rows)} links (skipped gated {skipped}) -> {fn}")
    for r in rows[:10]:
        print(f"  [{r['pan']}] {r['title']}  {r['url']}  ({r['submit_time_cst']})")
