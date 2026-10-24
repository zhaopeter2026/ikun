#!/workspace/scrapling-env/bin/python
"""Search 皮卡搜索 pikasoo.top (Django netdisk search engine, 图欧科技 circle - sister of 咔帕搜索 cuppaso / 千帆 qianfan
style sites; results overlap with cuppaso) for a keyword.

Access: plain HTTP, server-rendered HTML, no login, no anti-bot seen. Real share links are right in the search page:
  GET /search/?pan=<pan>&type=<type>&q=<kw>&page=N      10 results/page
  pan  (from the site's own menu, data-pan): all | aliyundrive | quark | baidu | lanzou | cloud189
  type (from the site's type tabs):          all | folder | video | audio | image | doc | app | zip | others
  card: <div class="search-item"><a href="<share link>"><h2 class="search-title"><img alt="夸克网盘资源">title</h2></a>
        .search-des <p> = file list, 文件大小, 数量, .search-creator (uploader), .report data-type/data-id
  The first 1-2 cards on each page are ads (夸克扩容 / AIGC 平台) - skipped (no .search-note / non-pan host / ad words).
robots.txt: Disallow /login/ only.
Polite: 1.5-2.5 s random delay between requests; <= --max-pages requests per run.
Usage: pikasoo_search.py KEYWORD [--pan all|aliyundrive|quark|baidu|lanzou|cloud189] [--type all|folder|video|...]
                         [--max-pages N (default 2)] [--out DIR]
"""
import os, sys, re, csv, json, time, random, datetime, argparse, html
from urllib.parse import urlencode, urlparse, parse_qs
from zoneinfo import ZoneInfo
from scrapling.fetchers import Fetcher

BASE = "https://www.pikasoo.top"
PT = ZoneInfo("America/Los_Angeles")
PANS = ["all", "aliyundrive", "quark", "baidu", "lanzou", "cloud189"]
TYPES = ["all", "folder", "video", "audio", "image", "doc", "app", "zip", "others"]
HOSTS = [("quark.cn", "夸克网盘"), ("aliyundrive.com", "阿里云盘"), ("alipan.com", "阿里云盘"), ("baidu.com", "百度网盘"),
         ("lanzou", "蓝奏云"), ("189.cn", "天翼云盘"), ("xunlei.com", "迅雷云盘"), ("uc.cn", "UC网盘")]
AD_WORDS = re.compile(r"扩容|新人|AIGC|人工智能|Nano Banana|免费体验|点此转存")
_last = [0.0]

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

def txt(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()

def pan_of(u):
    host = urlparse(u).netloc.lower()
    for d, n in HOSTS:
        if d in host:
            return n
    return ""

def parse(h):
    rows, ads = [], 0
    pages = [int(x) for x in re.findall(r"[?&]page=(\d+)", h)]
    for blk in re.split(r'<div class="search-item">', h)[1:]:
        m = re.search(r'<a href="([^"]+)"[^>]*>\s*<h2 class="search-title">(.*?)</h2>', blk, re.S)
        if not m:
            continue
        url, title = html.unescape(m.group(1)).strip(), txt(m.group(2))
        if not pan_of(url) or 'class="search-note"' not in blk or AD_WORDS.search(title):
            ads += 1; continue
        des = [txt(p) for p in re.findall(r"<p>(.*?)</p>", blk.split('class="search-des"', 1)[-1].split('class="search-note"')[0], re.S)]
        size = re.search(r"文件大小:\s*([^<]+)<", blk)
        num = re.search(r"数量:\s*(\d+)", blk)
        up = re.search(r'class="search-creator"[^>]*>(?:<i[^>]*></i>)?\s*([^<]*)<', blk)
        rep = re.search(r'class="report" data-type="([^"]*)" data-id="(\d+)"', blk)
        qs = parse_qs(urlparse(url).query)
        code = (qs.get("pwd") or [""])[0]
        if not code:
            c = re.search(r"(?:提取码|密码)[:：]\s*([A-Za-z0-9]{4,8})", " ".join(des))
            code = c.group(1) if c else ""
        rows.append({"title": title, "pan_type": pan_of(url), "share_url": url, "extraction_code": code,
                     "size": size.group(1).strip() if size else "", "file_count": int(num.group(1)) if num else None,
                     "files": " / ".join(d for d in des[1:9] if d != "......"), "uploader": up.group(1).strip() if up else "",
                     "site_id": f"{rep.group(1)}:{rep.group(2)}" if rep else ""})
    return rows, (max(pages) if pages else 1), ads

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--pan", default="all", choices=PANS)
    ap.add_argument("--type", default="all", choices=TYPES); ap.add_argument("--max-pages", type=int, default=2)
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip()
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows, seen, last, fetched, ads = [], set(), 1, 0, 0
    for page in range(1, max(1, a.max_pages) + 1):
        q = {"pan": a.pan, "type": a.type, "q": kw}
        if page > 1:
            q["page"] = page
        recs, lp, ad = parse(get(f"{BASE}/search/?{urlencode(q)}"))
        fetched += 1; ads += ad; last = max(last, lp)
        for r in recs:
            if r["share_url"] in seen:
                continue
            seen.add(r["share_url"]); r["page"] = page; r["scraped_at"] = ts; rows.append(r)
        if not recs or page >= last:
            break
    sfx = "".join(f"_{v}" for v in (a.pan, a.type) if v != "all")
    base = os.path.join(a.out, f"pikasoo_{kw}{sfx}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "pan": a.pan, "type": a.type, "scraped_at": ts, "site_pages": last,
                   "site_total_approx": last * 10, "pages_fetched": fetched, "ads_skipped": ads, "results": rows},
                  f, ensure_ascii=False, indent=1)
    cols = ["title", "pan_type", "share_url", "extraction_code", "size", "file_count", "files", "uploader", "site_id", "page", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["pan_type"]] = by.get(r["pan_type"], 0) + 1
    print(f"{len(rows)} results (site {last} pages ≈ {last*10}; fetched {fetched}; {ads} ads skipped) @ {ts}\n{base}.json\n{base}.csv\nby pan: {by}")
    for r in rows:
        print(f"{r['pan_type']}\t{r['share_url']}\t{r['extraction_code'] or '-'}\t{r['size']}\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
