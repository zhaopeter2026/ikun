#!/workspace/scrapling-env/bin/python
"""Search 橘子盘搜 (Mucho Studio 'Mcsou' template site) for a keyword across its 4 search sources and get real share
links via the site's own /open/ redirect.

Domains: real site www.nmme.xyz, 备用 www.nmsou.cc; www.nmme.one is only the 导航/测速 page - used solely to discover the
  current domain when both known ones fail (it lists them as plain <a href> with data-title "橘子盘搜 - ...").
Search (plain HTTP, no login):  GET /s/1/<kw>  (source 橘子盘)  and  /s/1/<kw>?type=one|two|three  (盘一区/盘二区/盘三区)
  Each result <a class="open" data-url="<token>" data-code="<提取码>" data-id="<id|other>"> + pan icon /asset/index/pan/<x>.png.
  Title, pan type and code are in the HTML. The share link itself is NOT: the copy button holds an obfuscated string
  (deliberately not decoded) and the title link makes the page JS open /open/<id>/<token>, which redirects to the pan.
Real links: plain HTTP GET of /open/<id>/<token> returns an empty 200 (works only from the site's own page JS), so
  --links uses one browser session (Scrapling StealthySession): load the source page, click the result's own link,
  catch the popup's navigation to the pan domain, close it. Rows are matched by position + title check.
robots.txt (nmme.xyz): allows all. Ads/analytics domains are blocked in the browser.
Polite: 1.5-2.5 s random delay between requests/clicks; <= 4 search pages over HTTP + 1 browser page per source used + N clicks.
Usage: nmme_search.py KEYWORD [--links N (default 8)] [--out DIR (default /workspace/scrapes/out)]
"""
import os, sys, re, csv, json, time, random, datetime, argparse, html
from urllib.parse import quote
from zoneinfo import ZoneInfo
from scrapling.fetchers import Fetcher

PT = ZoneInfo("America/Los_Angeles")
DOMAINS = ["https://www.nmme.xyz", "https://www.nmsou.cc"]
NAV = "https://www.nmme.one/"
SOURCES = [("", "橘子盘"), ("one", "盘一区"), ("two", "盘二区"), ("three", "盘三区")]
PAN_ICON = {"baidu": "百度网盘", "quark": "夸克网盘", "xunlei": "迅雷云盘", "thunder": "迅雷云盘", "ali": "阿里云盘",
            "aliyun": "阿里云盘", "alipan": "阿里云盘", "115": "115网盘", "uc": "UC网盘", "tianyi": "天翼云盘", "189": "天翼云盘",
            "123": "123云盘", "caiyun": "移动云盘", "magnet": "磁力"}
PAN_HOST = [("夸克网盘", r"pan\.quark\.cn"), ("百度网盘", r"pan\.baidu\.com"), ("迅雷云盘", r"pan\.xunlei\.com"),
            ("阿里云盘", r"aliyundrive\.com|alipan\.com"), ("115网盘", r"115\.com|115cdn\.com"), ("UC网盘", r"drive\.uc\.cn"),
            ("天翼云盘", r"cloud\.189\.cn"), ("123云盘", r"123pan\.com|123684\.com|123865\.com|123912\.com"), ("移动云盘", r"139\.com")]
PAN_RE = re.compile("|".join(p for _, p in PAN_HOST), re.I)
BLOCK = ["hm.baidu.com", "tj.l-il.cn", "cdn.leeleo.vip", "yhzu.cn", "mqtv.fun", "googlesyndication.com", "doubleclick.net"]
OPEN_RE = re.compile(r'<a class="open[^"]*"\s+href="javascript:;"\s+data-url="([^"]+)"\s+data-code="([^"]*)"\s+data-id="([^"]+)"([^>]*)>(.*?)</a>', re.S)
_last = [0.0]

def pause():
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)

def get(url, ref):
    pause()
    for attempt in (1, 2):
        r = Fetcher.get(url, timeout=40, headers={"Referer": ref})
        _last[0] = time.time()
        if r.status == 200:
            return r.body.decode("utf-8", "replace") if isinstance(r.body, bytes) else str(r.body)
        if r.status in (429, 500, 502, 503, 504) and attempt == 1:
            print(f"[warn] HTTP {r.status} - retrying in 10 s", file=sys.stderr); time.sleep(10); continue
        raise RuntimeError(f"HTTP {r.status} on {url}")

def txt(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or "")).replace("\xa0", " ")).strip()

def src_url(base, kw, t):
    return f"{base}/s/1/{quote(kw)}" + (f"?type={t}" if t else "")

def pick_domain(kw):
    """Return (base, first-source html). Known domains first, then whatever the 导航页 lists."""
    tried = []
    for cand in [DOMAINS, None]:
        if cand is None:
            try:
                nav = get(NAV, NAV)
                cand = [u.rstrip("/") for u in re.findall(r'href="(https?://[^"/]+)/?"[^>]*data-title="橘子盘搜', nav)]
                cand = [u for u in dict.fromkeys(cand) if u not in tried]
                print(f"[info] 导航页 lists: {cand}", file=sys.stderr)
            except Exception as e:
                print(f"[warn] 导航页 {NAV} failed: {e}", file=sys.stderr); cand = []
        for base in cand:
            tried.append(base)
            try:
                h = get(src_url(base, kw, ""), base + "/")
                if "橘子" in h and 'class="count"' in h:
                    return base, h
                print(f"[warn] {base}: unexpected page", file=sys.stderr)
            except Exception as e:
                print(f"[warn] {base} failed: {e}", file=sys.stderr)
    sys.exit("橘子盘搜: no working domain (nmme.xyz / nmsou.cc / 导航页 nmme.one all failed)")

def parse(h, label):
    m = re.search(r'里为您找到\s*<strong>(\d+)</strong>', h)
    count = int(m.group(1)) if m else None
    rows = []
    for item in h.split('<div class="item">')[1:]:
        nm = re.search(r'<div class="name">(.*?)</div>', item, re.S)
        name = txt(nm.group(1)) if nm else ""
        t = re.search(r'#icon-jilu"></use></svg>\s*<a>([^<]+)</a>', item)
        for i, (tok, code, did, attrs, inner) in enumerate(OPEN_RE.findall(item)):
            ti = re.search(r'title="([^"]*)"', attrs)
            title = html.unescape(ti.group(1)).strip() if ti else name
            ic = re.search(r'/asset/index/pan/([\w]+)\.png', inner)
            pan = PAN_ICON.get(ic.group(1).lower(), ic.group(1)) if ic else ""
            if not pan:
                pm = re.search(r"(百度网盘|夸克网盘|迅雷网盘|迅雷云盘|阿里云盘|115网盘|UC网盘|天翼云盘)", txt(inner))
                pan = pm.group(1).replace("迅雷网盘", "迅雷云盘") if pm else ""
            code = html.unescape(code).strip()
            rows.append({"source": label, "title": title, "pan_type": pan, "extraction_code": "" if code in ("无", "") else code,
                         "share_url": "", "time": t.group(1).strip() if t else "", "data_id": did, "token": tok,
                         "dom_index": None})
    return count, rows

def fetch_links(base, kw, rows, n):
    from scrapling.fetchers import StealthySession
    todo = {}
    for idx, r in enumerate(rows):
        if len([1 for v in todo.values() for _ in v]) >= n:
            break
        todo.setdefault(r["source_t"], []).append(idx)
    done = 0
    state = {}

    def act(page):
        ctx = page.context
        page.wait_for_selector('a.open', timeout=30000)
        els = page.locator('a.open')
        for idx in state["idxs"]:
            r = rows[idx]
            try:
                el = els.nth(r["dom_index"])
                tok = el.get_attribute("data-url") or ""
                ttl = (el.get_attribute("title") or el.inner_text() or "")
                if r["title"][:6] not in ttl and r["pan_type"][:2] not in el.inner_text():
                    r["link_note"] = "页面结果和 HTTP 结果对不上，跳过"; continue
                pause()
                with ctx.expect_page(timeout=15000) as pi:
                    el.scroll_into_view_if_needed(); el.click()
                _last[0] = time.time()
                pop = pi.value
                hit = {}
                pop.on("request", lambda q: hit.setdefault("u", q.url) if (q.is_navigation_request() and PAN_RE.search(q.url)) else None)
                end = time.time() + 20
                while time.time() < end and "u" not in hit and not PAN_RE.search(pop.url or ""):
                    pop.wait_for_timeout(300)
                u = hit.get("u") or (pop.url if PAN_RE.search(pop.url or "") else "")
                r["open_url"] = f"{base}/open/{r['data_id']}/{tok[:12]}..."
                if u:
                    r["share_url"] = re.sub(r"[?&]entry=nmmecc", "", u).replace("?#", "#").rstrip("?")
                    for name, pat in PAN_HOST:
                        if re.search(pat, u, re.I):
                            r["pan_type"] = name; break
                else:
                    r["link_note"] = f"跳转未到网盘: {pop.url[:80]}"
                pop.close()
            except Exception as e:
                r["link_note"] = f"点击失败: {str(e)[:80]}"
        return page

    with StealthySession(headless=True, google_search=False, network_idle=False, timeout=60000, blocked_domains=set(BLOCK)) as s:
        for t, idxs in todo.items():
            state["idxs"] = idxs
            pause()
            s.fetch(src_url(base, kw, t), page_action=act)
            _last[0] = time.time()
    return sum(1 for r in rows if r["share_url"])

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--links", type=int, default=8)
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip()
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    base, first = pick_domain(kw)
    rows, counts = [], {}
    for t, label in SOURCES:
        h = first if t == "" else get(src_url(base, kw, t), src_url(base, kw, ""))
        c, rs = parse(h, label)
        counts[label] = c
        for i, r in enumerate(rs):
            r["source_t"] = t; r["dom_index"] = i; r["scraped_at"] = ts
        rows += rs
    got = 0
    if a.links > 0 and rows:
        got = fetch_links(base, kw, rows, a.links)
    for r in rows:
        r.pop("token", None); r.pop("source_t", None); r.setdefault("link_note", ""); r.setdefault("open_url", "")
    out = os.path.join(a.out, f"nmme_{kw}")
    with open(out + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "domain": base, "scraped_at": ts, "source_counts": counts, "rows": len(rows),
                   "links_resolved": got, "results": rows}, f, ensure_ascii=False, indent=1)
    cols = ["source", "title", "pan_type", "share_url", "extraction_code", "time", "data_id", "dom_index", "open_url", "link_note", "scraped_at"]
    with open(out + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    print(f"{len(rows)} rows via {base}; per source {counts}; real links {got}/{min(a.links, len(rows))} tried @ {ts}\n{out}.json\n{out}.csv")
    for r in rows:
        print(f"{r['source']}\t{r['pan_type']}\t{r['share_url'] or '-'}\t{r['extraction_code']}\t{r['title'][:50]}\t{r['link_note']}")

if __name__ == "__main__":
    main()
