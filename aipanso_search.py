#!/workspace/scrapling-env/bin/python
"""Search 爱盘搜 aipanso.com (crawler-built netdisk search engine: 夸克/迅雷/天翼/阿里/百度/UC in ONE shared index,
switched with p=) for a keyword, optionally resolving real share links.

Access: Cloudflare blocks plain HTTP (403 challenge), so everything runs in ONE Scrapling headless browser session
  (challenge passes automatically; no clicking/captcha). No login. Each session loads / first: the site's first
  load answers a 307 cookie-check redirect that HTML-escapes '&' and drops every parameter after the first.
Search: GET /search?k=<kw>&p=<pan>&page=N (k first; p = all|quark|xunlei|aliyun|baidu|uc|tianyi; optional &s= sort,
  &t= type, unused here; 10 results/page, no total shown) -> a[href=/s/<id>] cards with title and
  「来源: 迅雷云盘 时间: YYYY-MM-DD 格式: .pdf 大小: 2.5M」 (大小 absent on some cards).
  --pan takes one value or a comma list (default quark,xunlei,tianyi = Peter's default set); each pan is searched
  separately (--max-pages per pan), results de-duplicated by detail URL. 百度 results are old (2015-2019), no 提取码.
Links (--links N): needs the activation cookie `_egg` (~7 days) in ~/.secrets/aipanso_cookies.json, made by
  aipanso_activate.py CODE (one cookie unlocks every netdisk). For N results in total (taken round-robin across the
  requested pans): open /s/<id>, check the page's own had_follow flag, click 「同意声明,继续访问下载」 and read the
  /cv/<id> 302 target (real share URL). If not activated -> exit 3 with
  爱盘搜需要新动态码，请 Peter 从公众号拿一个（1分钟内有效）.
robots.txt: unreadable (Cloudflare). Polite: 1.5-2.5 s random delay between page loads.
Output: <out>/aipanso_<kw>_<pan+pan...>.json/.csv with columns 网盘 (Chinese netdisk name), pan (code), title, ...
Usage: aipanso_search.py KEYWORD [--pan quark,xunlei,tianyi | all | baidu ...] [--max-pages 1] [--links 0]
       [--out DIR (default /workspace/scrapes/out)]
"""
import os, sys, re, csv, json, time, random, datetime, argparse, html
from urllib.parse import quote
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aipanso_common import (BASE, BTN, PT, NEED_CODE, load_cookies, save_cookies, activation_expiry, fmt_exp,
                            had_follow, click_through)
from scrapling.fetchers import StealthySession

PANS = {"quark": "夸克网盘", "xunlei": "迅雷云盘", "tianyi": "天翼云盘", "aliyun": "阿里云盘", "baidu": "百度网盘",
        "uc": "UC网盘", "all": "全部网盘"}
DEFAULT_PANS = "quark,xunlei,tianyi"
_last = [0.0]

def pace():
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)

def pan_code(name, fallback):
    """Map a card's 来源 text (e.g. 迅雷云盘) to its p= code."""
    for code, zh in PANS.items():
        if code != "all" and (name == zh or name.startswith(zh[:2]) or name.lower().startswith(code)):
            return code
    return fallback if fallback != "all" else ""

def parse_pans(v):
    pans = []
    for p in re.split(r"[,，\s]+", v.strip().lower()):
        if not p:
            continue
        if p not in PANS:
            raise argparse.ArgumentTypeError(f"unknown pan '{p}' (choose from {', '.join(PANS)})")
        if p not in pans:
            pans.append(p)
    if not pans:
        raise argparse.ArgumentTypeError("empty --pan")
    return ["all"] if "all" in pans else pans

def parse_search(h, qpan):
    rows = []
    # split on result anchors (ids contain - and _) and read each card's text up to the next one
    parts = re.split(r'<a href="(/s/[A-Za-z0-9_-]+)"[^>]*>', h)
    for i in range(1, len(parts), 2):
        href, chunk = parts[i], parts[i + 1]
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", chunk))).strip()
        mt = re.search(r"^(.*?)\s*来源:\s*(\S+)\s*时间:\s*([\d-]+)\s*格式:\s*(\S+)(?:\s*大小:\s*(\S+))?", t)
        if not mt:
            continue
        src = mt.group(2); code = pan_code(src, qpan)
        rows.append({"网盘": PANS.get(code, src), "pan": code, "query_pan": qpan, "title": mt.group(1).strip(),
                     "pan_type": src, "shared": mt.group(3), "format": mt.group(4), "size": mt.group(5) or "",
                     "detail_url": BASE + href, "share_url": "", "extraction_code": ""})
    return rows

def round_robin(rows, pans, n):
    buckets = [[r for r in rows if r["query_pan"] == p] for p in pans]
    out = []
    while len(out) < n and any(buckets):
        for b in buckets:
            if b and len(out) < n:
                out.append(b.pop(0))
    return out

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--max-pages", type=int, default=1)
    ap.add_argument("--pan", type=parse_pans, default=parse_pans(DEFAULT_PANS),
                    help="quark|xunlei|tianyi|aliyun|baidu|uc|all, comma list ok (default %s)" % DEFAULT_PANS)
    ap.add_argument("--links", type=int, default=0); ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip(); pans = a.pan
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    cookies = load_cookies()
    exp = activation_expiry(cookies)
    print(f"[info] 激活 cookie _egg 有效期至 {fmt_exp(exp)}" if exp else "[info] 没有激活 cookie（只能搜标题）")
    print(f"[info] 网盘: {', '.join(f'{p}({PANS[p]})' for p in pans)}")
    if a.links > 0 and (not exp or (exp > 0 and exp < time.time())):
        print(NEED_CODE, file=sys.stderr); sys.exit(3)
    rows, seen, fetched, need_code = [], set(), 0, False
    per_pan = {}
    state = {}
    def grab(page):
        page.wait_for_selector("text=爱盘搜", timeout=45000)
        page.wait_for_timeout(500)
        state["html"] = page.content()
        return page
    def resolve(page):
        page.wait_for_selector(BTN, timeout=45000)
        state["hf"] = had_follow(page)
        state["link"] = click_through(page) if state["hf"] else (None, None)
        state["cookies"] = page.context.cookies()
        return page
    with StealthySession(headless=True, google_search=False, network_idle=False, timeout=90000, cookies=cookies) as s:
        # load / first: absorbs the session's 307 cookie-check redirect (it would drop &p= and &page=)
        pace(); s.fetch(BASE + "/", page_action=grab); _last[0] = time.time()
        for pan in pans:
            per_pan[pan] = 0
            for page in range(1, max(1, a.max_pages) + 1):
                pace()
                url = f"{BASE}/search?k={quote(kw)}&p={pan}" + (f"&page={page}" if page > 1 else "")
                s.fetch(url, page_action=grab); _last[0] = time.time(); fetched += 1
                recs = parse_search(state.get("html", ""), pan)
                new = [r for r in recs if r["detail_url"] not in seen]
                for r in new:
                    seen.add(r["detail_url"]); rows.append(r)
                per_pan[pan] += len(new)
                if len(recs) < 10 or not new:
                    break
        targets = round_robin(rows, pans, max(0, a.links))
        for r in targets:
            pace()
            for k in ("hf", "link"):
                state.pop(k, None)
            try:
                resp = s.fetch(r["detail_url"], page_action=resolve)
                if resp.status != 200 or "hf" not in state:
                    raise RuntimeError(f"HTTP {resp.status}, button not found (site slow/504?)")
            except Exception as e:
                print(f"[warn] {r['detail_url']}: {e}", file=sys.stderr); _last[0] = time.time(); continue
            _last[0] = time.time()
            if state["hf"] is False:
                need_code = True; break
            url, st = state.get("link") or (None, None)
            if url and "aipanso.com" not in url:
                r["share_url"] = url
                q = re.search(r"[?&](?:pwd|password)=([A-Za-z0-9]+)", url)
                r["extraction_code"] = q.group(1) if q else ""
            else:
                print(f"[warn] no link for {r['detail_url']} (cv {st})", file=sys.stderr)
        if state.get("cookies"):
            save_cookies(state["cookies"])
    for r in rows:
        r["scraped_at"] = ts
    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, f"aipanso_{kw}_{'+'.join(pans)}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "pans": pans, "scraped_at": ts, "pages_fetched": fetched, "per_pan": per_pan,
                   "links_requested": a.links, "results": rows}, f, ensure_ascii=False, indent=1)
    cols = ["网盘", "pan", "title", "share_url", "extraction_code", "size", "format", "shared", "detail_url",
            "query_pan", "pan_type", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    got = sum(1 for r in rows if r["share_url"])
    print(f"{len(rows)} results ({fetched} pages; " + ", ".join(f"{p} {n}" for p, n in per_pan.items())
          + f"); real links {got}/{len(targets)} @ {ts}\n{base}.json\n{base}.csv")
    for r in rows:
        print(f"{r['网盘']}\t{r['share_url'] or '-'}\t{r['shared']}\t{r['size'] or '-'}\t{r['format']}\t{r['title'][:60]}")
    if need_code:
        print(NEED_CODE, file=sys.stderr); sys.exit(3)

if __name__ == "__main__":
    main()
