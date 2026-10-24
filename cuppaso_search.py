#!/workspace/scrapling-env/bin/python
"""Search 咔帕搜索 cuppaso.com (Java/Spring Boot netdisk search engine, mostly 阿里云盘; index appears to stop ~2023-11)
for a keyword, then open up to --links N detail pages to read the real share links.

Access: plain HTTP, server-rendered HTML, no login, no anti-bot seen. The WeChat 「芝麻开门」 unlock modal exists in the
  page source but is commented out (disabled) - if it ever comes back, detail pages will lack the link and rows are
  marked 需解锁 instead of guessing.
Endpoints:
  GET /search?type=2&keyword=<kw>&searchType=0&page=N   (type=2 精准 default; --fuzzy uses type=1 智能, mostly loose junk)
      -> cards: <a href="share/<id>:<sig>"> title, 收录时间, view count; header '共 N 条相关资源'
  GET /share/<id>:<sig>  -> '前往阿里云盘下载》》' button holding the real link (plain HTML)
robots.txt: none (404). Terms: no anti-scraping clause; commercial use needs permission.
Polite: 1.5-2.5 s random delay between requests; <= --max-pages + --links requests per run.
Usage: cuppaso_search.py KEYWORD [--fuzzy] [--max-pages N (default 1)] [--links N (default 5)] [--out DIR]
"""
import os, sys, re, csv, json, time, random, datetime, argparse, html
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from scrapling.fetchers import Fetcher

BASE = "https://www.cuppaso.com"
PT = ZoneInfo("America/Los_Angeles")
PANS = [("夸克网盘", r"pan\.quark\.cn/s/"), ("阿里云盘", r"(?:aliyundrive\.com|alipan\.com)/s/"), ("百度网盘", r"pan\.baidu\.com/"),
        ("迅雷云盘", r"pan\.xunlei\.com/s/"), ("天翼云盘", r"cloud\.189\.cn/"), ("UC网盘", r"drive\.uc\.cn/s/")]
LINK_RE = re.compile(r'href="(https?://(?:[\w-]+\.)*(?:' + "|".join(p for _, p in PANS) + r')[^"]*)"', re.I)
CODE_RE = re.compile(r"(?:提取码|密码|访问码)\s*[:：]\s*([A-Za-z0-9]{4,8})")
_last = [0.0]

def get(url, ref=BASE + "/"):
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    for attempt in (1, 2):
        r = Fetcher.get(url, timeout=40, headers={"Referer": ref})
        _last[0] = time.time()
        if r.status == 200:
            return r.body.decode("utf-8", "replace") if isinstance(r.body, bytes) else str(r.body)
        if r.status in (429, 500, 502, 503, 504) and attempt == 1:
            print(f"[warn] HTTP {r.status} - retrying in 10 s", file=sys.stderr); time.sleep(10); continue
        raise RuntimeError(f"HTTP {r.status} on {url}")

def txt(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()

def pan_of(u):
    for name, pat in PANS:
        if re.search(pat, u, re.I):
            return name
    return "未知"

def parse_search(h):
    tot = re.search(r"共\s*<span[^>]*>\s*(\d+)\s*</span>\s*条", h)
    pages = [int(x) for x in re.findall(r"[?&]page=(\d+)", h)]
    recs = []
    cards = re.split(r'<div class="card">', h)[1:]
    for c in cards:
        m = re.search(r'<h3 class="card-title">\s*<a href="/?(share/[^"]+)">(.*?)</a>', c, re.S)
        if not m:
            continue
        t = re.search(r"收录时间:\s*([\d-]+ [\d:]+)", c)
        v = re.search(r"icon-tabler-eye.*?</svg>\s*(\d+)", c, re.S)
        recs.append({"title": txt(m.group(2)), "detail_url": f"{BASE}/{m.group(1)}", "indexed": t.group(1) if t else "",
                     "views": int(v.group(1)) if v else None})
    return recs, int(tot.group(1)) if tot else None, max(pages) if pages else 1

def parse_detail(h):
    links = []
    for u in LINK_RE.findall(h):
        u = html.unescape(u)
        if u not in links:
            links.append(u)
    t = re.search(r"关于“(.*?)\s*”的(\S+?)资源分享链接", h)
    code = CODE_RE.search(re.sub(r"<!--.*?-->", "", h, flags=re.S))
    return links, (t.group(2) if t else ""), (code.group(1) if code else "")

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--fuzzy", action="store_true", help="type=1 智能 instead of 2 精准")
    ap.add_argument("--max-pages", type=int, default=1); ap.add_argument("--links", type=int, default=5)
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip()
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows, seen, total, last_page, fetched = [], set(), None, None, 0
    for page in range(1, max(1, a.max_pages) + 1):
        q = {"type": 1 if a.fuzzy else 2, "keyword": kw, "searchType": 0, "page": page}
        recs, t, lp = parse_search(get(f"{BASE}/search?{urlencode(q)}"))
        fetched += 1
        if t is not None:
            total = t
        last_page = max(last_page or 1, lp)
        for r in recs:
            if r["detail_url"] in seen:
                continue
            seen.add(r["detail_url"])
            r.update({"pan_type": "", "share_url": "", "extraction_code": "", "scraped_at": ts}); rows.append(r)
        if not recs or page >= last_page:
            break
    opened = 0
    for r in rows[:max(0, a.links)]:
        try:
            links, pan_word, code = parse_detail(get(r["detail_url"], ref=f"{BASE}/search"))
        except Exception as e:
            print(f"[warn] detail failed {r['detail_url']}: {e}", file=sys.stderr); continue
        opened += 1
        if links:
            r["share_url"] = links[0]; r["pan_type"] = pan_of(links[0]); r["extraction_code"] = code
            if len(links) > 1:
                r["extra_links"] = " ".join(links[1:])
        else:
            r["pan_type"] = "需解锁" if "芝麻开门" in pan_word or not pan_word else pan_word
    for r in rows:
        r.setdefault("extra_links", "")
        if not r["pan_type"]:
            r["pan_type"] = "未打开"
    base = os.path.join(a.out, f"cuppaso_{kw}{'_fuzzy' if a.fuzzy else ''}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "mode": "智能" if a.fuzzy else "精准", "scraped_at": ts, "site_total": total,
                   "site_pages": last_page, "pages_fetched": fetched, "details_opened": opened, "results": rows},
                  f, ensure_ascii=False, indent=1)
    cols = ["title", "pan_type", "share_url", "extraction_code", "extra_links", "indexed", "views", "detail_url", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    got = sum(1 for r in rows if r["share_url"])
    print(f"{len(rows)} results (site total {total}, {last_page}+ pages, fetched {fetched}); real links {got}/{opened} opened @ {ts}\n{base}.json\n{base}.csv")
    for r in rows:
        print(f"{r['pan_type']}\t{r['share_url'] or '-'}\t{r['extraction_code']}\t{r['indexed']}\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
