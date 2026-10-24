#!/workspace/scrapling-env/bin/python
"""Search 海搜 haisou.cc through its OFFICIAL API on iDataRiver (not the website: haisou.cc's terms forbid scripted
scraping of the site, so this script never touches haisou.cc itself).

API: POST https://apiok.us/api/b9d1/search?apikey=<IDATARIVER_API_KEY>
     JSON {"query": kw (<=100 chars), "platforms": [ali|baidu|quark|xunlei|tianyi|yidong|115|123|uc] (empty = all),
           "searchIn": "title"|"files", "page": N (from 1), "pageSize": 1-100}
  -> {"code":0, "credits":x, "result":{"items":[{hsid, platform, platformName, title, shareUrl, shareCode, sharePwd,
       fileCount, sizeBytes}], "pagination":{page,pageSize,total,totalPages}}}
  HTTP status is always 200; success is code==0. code 1000/1003 = retry later (we retry once, only if credits==0),
  1001 parameter error, 1004 no permission, 1005 insufficient balance (stop).
  The API returns no share date, so the date column stays empty.
Docs: https://www.idatariver.com/project/haisou-api-b9d1
Cost: first 100 calls/day free, then 0.0001 USD per call (Peter OK'd going over). One page = one call.
Key: IDATARIVER_API_KEY from the environment, else read from ~/.secrets/idatariver.env. The key is never printed.
Polite: 1.5-2.5 s between calls; timeout 70 s (docs: >60 s to avoid being charged for aborted calls).
Usage: haisou_search.py KEYWORD [--max-pages N (default 1)] [--page-size N (default 100, max 100)]
                        [--pan all|ali,quark,...] [--search-in title|files] [--out DIR]
"""
import os, sys, csv, json, time, random, datetime, argparse
import urllib.request, urllib.error
from zoneinfo import ZoneInfo

API = "https://apiok.us/api/b9d1/search"
PT = ZoneInfo("America/Los_Angeles")
PLATFORMS = ["ali", "baidu", "quark", "xunlei", "tianyi", "yidong", "115", "123", "uc"]
PAN_CN = {"ali": "阿里云盘", "baidu": "百度网盘", "quark": "夸克网盘", "xunlei": "迅雷云盘", "tianyi": "天翼云盘",
          "yidong": "移动云盘", "115": "115网盘", "123": "123云盘", "uc": "UC网盘"}
ENV_FILE = os.path.expanduser("~/.secrets/idatariver.env")
_last = [0.0]
calls = [0, 0.0]          # number of API calls, credits reported

class ApiStop(Exception):
    pass

def api_key():
    k = os.environ.get("IDATARIVER_API_KEY", "").strip()
    if not k and os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("IDATARIVER_API_KEY="):
                k = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not k:
        sys.exit(f"没有 IDATARIVER_API_KEY（环境变量或 {ENV_FILE}），无法调用海搜 API")
    return k

def post(key, body):
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    for attempt in (1, 2):
        req = urllib.request.Request(f"{API}?apikey={key}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json",
                                              "User-Agent": "haisou_search.py/1.0 (+Peter; iDataRiver API client)"})
        calls[0] += 1
        try:
            with urllib.request.urlopen(req, timeout=70) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:     # don't echo the URL (contains the key)
            snip = (e.read() or b"")[:120].decode("utf-8", "replace").replace(key, "***")
            raise ApiStop(f"HTTP {e.code} {snip!r}")
        except Exception as e:
            raise ApiStop(f"network error: {type(e).__name__}")
        finally:
            _last[0] = time.time()
        try:
            calls[1] += float(d.get("credits") or 0)
        except Exception:
            pass
        code = d.get("code")
        if code == 0:
            return d
        msg = d.get("msg") or ""
        if code in (1000, 1003) and attempt == 1 and not d.get("credits"):
            print(f"[warn] API code {code} {msg} - retrying once in 10 s", file=sys.stderr)
            time.sleep(10); continue
        raise ApiStop(f"API code {code} {msg}".strip())
    raise ApiStop("API busy twice")

def human(n):
    try:
        n = float(n)
    except Exception:
        return ""
    if n <= 0:
        return ""
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.2f} {u}"
        n /= 1024

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword")
    ap.add_argument("--max-pages", type=int, default=1)
    ap.add_argument("--page-size", type=int, default=100)
    ap.add_argument("--pan", default="all", help="all or comma list of " + ",".join(PLATFORMS))
    ap.add_argument("--search-in", default="title", choices=["title", "files"])
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip()[:100]
    pans = [] if a.pan == "all" else [p.strip() for p in a.pan.split(",") if p.strip()]
    bad = [p for p in pans if p not in PLATFORMS]
    if bad:
        sys.exit(f"unknown --pan {bad}; choose from {PLATFORMS}")
    size = max(1, min(100, a.page_size))
    key = api_key()
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows, seen, total, pages_total, fetched, stopped = [], set(), None, None, 0, ""
    try:
        for page in range(1, max(1, a.max_pages) + 1):
            body = {"query": kw, "searchIn": a.search_in, "page": page, "pageSize": size}
            if pans:
                body["platforms"] = pans
            d = post(key, body)
            fetched += 1
            res = d.get("result") or {}
            pg = res.get("pagination") or {}
            total, pages_total = pg.get("total", total), pg.get("totalPages", pages_total)
            items = res.get("items") or []
            for it in items:
                u = (it.get("shareUrl") or "").strip()
                k = u or it.get("hsid")
                if k in seen:
                    continue
                seen.add(k)
                rows.append({"网盘": it.get("platformName") or PAN_CN.get(it.get("platform"), it.get("platform") or ""),
                             "title": (it.get("title") or "").strip(), "share_url": u,
                             "提取码": (it.get("sharePwd") or "").strip(),
                             "size": human(it.get("sizeBytes")), "size_bytes": it.get("sizeBytes") or "",
                             "file_count": it.get("fileCount") if it.get("fileCount") is not None else "",
                             "date": "", "hsid": it.get("hsid") or "", "fetched_at": ts})
            if not items or (pages_total and page >= pages_total):
                break
    except ApiStop as e:
        stopped = str(e)
        print(f"[warn] stopped: {stopped} (partial results saved)", file=sys.stderr)
    secs = time.time() - t0
    base = os.path.join(a.out, f"haisou_{kw}" + (f"_{'+'.join(pans)}" if pans else ""))
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "platforms": pans or "all", "search_in": a.search_in, "fetched_at": ts,
                   "site_total": total, "site_total_pages": pages_total, "page_size": size, "pages_fetched": fetched,
                   "api_calls": calls[0], "credits": calls[1], "seconds": round(secs, 1), "stopped": stopped or None,
                   "results": rows}, f, ensure_ascii=False, indent=1)
    cols = ["网盘", "title", "share_url", "提取码", "size", "size_bytes", "file_count", "date", "hsid", "fetched_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["网盘"]] = by.get(r["网盘"], 0) + 1
    print(f"海搜(API)「{kw}」: {len(rows)} 条链接（网站共 {total} 条/{pages_total} 页；抓了 {fetched} 页，每页 {size}；"
          f"API 调用 {calls[0]} 次，credits {calls[1]}）@ {ts}，用时 {secs:.0f} 秒\n{base}.json\n{base}.csv\nby pan: {by}")
    for r in rows:
        print(f"{r['网盘']}\t{r['share_url']}\t码:{r['提取码'] or '-'}\t{r['size']}\t{r['file_count']}个文件\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
