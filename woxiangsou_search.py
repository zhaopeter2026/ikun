#!/workspace/scrapling-env/bin/python
"""Search 我想搜 / 口袋云 (搜狐) woxiangsou.com for a keyword.

Access: plain JSON API (Scrapling Fetcher), no login, no cookie, no signature. No robots.txt.
  GET https://www.woxiangsou.com/api/v1/resInfo/search
      ?deviceId=<any string, required - a random UUID per run>&keyword=<kw>&sourceType=0|1|2|3
       &timeFilter=0&sizeFilter=0&offset=<N*20>&limit=20&applyAt=0
      sourceType: 0 全部 1 百度网盘 2 阿里云盘 3 夸克网盘
  Response: {count, hasMore, resInfos:[{id, name, code (提取码), sourceType, createTime (ms), files:[{name,size,...}],
             url: "https://www.woxiangsou.com/api/redirect?type=resInfoClick&url=<real share URL, urlencoded>&..."}]}
  The real share URL is decoded from the redirect's `url` parameter (the redirect/click tracker itself is never called).
  Baidu links usually already carry ?pwd=; otherwise the code is appended as ?pwd= for Baidu, and kept in extraction_code.
Caveats: index createTime is 2023-06 .. 2024-11 (old links may be dead); relevance is weak beyond page 1.
Polite: 1.5-2.5 s random delay between requests; <= --pages requests per run (default 2).
Usage: woxiangsou_search.py KEYWORD [--pages N (default 2)] [--pan all|BAIDU|ALIYUN|QUARK] [--out DIR]
"""
import os, sys, re, csv, json, time, random, uuid, datetime, argparse
from urllib.parse import urlencode, urlparse, parse_qs
from zoneinfo import ZoneInfo
from scrapling.fetchers import Fetcher

BASE = "https://www.woxiangsou.com"
API = BASE + "/api/v1/resInfo/search"
PT = ZoneInfo("America/Los_Angeles")
LIMIT = 20
MAX_PAGES = 50
PANS = {"all": 0, "BAIDU": 1, "ALIYUN": 2, "QUARK": 3}
PAN_CN = {"all": "全部", "BAIDU": "百度网盘", "ALIYUN": "阿里云盘", "QUARK": "夸克网盘"}
SRC_CN = {1: "百度网盘", 2: "阿里云盘", 3: "夸克网盘"}
HOSTS = [("quark.cn", "夸克网盘"), ("aliyundrive.com", "阿里云盘"), ("alipan.com", "阿里云盘"), ("baidu.com", "百度网盘")]
_last = [0.0]

def get_json(params):
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    url = f"{API}?{urlencode(params)}"
    for attempt in (1, 2):
        r = Fetcher.get(url, timeout=40, headers={"Referer": BASE + "/home", "Accept": "application/json, text/plain, */*"})
        _last[0] = time.time()
        if r.status == 200:
            body = r.body.decode("utf-8", "replace") if isinstance(r.body, bytes) else str(r.body)
            return json.loads(body)
        if r.status in (429, 500, 502, 503, 504) and attempt == 1:
            print(f"[warn] HTTP {r.status} - 10 秒后重试", file=sys.stderr); time.sleep(10); continue
        raise RuntimeError(f"HTTP {r.status} on {url}")

def real_url(u):
    """decode the real share URL out of /api/redirect?...&url=<encoded>"""
    if not u:
        return ""
    p = urlparse(u)
    if p.netloc.endswith("woxiangsou.com"):
        inner = parse_qs(p.query).get("url")
        return inner[0].strip() if inner else ""
    return u.strip()

def pan_of(u, st=None):
    host = urlparse(u).netloc.lower()
    for d, n in HOSTS:
        if d in host:
            return n
    return SRC_CN.get(st, "")

def with_pwd(u, code, pan):
    if not code or pan != "百度网盘" or parse_qs(urlparse(u).query).get("pwd"):
        return u
    return u + ("&" if "?" in u else "?") + "pwd=" + code

def norm(u):
    """dedupe key: host+path (no #fragment), keep ?pwd="""
    p = urlparse(u.split("#")[0]); pwd = parse_qs(p.query).get("pwd")
    return f"{p.netloc.lower()}{p.path.rstrip('/')}" + (f"?pwd={pwd[0]}" if pwd else "")

def ms_date(ms):
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000, PT).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""

def human(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024

def to_row(x):
    u = real_url(x.get("url"))
    code = (x.get("code") or "").strip()
    if not code and u:
        code = (parse_qs(urlparse(u).query).get("pwd") or [""])[0]
    pan = pan_of(u, x.get("sourceType"))
    files = x.get("files") or []
    total = sum(int(f.get("size") or 0) for f in files if str(f.get("size") or "").isdigit())
    return {"title": re.sub(r"\s+", " ", x.get("name") or "").strip(), "pan_type": pan,
            "share_url": with_pwd(u, code, pan) if u else "", "extraction_code": code,
            "date": ms_date(x.get("createTime")), "file_count": len(files), "files_size": human(total) if files else "",
            "files": " | ".join((f.get("name") or "") for f in files[:10])[:500],
            "desc": re.sub(r"\s+", " ", x.get("desc") or "").strip()[:300], "site_id": x.get("id") or ""}

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--pan", default="all", type=lambda s: s.upper() if s.lower() != "all" else "all", choices=list(PANS))
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip()
    if a.pages > MAX_PAGES:
        print(f"[提示] --pages 最多 {MAX_PAGES}，已改为 {MAX_PAGES}", file=sys.stderr)
    pages = max(1, min(a.pages, MAX_PAGES))
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    device = str(uuid.uuid4())
    rows, seen, fetched, items, nolink, total, stop = [], set(), 0, 0, 0, None, "已到 --pages 上限"
    for page in range(1, pages + 1):
        d = get_json({"deviceId": device, "keyword": kw, "sourceType": PANS[a.pan], "timeFilter": 0, "sizeFilter": 0,
                      "offset": (page - 1) * LIMIT, "limit": LIMIT, "applyAt": 0})
        fetched += 1
        if total is None:
            total = d.get("count")
        recs = d.get("resInfos") or []
        if not recs:
            stop = "没有更多结果" if page > 1 else "没有结果"; break
        items += len(recs)
        for x in recs:
            r = to_row(x)
            if not r["share_url"]:
                nolink += 1; continue
            k = norm(r["share_url"])
            if k in seen:
                continue
            seen.add(k); r["page"] = page; r["scraped_at"] = ts; rows.append(r)
        if not d.get("hasMore"):
            stop = "最后一页（hasMore=false）"; break
    sfx = f"_{PAN_CN[a.pan]}" if a.pan != "all" else ""
    base = os.path.join(a.out, f"woxiangsou_{kw}{sfx}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "pan": PAN_CN[a.pan], "scraped_at": ts, "device_id": device, "site_total_count": total,
                   "pages_fetched": fetched, "items_seen": items, "items_without_link": nolink, "stop_reason": stop,
                   "results": rows}, f, ensure_ascii=False, indent=1)
    cols = ["title", "pan_type", "share_url", "extraction_code", "date", "file_count", "files_size", "files", "desc",
            "site_id", "page", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["pan_type"]] = by.get(r["pan_type"], 0) + 1
    print(f"我想搜「{kw}」（{PAN_CN[a.pan]}）: {len(rows)} 条不重复链接（抓了 {fetched} 页、{items} 个结果，网站称共 {total} 条；"
          f"{nolink} 个结果没有链接）@ {ts}")
    print(f"停止原因: {stop}\n{base}.json\n{base}.csv\n按网盘: {by}")
    print("提醒: 收录时间多在 2023-06 ~ 2024-11，老链接可能已失效；第 1 页之后相关度明显变弱。")
    if not rows:
        print("（没有结果）")
    for r in rows:
        print(f"{r['pan_type']}\t{r['share_url'][:90]}\t码:{r['extraction_code'] or '-'}\t{r['date']}\t{r['file_count']}个文件\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
