#!/workspace/scrapling-env/bin/python
"""Search 爱盼（影视）www.aipan.me (films / TV only, Next.js) for a keyword and collect the share links of each title.

Access (robots.txt: Allow /, Disallow /admin and /api - respected, the script never requests /api/*):
  1) Search: /search?q=<kw> is a public page but rendered client-side (its own front-end calls /api/movies/search);
     the server HTML has no results and the sitemap has no titles. So the search page is loaded once in a headless
     browser (Scrapling StealthyFetcher) like a normal visitor, and the rendered cards are read:
       <a href="/movie/<id>"> <h3 title="...">title</h3> <p>subtitle</p> <p>genres</p> ... 评分 <span>7.8</span>
  2) Detail: GET /movie/<id> (allowed, plain HTTP, server-rendered). Links live in the RSC flight payload
     (self.__next_f.push) as {"aipanId","movieTitle","url","linkLabel","kindLabel"}; size / 有效链接 badge come from
     the same row. Metadata (year, alt names, genres) from the page's JSON-LD. Types seen in tests: 夸克 / 迅雷 / 磁力 (site meta also claims 光鸭云盘, none seen yet).
  Extraction codes: none seen on aipan (quark links are open); taken from ?pwd= or "提取码" text if ever present.
Polite: one browser at a time, 1.5-2.5 s random delay between all requests; 1 search + <= --max detail pages per run.
No login, no form submits.
Usage: aipan_search.py KEYWORD [--max N (detail pages to open, default 10)] [--out DIR]
Exit: 0 ok (incl. 0 results), 2 site error.
"""
import os, sys, re, csv, json, time, random, datetime, argparse, html
from urllib.parse import quote, urlparse, parse_qs
from zoneinfo import ZoneInfo

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/workspace/scrapling-env/ms-playwright")
from scrapling.fetchers import Fetcher, StealthyFetcher

BASE = "https://www.aipan.me"
PT = ZoneInfo("America/Los_Angeles")
HOSTS = [("quark.cn", "夸克网盘"), ("guangyapan", "光鸭云盘"), ("guangya", "光鸭云盘"), ("aliyundrive.com", "阿里云盘"),
         ("alipan.com", "阿里云盘"), ("baidu.com", "百度网盘"), ("xunlei.com", "迅雷云盘"), ("uc.cn", "UC网盘"),
         ("189.cn", "天翼云盘"), ("139.com", "移动云盘"), ("123pan", "123云盘"), ("123684", "123云盘"), ("115", "115网盘")]
QUAL = re.compile(r"4K|2160[pP]|1080[pP]|720[pP]|HDR10\+?|HDR|DV|杜比视界|Dolby|WEB-DL|BluRay|蓝光|REMUX|60FPS|高码|内封[^ \]】]*|中字|国语|双语", re.I)
_last = [0.0]

def wait():
    w = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and w > 0:
        time.sleep(w)

def txt(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()

def search(kw):
    """Load the public search page in a headless browser and return the rendered cards."""
    def settle(page):
        try:
            page.wait_for_function("() => /个结果|未找到相关内容/.test(document.body.innerText)", timeout=30000)
        except Exception:
            pass
    wait()
    r = StealthyFetcher.fetch(f"{BASE}/search?q={quote(kw)}", headless=True, network_idle=True,
                              page_action=settle, timeout=60000)
    _last[0] = time.time()
    if r.status != 200:
        raise RuntimeError(f"HTTP {r.status} on search page")
    h = r.html_content if hasattr(r, "html_content") else r.body.decode("utf-8", "replace")
    cnt = re.search(r'font-medium">(\d+)</span>\s*个结果', h)
    cards, seen = [], set()
    for m in re.finditer(r'<a href="/movie/(\d+)"[^>]*>(.*?)</a>', h, re.S):
        mid, blk = m.group(1), m.group(2)
        if mid in seen:
            continue
        seen.add(mid)
        t = re.search(r'<h3[^>]*>(.*?)</h3>', blk, re.S)
        ps = [txt(p) for p in re.findall(r'<p[^>]*>(.*?)</p>', blk, re.S)]
        rate = re.search(r'评分</span><span[^>]*>([^<]+)<', blk)
        cards.append({"id": mid, "title": txt(t.group(1)) if t else "", "subtitle": ps[0] if ps else "",
                      "genres": ps[1] if len(ps) > 1 else "", "rating": rate.group(1).strip() if rate else "",
                      "detail_url": f"{BASE}/movie/{mid}"})
    none = "未找到相关内容" in h
    return cards, (int(cnt.group(1)) if cnt else (0 if none else len(cards)))

def get(url):
    wait()
    for attempt in (1, 2):
        r = Fetcher.get(url, timeout=40, headers={"Referer": BASE + "/"})
        _last[0] = time.time()
        if r.status == 200:
            return r.body.decode("utf-8", "replace") if isinstance(r.body, bytes) else str(r.body)
        if r.status in (429, 500, 502, 503, 504) and attempt == 1:
            print(f"[warn] HTTP {r.status} - retrying in 10 s", file=sys.stderr); time.sleep(10); wait(); continue
        raise RuntimeError(f"HTTP {r.status} on {url}")

def pan_of(u):
    if u.startswith("magnet:"):
        return "磁力"
    if u.startswith("ed2k:"):
        return "电驴"
    host = urlparse(u).netloc.lower()
    for d, n in HOSTS:
        if d in host:
            return n
    return host

def detail(h):
    meta = {}
    ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', h, re.S)
    if ld:
        try:
            j = json.loads(ld.group(1))
            meta = {"name": j.get("name", ""), "alt_names": j.get("alternateName", ""), "year": str(j.get("datePublished", "")),
                    "genres": " ".join(j.get("genre") or [])}
        except ValueError:
            pass
    fl = ""
    for s in re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', h, re.S):
        try:
            fl += json.loads('"' + s + '"')
        except ValueError:
            pass
    cnt = re.search(r'"panCount":(\d+),"magnetCount":(\d+)', fl)
    links, seen = [], set()
    for m in re.finditer(r'\{"aipanId":\d+,"movieTitle":"(?:[^"\\]|\\.)*","url":("(?:[^"\\]|\\.)*"),"linkLabel":("(?:[^"\\]|\\.)*"|null),"kindLabel":("(?:[^"\\]|\\.)*"|null)', fl):
        url, label, kind = (json.loads(x) or "" for x in m.groups())
        if url in seen:
            continue
        seen.add(url)
        # the row that holds this link: from its keyed div ("<url>-<n>") up to this feedback widget
        start = fl.rfind(f'"{url}-', 0, m.start())
        row = fl[start:m.start()] if start >= 0 else ""
        size = re.findall(r'"text-xs text-gray-500 mt-1","children":"([^"]*)"', row)
        if not label:
            t = re.findall(r'"font-medium text-white text-sm line-clamp-1","children":"([^"]*)"', row)
            label = t[-1] if t else ""
        badge = re.findall(r'"children":"(有效链接|疑似失效|已失效|失效|待验证)"', row)
        code = (parse_qs(urlparse(url).query).get("pwd") or [""])[0] if url.startswith("http") else ""
        if not code:
            c = re.search(r"(?:提取码|密码|访问码)[:：\s]*([A-Za-z0-9]{4,8})", label + " " + row)
            code = c.group(1) if c else ""
        q = []
        for x in QUAL.findall(label):
            if x.upper() not in (y.upper() for y in q):
                q.append(x)
        links.append({"pan_type": pan_of(url), "url": url, "extraction_code": code, "label": label,
                      "site_kind": kind, "size": size[-1] if size else "", "status": badge[-1] if badge else "",
                      "quality": " ".join(q)})
    meta["site_pan_count"], meta["site_magnet_count"] = (int(cnt.group(1)), int(cnt.group(2))) if cnt else (None, None)
    return meta, links

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--max", type=int, default=10, help="detail pages to open (default 10)")
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip()
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    try:
        cards, total = search(kw)
    except Exception as e:
        print(f"爱盼搜索页打开失败: {e}", file=sys.stderr); sys.exit(2)
    results = []
    for c in cards[:max(0, a.max)]:
        try:
            meta, links = detail(get(c["detail_url"]))
        except Exception as e:
            print(f"[warn] 详情页失败 {c['detail_url']}: {e}", file=sys.stderr); meta, links = {"error": str(e)}, []
        results.append({**c, "year": meta.get("year", ""), "alt_names": meta.get("alt_names", ""),
                        "genres": meta.get("genres") or c["genres"], "site_pan_count": meta.get("site_pan_count"),
                        "site_magnet_count": meta.get("site_magnet_count"), "links": links, "scraped_at": ts,
                        **({"error": meta["error"]} if "error" in meta else {})})
    base = os.path.join(a.out, f"aipan_{kw}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "site": BASE, "scraped_at": ts,
                   "method": "search page rendered in headless browser (StealthyFetcher) + /movie/{id} SSR HTML; no /api/* calls",
                   "site_total": total, "cards_found": len(cards), "details_opened": len(results), "results": results},
                  f, ensure_ascii=False, indent=1)
    cols = ["title", "year", "rating", "detail_url", "pan_type", "url", "extraction_code", "size", "status", "quality", "label", "scraped_at"]
    flat = [{**{k: r.get(k, "") for k in cols[:4]}, **{k: l.get(k, "") for k in cols[4:11]}, "scraped_at": ts}
            for r in results for l in r["links"]]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(flat)
    by = {}
    for l in flat:
        by[l["pan_type"]] = by.get(l["pan_type"], 0) + 1
    print(f"爱盼影视搜索「{kw}」: 站内 {total} 部影视，打开详情 {len(results)} 个，共 {len(flat)} 条链接 @ {ts}")
    print(f"{base}.json\n{base}.csv\n按网盘: {by}")
    if not cards:
        print("（没有结果——爱盼只收录电影/电视剧，非影视内容请换其他站）")
    for r in results:
        print(f"\n■ {r['title']} ({r['year'] or '?'}) 评分 {r['rating'] or '-'}  {r['detail_url']}  [网盘 {r['site_pan_count']} / 磁力 {r['site_magnet_count']}]")
        for l in r["links"]:
            print(f"  {l['pan_type']}\t{l['url'][:90]}\t码:{l['extraction_code'] or '-'}\t{l['size'] or '-'}\t{l['status'] or ''}\t{l['label'][:60]}")
    for c in cards[len(results):]:
        print(f"\n□ (未打开) {c['title']}  {c['detail_url']}")

if __name__ == "__main__":
    main()
