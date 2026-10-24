#!/workspace/scrapling-env/bin/python
"""Search 夸克网盘网 kuake5.com (Xiuno BBS 4.0.4 forum; same software as 海绵小站 hmxz.org, but a separate site) for a keyword
and pull netdisk links from threads.

Access: plain HTTP (no anti-bot wall, no browser needed). No login. Never touches /user-* (robots-disallowed).
Endpoints: GET /search-<urlencoded kw>.htm -> thread links 'thread-<id>.htm'; thread pages show pan links to guests,
  except posts using the reply-to-view plugin ("您好，本帖含有特定内容，请回复后再查看"). Replying needs an account, which
  we do not do: such threads are kept as one row with pan_type 需回复 and an empty share_url.
  robots.txt (User-agent *) disallows /admin, /user-*, /serach-* only; search-* and thread-* allowed.
Polite: 1.5-2.5 s random delay between requests; <= max+2 requests per run (search, optional fallback search, N threads).
If a keyword containing 合集/全集 etc. yields 0 threads, retries once with those words stripped.
Usage: kuake5_search.py KEYWORD [--max N (default 10)] [--out DIR (default /workspace/scrapes/out)]
"""
import os, sys, re, csv, json, time, random, datetime, argparse, html
from urllib.parse import quote
from zoneinfo import ZoneInfo
from scrapling.fetchers import Fetcher

BASE = "https://kuake5.com/"
PT = ZoneInfo("America/Los_Angeles")
GENERIC = re.compile(r"(合集|全集|系列|大全|全套|收藏版?|打包)")
PANS = [("夸克网盘", r"pan\.quark\.cn/s/"), ("百度网盘", r"pan\.baidu\.com/"), ("阿里云盘", r"(?:aliyundrive\.com|alipan\.com)/s/"),
        ("天翼云盘", r"cloud\.189\.cn/"), ("移动云盘", r"(?:caiyun\.139\.com|yun\.139\.com)/"), ("115网盘", r"(?:115\.com|115cdn\.com)/s/"),
        ("UC网盘", r"drive\.uc\.cn/s/"), ("迅雷云盘", r"pan\.xunlei\.com/s/"), ("123云盘", r"(?:123pan\.com|123684\.com|123865\.com|123912\.com)/s/"),
        ("光鸭云盘", r"guangyapan\.com/"), ("蓝奏云", r"lanzou\w*\.com/")]
LINK_RE = re.compile(r"https?://(?:[\w-]+\.)*(?:" + "|".join(p for _, p in PANS) + r")[^\s\"'<>&]*(?:&amp;[^\s\"'<>&]*|&[^\s\"'<>&]*)*", re.I)
CODE_RE = re.compile(r"(?:提取码|密码|访问码|pwd)\s*[:：=]?\s*([A-Za-z0-9]{4,8})", re.I)
HIDDEN = "回复后再查看"
_last = [0.0]

def fetch(url):
    if "/user-" in url:
        raise ValueError("refusing /user-* (robots-disallowed)")
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    for attempt in (1, 2):
        r = Fetcher.get(url, timeout=40, headers={"Referer": BASE})
        _last[0] = time.time()
        if r.status == 200:
            return r.body.decode("utf-8", "replace") if isinstance(r.body, bytes) else str(r.body)
        if r.status in (429, 500, 502, 503, 504) and attempt == 1:
            print(f"[warn] HTTP {r.status} - retrying in 10 s", file=sys.stderr); time.sleep(10); continue
        raise RuntimeError(f"HTTP {r.status} on {url}")

def pan_type(u):
    for name, pat in PANS:
        if re.search(pat, u, re.I):
            return name
    return "未知"

def search(kw):
    h = fetch(f"{BASE}search-{quote(kw)}.htm")
    # only the result list (main column), not the sidebar rankings
    main = h.split('筛选条件')[0]
    ids = []
    for m in re.finditer(r'href="[^"]*?(thread-(\d+)\.htm)"', main):
        if m.group(2) not in ids:
            ids.append(m.group(2))
    return ids

def parse_thread(h, url):
    t = re.search(r"<title>(.*?)</title>", h, re.S)
    title = html.unescape(re.sub(r"\s+", " ", t.group(1))).strip() if t else ""
    title = re.sub(r"\s*-[^-]*-\s*夸克网盘网.*$", "", title).strip()
    # the first post's body; sidebar/footer have no pan links but cut them off anyway
    body = h
    m = re.search(r'class="message[^"]*"', h)
    if m:
        end = h.find('id="comments"', m.end())
        body = h[m.start(): end if end > 0 else len(h)]
    hidden = HIDDEN in body
    rows, seen = [], set()
    for m in LINK_RE.finditer(body):
        u = html.unescape(m.group(0)).rstrip("'\"<>.,;)）】")
        if u in seen:
            continue
        seen.add(u)
        q = re.search(r"[?&]pwd=([A-Za-z0-9]+)", u)
        if q:
            code = q.group(1)
        else:
            near = re.sub(r"<[^>]+>", " ", body[m.end():m.end() + 300])
            c = CODE_RE.search(near)
            code = c.group(1) if c else ""
        rows.append({"title": title, "thread_url": url, "status": "可见" + ("（另有需回复内容）" if hidden else ""),
                     "pan_type": pan_type(u), "share_url": u, "extraction_code": code})
    if not rows:
        rows.append({"title": title, "thread_url": url, "status": "需回复" if hidden else "无链接",
                     "pan_type": "需回复" if hidden else "—", "share_url": "", "extraction_code": ""})
    return title, hidden, rows

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--max", type=int, default=10)
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip(); used = kw
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows, threads = [], []
    ids = search(kw)
    core = GENERIC.sub("", kw).strip()
    if not ids and core and core != kw:
        print(f"[info] 0 threads for {kw!r}; retrying with {core!r}", file=sys.stderr)
        used = core; ids = search(core)
    for tid in ids[:a.max]:
        url = f"{BASE}thread-{tid}.htm"
        try:
            title, hidden, r = parse_thread(fetch(url), url)
        except Exception as e:
            print(f"[warn] {url}: {e}", file=sys.stderr); continue
        n = sum(1 for x in r if x["share_url"])
        threads.append({"thread_url": url, "title": title, "links": n, "reply_required": hidden})
        for x in r:
            x["scraped_at"] = ts
        rows += r
    base = os.path.join(a.out, f"kuake5_{kw}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "searched": used, "scraped_at": ts, "threads_found": len(ids),
                   "threads": threads, "links": rows}, f, ensure_ascii=False, indent=1)
    cols = ["title", "thread_url", "status", "pan_type", "share_url", "extraction_code", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["pan_type"]] = by.get(r["pan_type"], 0) + 1
    nl = sum(1 for r in rows if r["share_url"])
    print(f"{len(ids)} threads found, {len(threads)} opened, {nl} links, {sum(t['reply_required'] for t in threads)} threads 需回复 "
          f"(searched {used!r}) @ {ts}\n{base}.json\n{base}.csv\nby pan: {by}")
    for r in rows:
        print(f"{r['pan_type']}\t{r['share_url'] or '-'}\t{r['extraction_code'] or '-'}\t{r['status']}\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
