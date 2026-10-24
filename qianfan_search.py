#!/workspace/scrapling-env/bin/python
"""Search 千帆搜索 pan.qianfan.app (aggregated netdisk search: 阿里/百度/夸克/蓝奏/天翼/迅雷) for a keyword.

Access: search needs an activation cookie. Peter gets a code from WeChat 公众号「阿枫科技」 (send 「芝麻开门」); it is
  entered once on /code/, which sets cookie `code` (~7 days). Cookies live in ~/.secrets/qianfan_cookies.txt
  (Netscape format, chmod 600). If the site redirects to /code/ the script stops with a Chinese message, exit 3.
Search (plain HTTP with the cookie): GET /search/?pan=<all|aliyundrive|baidu|quark|lanzou|cloud189|xunlei>&type=all&q=<kw>&page=N
  10 results/page, server-rendered Django HTML: div.search-item -> a[href=/share/<b64 {"model","id"}>:<sig>],
  h2 img (pan icon), title, .search-file-size, .search-file-num, .search-creator.
Real share link: the detail page keeps it scrambled in data-url and decodes it client-side when the user clicks
  「复制资源链接」 (.pan-url). With --links N the script opens the top N detail pages in ONE Scrapling headless browser
  session (cookies loaded, third-party lb.jjwttg.xyz jump script blocked), clicks that button and reads the site's
  own alert 「分享链接：…」; extraction code from .copy-pwd[data-pwd] or ?pwd= in the link. Decoder is NOT reimplemented.
robots.txt: only /login/ disallowed. Polite: 1.5-2.5 s random delay between all requests.
Usage: qianfan_search.py KEYWORD [--pan all] [--max-pages 1] [--links 5] [--out DIR (default /workspace/scrapes/out)]
"""
import os, sys, re, csv, json, time, random, datetime, argparse, html
import urllib.request, urllib.error, http.cookiejar
from urllib.parse import urlencode, urljoin
from zoneinfo import ZoneInfo
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/workspace/scrapling-env/ms-playwright")

BASE = "https://pan.qianfan.app"
COOKIES = os.path.expanduser(os.environ.get("QIANFAN_COOKIES", "~/.secrets/qianfan_cookies.txt"))
PT = ZoneInfo("America/Los_Angeles")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
BLOCK = {"lb.jjwttg.xyz", "jjwttg.xyz"}
EXPIRED = "千帆激活码过期，请 Peter 从公众号「阿枫科技」发「芝麻开门」拿新码"
PANS = {"aliyundrive": "阿里云盘", "baidu": "百度网盘", "quark": "夸克网盘", "lanzou": "蓝奏云盘",
        "cloud189": "天翼云盘", "xunlei": "迅雷云盘"}
ALIAS = {"aliyun": "aliyundrive", "ali": "aliyundrive", "alipan": "aliyundrive", "tianyi": "cloud189", "189": "cloud189",
         "lanzouyun": "lanzou", "thunder": "xunlei"}
_last = [0.0]

class Expired(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None

def pace():
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)

def load_jar():
    if not os.path.exists(COOKIES):
        raise Expired(f"cookie 文件不存在: {COOKIES}")
    jar = http.cookiejar.MozillaCookieJar(COOKIES)
    jar.load(ignore_discard=True, ignore_expires=True)
    return jar

def code_expiry(jar):
    for c in jar:
        if c.name == "code":
            return c.expires
    return None

def get(opener, url):
    pace()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": BASE + "/"})
    try:
        r = opener.open(req, timeout=40); status, loc, body = r.status, "", r.read()
    except urllib.error.HTTPError as e:
        status, loc, body = e.code, e.headers.get("Location", ""), e.read()
    _last[0] = time.time()
    if status in (301, 302, 303, 307, 308):
        if "/code/" in loc:
            raise Expired(EXPIRED)
        raise RuntimeError(f"unexpected redirect {status} -> {loc}")
    if status != 200:
        raise RuntimeError(f"HTTP {status} on {url}")
    return body.decode("utf-8", "replace")

def text(s):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or ""))).strip()

def parse_search(h):
    rows = []
    for blk in re.split(r'<div class="search-item">', h)[1:]:
        a = re.search(r'<a href="(/share/[^"]+)"', blk)
        h2 = re.search(r'<h2 class="search-title">(.*?)</h2>', blk, re.S)
        if not a or not h2:
            continue
        icon = re.search(r'/static/img/(\w+)\.png', h2.group(1))
        g = lambda cls: (re.search(r'class="' + cls + r'"[^>]*>(.*?)</div>', blk, re.S) or [None, ""])[1]
        rows.append({"title": text(h2.group(1)), "pan_type": PANS.get(icon.group(1), icon.group(1)) if icon else "未知",
                     "size": text(g("search-file-size")).replace("文件大小:", "").strip(),
                     "file_count": text(g("search-file-num")).replace("数量:", "").strip(),
                     "sharer": text(g("search-creator")), "detail_url": urljoin(BASE, html.unescape(a.group(1))).rstrip("/") + "/",
                     "share_url": "", "extraction_code": "", "shared_at": ""})
    pages = [int(x) for x in re.findall(r'[?&]page=(\d+)', h)]
    return rows, max(pages) if pages else (1 if rows else 0)

def jar_to_pw(jar):
    return [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path or "/",
             "expires": float(c.expires or -1), "secure": bool(c.secure), "httpOnly": False, "sameSite": "Lax"}
            for c in jar if "qianfan" in c.domain]

def get_links(rows, jar, n):
    from scrapling.fetchers import StealthySession
    box = {}
    def act(page):
        box.clear(); box["url"] = page.url
        if "/code/" in page.url:
            return page
        box["pwd"] = page.evaluate("Array.from(document.querySelectorAll('.copy-pwd')).map(e=>e.getAttribute('data-pwd')).filter(Boolean)")
        box["meta"] = page.evaluate("(document.body.innerText.match(/在\\s*🕔\\s*([^\\n]*?)\\s*创建并分享/)||['',''])[1]")
        btn = page.locator(".pan-url")
        if btn.count():
            btn.first.click(); page.wait_for_timeout(1500)
            box["alert"] = page.evaluate("Array.from(document.querySelectorAll('body *')).filter(e=>e.children.length<4&&/分享链接：/.test(e.innerText||'')).map(e=>e.innerText)")
        else:
            box["alert"] = []
        return page
    with StealthySession(headless=True, cookies=jar_to_pw(jar), blocked_domains=BLOCK, google_search=False,
                         network_idle=True, timeout=60000,
                         additional_args={"permissions": ["clipboard-read", "clipboard-write"]}) as s:
        for r in rows[:n]:
            pace()
            try:
                s.fetch(r["detail_url"], page_action=act)
            except Exception as e:
                print(f"[warn] {r['detail_url']}: {e}", file=sys.stderr); _last[0] = time.time(); continue
            _last[0] = time.time()
            if "/code/" in box.get("url", ""):
                raise Expired(EXPIRED)
            m = None
            for t in box.get("alert", []):
                m = re.search(r"分享链接：\s*(\S+)", t) or m
            if m:
                r["share_url"] = m.group(1).strip()
            else:
                print(f"[warn] no link (dead entry?) {r['detail_url']}", file=sys.stderr)
            q = re.search(r"[?&]pwd=([A-Za-z0-9]+)", r["share_url"])
            r["extraction_code"] = (box.get("pwd") or [""])[0] or (q.group(1) if q else "")
            r["shared_at"] = box.get("meta", "")

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--pan", default="all", help="all|" + "|".join(PANS))
    ap.add_argument("--max-pages", type=int, default=1); ap.add_argument("--links", type=int, default=5)
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip(); pan = ALIAS.get(a.pan, a.pan)
    if pan != "all" and pan not in PANS:
        sys.exit(f"unknown --pan {a.pan!r}; use all|{'|'.join(PANS)}")
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    try:
        jar = load_jar()
        exp = code_expiry(jar)
        if exp:
            dt = datetime.datetime.fromtimestamp(exp, PT)
            print(f"[info] 激活 cookie 有效期至 {dt:%Y-%m-%d %H:%M %Z}" + ("（已过期）" if dt < datetime.datetime.now(PT) else ""))
        else:
            print("[warn] cookie 文件里没有 code 激活 cookie", file=sys.stderr)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)
        rows, seen, site_pages, fetched = [], set(), 0, 0
        for page in range(1, max(1, a.max_pages) + 1):
            h = get(opener, f"{BASE}/search/?{urlencode({'pan': pan, 'type': 'all', 'q': kw, 'page': page})}")
            fetched += 1
            if "/code/" in h[:3000] and "激活" in h[:3000]:
                raise Expired(EXPIRED)
            recs, sp = parse_search(h)
            site_pages = max(site_pages, sp)
            for r in recs:
                if r["detail_url"] not in seen:
                    seen.add(r["detail_url"]); rows.append(r)
            if not recs or page >= site_pages:
                break
        if a.links > 0 and rows:
            get_links(rows, jar, a.links)
    except Expired as e:
        print(str(e), file=sys.stderr); sys.exit(3)
    for r in rows:
        r["scraped_at"] = ts
    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, f"qianfan_{kw}" + ("" if pan == "all" else f"_{pan}"))
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "pan": pan, "scraped_at": ts, "site_pages": site_pages,
                   "site_total_approx": site_pages * 10, "pages_fetched": fetched, "links_requested": a.links,
                   "results": rows}, f, ensure_ascii=False, indent=1)
    cols = ["title", "pan_type", "share_url", "extraction_code", "size", "file_count", "sharer", "shared_at",
            "detail_url", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["pan_type"]] = by.get(r["pan_type"], 0) + 1
    got = sum(1 for r in rows if r["share_url"])
    print(f"{len(rows)} results (site ~{site_pages * 10}, {site_pages} pages, fetched {fetched}); real links {got}/{min(a.links, len(rows))} @ {ts}\n{base}.json\n{base}.csv\nby pan: {by}")
    for r in rows:
        print(f"{r['pan_type']}\t{r['share_url'] or '-'}\t{r['extraction_code'] or '-'}\t{r['size'] or '-'}\t{r['title'][:50]}")

if __name__ == "__main__":
    main()
