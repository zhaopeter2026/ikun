#!/workspace/scrapling-env/bin/python
"""Search slowread.net's 网盘 search (so.slowread.net) for a keyword and save results.

How the site works: the search box on https://slowread.net/k/k.html is a plain HTML form
that POSTs (application/x-www-form-urlencoded) to https://so.slowread.net/search with fields
  query=<keyword>, pan_type=<''|quark|ali|baidu|xunlei|uc|123pan|guangyapan>, adblock_disabled=false
Results are server-rendered as .result-card blocks (no pagination). Each card links to
/go?to=<base64(share URL)>; the share URL is decoded locally (the /go redirect and the drive
links are NOT requested). Title, drive icon, and link are all the page shows (no size/date).

Usage: slowread_search.py <keyword> [outdir]
"""
import sys, os, csv, base64, time, datetime
from urllib.parse import urlparse, parse_qs, urljoin
from zoneinfo import ZoneInfo
from scrapling.fetchers import FetcherSession

SEARCH = "https://so.slowread.net/search"
PAN_TYPES = ["", "quark", "ali", "baidu", "xunlei", "uc", "123pan", "guangyapan"]
ICON_NAMES = {"quark": "夸克网盘", "ali": "阿里云盘", "baidu": "百度网盘", "xunlei": "迅雷云盘",
              "uc": "UC网盘", "123pan": "123云盘", "guangyapan": "光鸭云盘"}
DOMAINS = [("quark.cn", "夸克网盘"), ("aliyundrive.com", "阿里云盘"), ("alipan.com", "阿里云盘"),
           ("baidu.com", "百度网盘"), ("xunlei.com", "迅雷云盘"), ("uc.cn", "UC网盘"),
           ("123pan", "123云盘"), ("123684.com", "123云盘"), ("123865.com", "123云盘"),
           ("115.com", "115网盘"), ("115cdn.com", "115网盘"), ("189.cn", "天翼云盘"),
           ("139.com", "移动云盘"), ("lanzou", "蓝奏云"), ("pikpak", "PikPak"), ("guangya", "光鸭云盘")]
CAP = 100

def drive_of(url, icon):
    host = urlparse(url).netloc.lower()
    for d, name in DOMAINS:
        if d in host:
            return name
    return ICON_NAMES.get(icon, icon or "未知")

def decode_go(href):
    q = parse_qs(urlparse(href).query).get("to", [""])[0]
    if not q:
        return href
    try:
        return base64.b64decode(q + "=" * (-len(q) % 4)).decode("utf-8")
    except Exception:
        return ""

def search(keyword):
    rows, seen = [], set()
    with FetcherSession() as s:
        for pt in PAN_TYPES:   # '' = 全部; per-type queries catch anything the 全部 list truncates
            p = s.post(SEARCH, data={"adblock_disabled": "false", "pan_type": pt, "query": keyword},
                       headers={"Referer": "https://slowread.net/k/k.html"})
            if p.status != 200:
                print(f"[warn] pan_type={pt!r} HTTP {p.status}", file=sys.stderr)
            for c in p.css(".result-card"):
                href = c.css("a::attr(href)").get() or ""
                url = decode_go(href)
                if not url or url in seen:
                    continue
                seen.add(url)
                icon = c.css("img::attr(alt)").get() or ""
                qs = parse_qs(urlparse(url).query)
                code = (qs.get("pwd") or qs.get("password") or qs.get("code") or [""])[0]
                txt = " ".join(c.css("*::text").getall())
                rows.append({
                    "title": (c.css("h3::text").get() or "").strip(),
                    "drive": drive_of(url, icon),
                    "share_url": url,
                    "extraction_code": code,
                    "size": "", "date": "",
                    "slowread_url": urljoin(SEARCH, href),
                })
                if len(rows) >= CAP:
                    return rows
            time.sleep(1.5)
    return rows

def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    kw = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(__file__))
    ts = datetime.datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows = search(kw)
    for r in rows:
        r["scraped_at"] = ts
    cols = ["title", "drive", "share_url", "extraction_code", "size", "date", "slowread_url", "scraped_at"]
    base = os.path.join(outdir, f"slowread_{kw}")
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(f"# slowread.net 网盘搜索: {kw}\n\nScraped: {ts} · Source: POST {SEARCH} · Results: {len(rows)}\n\n")
        f.write("| # | 标题 | 网盘 | 分享链接 | 提取码 | slowread 跳转链接 |\n|---|---|---|---|---|---|\n")
        for i, r in enumerate(rows, 1):
            t = r["title"].replace("|", "\\|")
            f.write(f"| {i} | {t} | {r['drive']} | {r['share_url']} | {r['extraction_code'] or '—'} | {r['slowread_url']} |\n")
        if not rows:
            f.write("_No results._\n")
    print(f"{len(rows)} results @ {ts}\n{base}.csv\n{base}.md")
    for r in rows:
        print(f"{r['drive']}\t{r['share_url']}\t{r['extraction_code'] or '-'}\t{r['title']}")

if __name__ == "__main__":
    main()
