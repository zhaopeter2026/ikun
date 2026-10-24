#!/workspace/scrapling-env/bin/python
"""Search 盘小子 pan.xiaozi.cc (general netdisk index, ~120k entries, almost all 夸克) for a keyword.

Access: plain HTTP, no login, no API key. Pages are server-rendered (TanStack Start); each search page embeds its
  result records in the hydration script as JS objects:
  {id, categoryKey, pinyin, title, desc, cover, diskType, url, hotNum, isShowHome, updatedAt: new Date("...Z")}
  plus total:N, totalPages:N. Detail page = /resource/<pinyin> (the numeric slug, not id).
  No public /api/ (frontend only calls internal /_serverFn/, not used). Cloudflare in front but no challenge seen.
Endpoint: GET https://pan.xiaozi.cc/resource?q=<kw>&page=N[&category=X]
  categories: movie tv anime cartoon variety short comic ebook course concert(音乐) material software other
robots.txt: Allow / for all (AI agents explicitly allowed). llms.txt asks to keep the detail-page URL when citing,
  so every row carries detail_url.
Polite: 1.5-2.5 s random delay between requests; <= --max-pages requests per run.
Usage: xiaozi_search.py KEYWORD [--max-pages N (default 2)] [--category X] [--out DIR (default /workspace/scrapes/out)]
"""
import os, sys, re, csv, json, time, random, datetime, argparse
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from scrapling.fetchers import Fetcher

BASE = "https://pan.xiaozi.cc"
PT = ZoneInfo("America/Los_Angeles")
CATS = {"movie": "电影", "tv": "电视剧", "anime": "动漫", "cartoon": "动画片", "variety": "综艺", "short": "短剧",
        "comic": "漫画", "ebook": "电子书", "course": "课程", "concert": "音乐", "material": "素材",
        "software": "软件", "other": "其他"}
DISK = {"夸克": "夸克网盘", "百度": "百度网盘", "阿里": "阿里云盘", "迅雷": "迅雷云盘", "UC": "UC网盘", "天翼": "天翼云盘"}
S = r'"((?:[^"\\]|\\.)*)"'
REC_RE = re.compile(r"\{id:(\d+),categoryKey:" + S + r",pinyin:" + S + r",title:" + S + r",desc:" + S +
                    r",cover:" + S + r",diskType:" + S + r",url:" + S + r"[^}]*?updatedAt:(?:\$R\[\d+\]=)?new Date\(" + S + r"\)")
_last = [0.0]

def js_str(s):
    try:
        return json.loads('"' + s + '"')
    except Exception:
        return s

def get(url):
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    for attempt in (1, 2):
        r = Fetcher.get(url, timeout=40, headers={"Referer": BASE + "/"})
        _last[0] = time.time()
        if r.status == 200:
            return r.body.decode("utf-8", "replace") if isinstance(r.body, bytes) else str(r.body)
        if r.status in (429, 500, 502, 503, 504) and attempt == 1:
            print(f"[warn] HTTP {r.status} - retrying in 10 s", file=sys.stderr); time.sleep(10); continue
        raise RuntimeError(f"HTTP {r.status} on {url}")

def to_pt(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PT).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso

def parse(h):
    # only the hydrated loader data of the search route; split off anything before it is unnecessary because
    # the search page carries no other resource lists, but dedupe by detail id anyway.
    tot = re.search(r"total:(\d+),totalPages:(\d+)", h) or re.search(r"total:(\d+).*?totalPages:(\d+)", h, re.S)
    recs = []
    for m in REC_RE.finditer(h):
        rid, cat, slug, title, desc, cover, disk, url, upd = m.groups()
        recs.append({"title": js_str(title), "category": CATS.get(js_str(cat), js_str(cat)),
                     "pan_type": DISK.get(js_str(disk), js_str(disk)), "share_url": js_str(url), "extraction_code": "",
                     "updated": to_pt(js_str(upd)), "detail_url": f"{BASE}/resource/{js_str(slug)}",
                     "desc": re.sub(r"\s+", " ", js_str(desc)).strip()[:200]})
    return recs, (int(tot.group(1)), int(tot.group(2))) if tot else (None, None)

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--max-pages", type=int, default=2)
    ap.add_argument("--category", default="", help="|".join(CATS))
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip()
    if a.category and a.category not in CATS:
        sys.exit(f"unknown --category {a.category!r}; use one of {', '.join(CATS)}")
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows, seen, total, pages, fetched = [], set(), None, None, 0
    for page in range(1, max(1, a.max_pages) + 1):
        q = {"q": kw, "page": page}
        if a.category:
            q["category"] = a.category
        recs, (t, tp) = parse(get(f"{BASE}/resource?{urlencode(q)}"))
        fetched += 1
        if t is not None:
            total, pages = t, tp
        for r in recs:
            if r["detail_url"] in seen:
                continue
            seen.add(r["detail_url"]); r["scraped_at"] = ts; rows.append(r)
        if not recs or (pages is not None and page >= pages):
            break
    sfx = f"_{a.category}" if a.category else ""
    base = os.path.join(a.out, f"xiaozi_{kw}{sfx}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "category": a.category or None, "scraped_at": ts, "site_total": total,
                   "site_pages": pages, "pages_fetched": fetched, "results": rows}, f, ensure_ascii=False, indent=1)
    cols = ["title", "category", "pan_type", "share_url", "extraction_code", "updated", "detail_url", "desc", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["pan_type"]] = by.get(r["pan_type"], 0) + 1
    print(f"{len(rows)} results (site total {total}, {pages} pages, fetched {fetched}) @ {ts}\n{base}.json\n{base}.csv\nby pan: {by}")
    for r in rows:
        print(f"{r['pan_type']}\t{r['share_url']}\t{r['updated']}\t{r['detail_url']}\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
