#!/workspace/scrapling-env/bin/python
"""Search pansearch.me (Next.js netdisk search engine; content aggregated from Telegram resource channels) for a keyword.

Access: public Next.js data JSON, no login, no key, no signing.
  1) GET https://www.pansearch.me/  -> read "buildId" from __NEXT_DATA__ (changes on every site deploy, so read each run)
  2) GET /_next/data/<buildId>/search.json?keyword=<kw>&offset=<(page-1)*9>[&pan=quark|aliyundrive|baidu|xunlei]
     -> pageProps.data = {total, time, data:[{id, content(html: 名称/描述/链接 <a href>, tags), pan, image, time}]}, limit 9
  Some '频道新增资源' entries carry no link at all; they are kept with share_url empty.
  Repost-style list entries ('1、title:url;' x N) are split per line; the same link reposted by several items is kept once.
Rate limit: the site answers HTTP 429 (请求过于频繁, retryAfter) when hit too fast. We wait the advised time once,
  retry once, and on a second 429 stop gracefully and save what we have.
robots.txt: Disallow /rearq/ only.
Polite: 1.5-2.5 s random delay between requests; 1 + --max-pages requests per run.
Usage: pansearch_search.py KEYWORD [--pan all|quark|aliyundrive|baidu|xunlei] [--max-pages N (default 3)] [--out DIR]
"""
import os, sys, re, csv, json, time, random, datetime, argparse, html
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from scrapling.fetchers import Fetcher

BASE = "https://www.pansearch.me"
PT = ZoneInfo("America/Los_Angeles")
PANS = {"quark": "夸克网盘", "aliyundrive": "阿里云盘", "baidu": "百度网盘", "xunlei": "迅雷云盘"}
LIMIT = 9
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
CODE_RE = re.compile(r"(?:提取码|密码|访问码)\s*[:：]?\s*([A-Za-z0-9]{4,8})")
_last = [0.0]

class RateLimited(Exception):
    pass

def get(url, ref=BASE + "/"):
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    waited_429 = False
    for attempt in (1, 2, 3):
        r = Fetcher.get(url, timeout=40, headers={"Referer": ref, "Accept": "application/json, text/html"})
        _last[0] = time.time()
        body = r.body.decode("utf-8", "replace") if isinstance(r.body, bytes) else str(r.body)
        if r.status == 200:
            return body
        if r.status == 429:
            if waited_429:
                raise RateLimited(url)
            ra = None
            try:
                ra = float((r.headers or {}).get("retry-after") or (r.headers or {}).get("Retry-After") or 0) or None
            except Exception:
                pass
            if ra is None:
                m = re.search(r'"retryAfter"\s*:\s*(\d+(?:\.\d+)?)', body)
                ra = float(m.group(1)) if m else 10.0
            ra = min(max(ra, 2.0), 120.0)
            print(f"[warn] 429 请求过于频繁 - waiting {ra:.0f} s once", file=sys.stderr)
            time.sleep(ra); waited_429 = True; continue
        if r.status in (500, 502, 503, 504) and attempt == 1:
            print(f"[warn] HTTP {r.status} - retrying in 10 s", file=sys.stderr); time.sleep(10); continue
        raise RuntimeError(f"HTTP {r.status} on {url}")
    raise RateLimited(url)

def build_id():
    h = get(BASE + "/")
    m = re.search(r'"buildId"\s*:\s*"([^"]+)"', h)
    if not m:
        raise RuntimeError("buildId not found on homepage (site layout changed?)")
    return m.group(1)

def to_pt(s):
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(PT).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return s or ""

def strip(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or ""))

PAN_URL = [("夸克网盘", r"pan\.quark\.cn/s/"), ("阿里云盘", r"(?:aliyundrive\.com|alipan\.com)/s/"), ("百度网盘", r"pan\.baidu\.com/"),
           ("迅雷云盘", r"pan\.xunlei\.com/s/"), ("天翼云盘", r"cloud\.189\.cn/"), ("UC网盘", r"drive\.uc\.cn/s/"),
           ("115网盘", r"(?:115\.com|115cdn\.com)/s/"), ("123云盘", r"(?:123pan\.com|123684\.com|123865\.com|123912\.com)/s/"),
           ("移动云盘", r"(?:caiyun|yun)\.139\.com/")]

def url_pan(u):
    for name, pat in PAN_URL:
        if re.search(pat, u, re.I):
            return name
    return ""

def clean_url(u):
    return html.unescape(u).strip().rstrip("#.,;)）】")

def parse_item(it):
    """One API item -> rows. Normal post: 名称/描述/链接 -> 1 row per pan link. List post ('1、title:url;' lines,
    e.g. collections reposted from Aliyun groups) -> 1 row per line with that line's own title. Non-netdisk URLs
    (channel / mirror-site ads) are dropped. Items without any pan link are kept once with share_url empty."""
    c = it.get("content") or ""
    text = strip(c)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    title = ""
    for l in lines:
        m = re.match(r"^(?:名称|资源名称|标题|频道新增资源)\s*[:：]\s*(.+)$", l)
        if m:
            title = m.group(1).strip(); break
    if not title and lines:
        title = re.sub(r"\s*[:：]?\s*https?://\S+", "", lines[0]).strip()
    desc = ""
    m = re.search(r"描述\s*[:：]\s*(.+?)(?:\n\s*\n|链接[:：]|$)", text, re.S)
    if m:
        desc = re.sub(r"\s+", " ", m.group(1)).strip()[:200]
    gc = CODE_RE.search(text)
    base_pan = PANS.get(it.get("pan"), it.get("pan") or "")
    pairs, seen = [], set()
    for l in lines:                       # list-style lines carry their own title
        m = re.match(r"^\d+\s*[、.]\s*(.+?)\s*[:：]\s*(https?://\S+?)\s*;?$", l)
        if m and url_pan(m.group(2)):
            u = clean_url(m.group(2))
            if u not in seen:
                seen.add(u); pairs.append((m.group(1).strip(), u))
    for u in re.findall(r'href="([^"]+)"', c) + URL_RE.findall(text):
        u = clean_url(u)
        if u in seen or not url_pan(u):
            continue
        seen.add(u); pairs.append((title, u))
    rows = []
    for t, u in pairs or [(title, "")]:
        q = re.search(r"[?&]pwd=([A-Za-z0-9]+)", u)
        rows.append({"title": t, "pan_type": url_pan(u) or base_pan, "share_url": u,
                     "extraction_code": q.group(1) if q else (gc.group(1) if gc and u and len(pairs) == 1 else ""),
                     "time": to_pt(it.get("time") or ""), "id": it.get("id"), "desc": desc})
    return rows

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--pan", default="all", choices=["all"] + list(PANS))
    ap.add_argument("--max-pages", type=int, default=3); ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip()
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows, seen, seen_urls, dup, total, fetched, stopped = [], set(), set(), 0, None, 0, ""
    try:
        bid = build_id()
        for page in range(1, max(1, a.max_pages) + 1):
            q = {"keyword": kw}
            if page > 1:
                q["offset"] = (page - 1) * LIMIT
            if a.pan != "all":
                q["pan"] = a.pan
            d = json.loads(get(f"{BASE}/_next/data/{bid}/search.json?{urlencode(q)}", ref=f"{BASE}/search?{urlencode(q)}"))
            fetched += 1
            data = ((d.get("pageProps") or {}).get("data") or {})
            total = data.get("total", total)
            items = data.get("data") or []
            for it in items:
                if it.get("id") in seen:
                    continue
                seen.add(it.get("id"))
                for r in parse_item(it):
                    if r["share_url"] and r["share_url"] in seen_urls:
                        dup += 1; continue
                    seen_urls.add(r["share_url"]); r["scraped_at"] = ts; rows.append(r)
            if not items or (total is not None and page * LIMIT >= total):
                break
    except RateLimited:
        stopped = "429 rate limit twice - stopped early, partial results saved"
        print(f"[warn] {stopped}", file=sys.stderr)
    sfx = f"_{a.pan}" if a.pan != "all" else ""
    base = os.path.join(a.out, f"pansearch_{kw}{sfx}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "pan": a.pan, "scraped_at": ts, "site_total": total, "pages_fetched": fetched, "duplicate_links_skipped": dup,
                   "stopped": stopped or None, "results": rows}, f, ensure_ascii=False, indent=1)
    cols = ["title", "pan_type", "share_url", "extraction_code", "time", "id", "desc", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["pan_type"]] = by.get(r["pan_type"], 0) + 1
    nolink = sum(1 for r in rows if not r["share_url"])
    print(f"{len(rows)} rows (site total {total}, pages fetched {fetched}, {nolink} without link, {dup} repeated links skipped) @ {ts}\n{base}.json\n{base}.csv\nby pan: {by}")
    for r in rows:
        print(f"{r['pan_type']}\t{r['share_url'] or '-'}\t{r['extraction_code']}\t{r['time']}\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
