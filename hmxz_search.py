#!/workspace/scrapling-env/bin/python
"""Search 海绵小站 / 空盘汇 www.hmxz.org (Xiuno BBS netdisk forum) for a keyword and pull netdisk links from threads.

Access: plain HTTP gets an anti-CC page with a 「进入网站」 button; a Scrapling StealthySession (one headless browser,
  reused for all pages) clicks it like a human and waits 4 s. No login. Never touches /user-* (robots-disallowed).
Endpoints: GET /search-<urlencoded kw>.htm -> thread links 'thread-<id>.htm'; thread pages show pan links anonymously.
  robots.txt (User-agent *) disallows /admin, /user-*, /serach-* only; search-* and thread-* allowed.
Polite: 1.5-2.5 s random delay between requests; <= max+2 requests per run (search, optional fallback search, N threads).
If a keyword containing 合集/全集 etc. yields 0 threads, retries once with those words stripped.
Usage: hmxz_search.py KEYWORD [--max N (default 10)] [--out DIR (default /workspace/scrapes/out)]
"""
import os, sys, re, csv, json, time, random, datetime, argparse, html
from urllib.parse import quote, urljoin
from zoneinfo import ZoneInfo
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/workspace/scrapling-env/ms-playwright")
from scrapling.fetchers import StealthySession

BASE = "https://www.hmxz.org/"
PT = ZoneInfo("America/Los_Angeles")
GENERIC = re.compile(r"(合集|全集|系列|大全|全套|收藏版?|打包)")
PANS = [("夸克网盘", r"pan\.quark\.cn/s/"), ("百度网盘", r"pan\.baidu\.com/"), ("阿里云盘", r"(?:aliyundrive\.com|alipan\.com)/s/"),
        ("天翼云盘", r"cloud\.189\.cn/"), ("移动云盘", r"(?:caiyun\.139\.com|yun\.139\.com)/"), ("115网盘", r"(?:115\.com|115cdn\.com)/s/"),
        ("UC网盘", r"drive\.uc\.cn/s/"), ("迅雷云盘", r"pan\.xunlei\.com/s/")]
LINK_RE = re.compile(r"https?://(?:" + "|".join(p for _, p in PANS) + r")[^\s\"'<>&]*(?:&amp;[^\s\"'<>&]*|&[^\s\"'<>&]*)*", re.I)
CODE_RE = re.compile(r"(?:提取码|密码|访问码|pwd)\s*[:：=]?\s*([A-Za-z0-9]{4,8})", re.I)
_last = [0.0]

def act(page):
    b = page.get_by_text("进入网站")
    if b.count():
        b.first.click(); page.wait_for_timeout(4000)

def fetch(s, url):
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    p = s.fetch(url, page_action=act, timeout=60000)
    _last[0] = time.time()
    if p.status != 200:
        raise RuntimeError(f"HTTP {p.status} on {url}")
    return p.html_content if hasattr(p, "html_content") else str(p.body)

def pan_type(u):
    for name, pat in PANS:
        if re.search(pat, u, re.I):
            return name
    return "未知"

def search(s, kw):
    h = fetch(s, f"{BASE}search-{quote(kw)}.htm")
    ids = []
    for m in re.finditer(r'href="[^"]*?(thread-(\d+)\.htm)"', h):
        if m.group(2) not in ids:
            ids.append(m.group(2))
    return ids

def parse_thread(h, url):
    t = re.search(r"<title>(.*?)</title>", h, re.S)
    title = html.unescape(re.sub(r"\s+", " ", t.group(1))).strip() if t else ""
    title = re.sub(r"\s*-\s*空盘汇.*$", "", title)
    rows, seen = [], set()
    for m in LINK_RE.finditer(h):
        u = html.unescape(m.group(0)).rstrip("'\"<>.,;)）】")
        if u in seen:
            continue
        seen.add(u)
        code = ""
        q = re.search(r"[?&]pwd=([A-Za-z0-9]+)", u)
        if q:
            code = q.group(1)
        else:
            near = re.sub(r"<[^>]+>", " ", h[m.end():m.end() + 300])
            c = CODE_RE.search(near)
            code = c.group(1) if c else ""
        rows.append({"title": title, "thread_url": url, "pan_type": pan_type(u), "share_url": u, "extraction_code": code})
    return title, rows

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--max", type=int, default=10)
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip(); used = kw
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows, threads = [], []
    with StealthySession(headless=True) as s:
        ids = search(s, kw)
        core = GENERIC.sub("", kw).strip()
        if not ids and core and core != kw:
            print(f"[info] 0 threads for {kw!r}; retrying with {core!r}", file=sys.stderr)
            used = core; ids = search(s, core)
        for tid in ids[:a.max]:
            url = f"{BASE}thread-{tid}.htm"
            try:
                title, r = parse_thread(fetch(s, url), url)
            except Exception as e:
                print(f"[warn] {url}: {e}", file=sys.stderr); continue
            threads.append({"thread_url": url, "title": title, "links": len(r)})
            for x in r:
                x["scraped_at"] = ts
            rows += r
    base = os.path.join(a.out, f"hmxz_{kw}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "searched": used, "scraped_at": ts, "threads_found": len(ids),
                   "threads": threads, "links": rows}, f, ensure_ascii=False, indent=1)
    cols = ["title", "thread_url", "pan_type", "share_url", "extraction_code", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["pan_type"]] = by.get(r["pan_type"], 0) + 1
    print(f"{len(ids)} threads found, {len(threads)} opened, {len(rows)} links (searched {used!r}) @ {ts}\n{base}.json\n{base}.csv\nby pan: {by}")
    for r in rows:
        print(f"{r['pan_type']}\t{r['share_url']}\t{r['extraction_code'] or '-'}\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
