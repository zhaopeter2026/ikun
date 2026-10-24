#!/workspace/scrapling-env/bin/python
"""Search 网盘资源社 kuakezy.top (Xiuno BBS 4.0.4 forum, ~126k topics, mostly 夸克; sister domain kuakezy.cc) for a keyword.

Access: plain HTTP only gets the /_guard/auto.js check page, so a Scrapling StealthySession (one headless browser,
  reused for all pages) loads it; the check passes on its own (no clicking, no captcha). No login.
  Never touches /user-* (robots-disallowed). Never replies/registers.
Endpoints: GET /search-<urlencoded kw>.htm (page 1), /search-<kw>-1-<N>.htm (page N, range 1 = 主题贴);
  each li.thread[data-tid] has the title link, tag badges (forum-<fid>-1.htm?tagids=..) -> section, author, date, views.
  Thread pages usually hide links behind the reply-to-view plugin ("您好，本帖含有特定内容，请回复后再查看"):
  with --open N those threads are kept with status 需回复 and no link (Peter replies himself if he wants it).
  robots.txt (User-agent *) disallows /admin, /user-*, /serach-* only.
Polite: 1.5-2.5 s random delay between page loads.
Usage: kuakezy_search.py KEYWORD [--max-pages 2] [--open 0] [--out DIR (default /workspace/scrapes/out)]
"""
import os, sys, re, csv, json, time, random, datetime, argparse, html
from urllib.parse import quote
from zoneinfo import ZoneInfo
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/workspace/scrapling-env/ms-playwright")
from scrapling.fetchers import StealthySession

BASE = "https://kuakezy.top/"
PT = ZoneInfo("America/Los_Angeles")
PANS = [("夸克网盘", r"pan\.quark\.cn/s/"), ("百度网盘", r"pan\.baidu\.com/"), ("阿里云盘", r"(?:aliyundrive\.com|alipan\.com)/s/"),
        ("天翼云盘", r"cloud\.189\.cn/"), ("移动云盘", r"(?:caiyun\.139\.com|yun\.139\.com)/"), ("115网盘", r"(?:115\.com|115cdn\.com)/s/"),
        ("UC网盘", r"drive\.uc\.cn/s/"), ("迅雷云盘", r"pan\.xunlei\.com/s/"), ("123云盘", r"(?:123pan\.com|123684\.com|123865\.com|123912\.com)/s/"),
        ("光鸭云盘", r"guangyapan\.com/")]
LINK_RE = re.compile(r"https?://(?:[\w-]+\.)*(?:" + "|".join(p for _, p in PANS) + r")[^\s\"'<>&]*(?:&amp;[^\s\"'<>&]*|&[^\s\"'<>&]*)*", re.I)
CODE_RE = re.compile(r"(?:提取码|密码|访问码|pwd)\s*[:：=]?\s*([A-Za-z0-9]{4,8})", re.I)
HIDDEN = "回复后再查看"
_last = [0.0]

def txt(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()

def act(page):
    # the /_guard/ check swaps in the real page by itself; wait until the forum page is there
    page.wait_for_function("document.title.indexOf('网盘资源社') >= 0", timeout=45000)

def fetch(s, url):
    if "/user-" in url:
        raise ValueError("refusing /user-* (robots-disallowed)")
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    p = s.fetch(url, page_action=act, timeout=60000)
    _last[0] = time.time()
    if p.status != 200:
        raise RuntimeError(f"HTTP {p.status} on {url}")
    return p.html_content if hasattr(p, "html_content") else str(p.body)

def forum_names(h):
    names = {}
    for fid, name in re.findall(r'href="forum-(\d+)\.htm"[^>]*>\s*<div>\s*<span class="font-weight-bold">([^<]{1,12})</span>', h):
        names.setdefault(fid, name.strip())
    return names

def parse_search(h):
    fnames = forum_names(h)
    rows = []
    for blk in re.split(r'<li class="media thread', h)[1:]:
        tid = re.search(r'data-tid="(\d+)"', blk)
        a = re.search(r'<a href="thread-(\d+)\.htm">(.*?)</a>', blk, re.S)
        if not tid or not a:
            continue
        tags = [txt(t) for t in re.findall(r'<a href="forum-\d+-1\.htm\?tagids=[^"]*"[^>]*>(.*?)</a>', blk, re.S)]
        fid = re.search(r'href="forum-(\d+)-1\.htm\?tagids=', blk)
        author = re.search(r'class="username[^"]*"[^>]*>(.*?)</a>', blk, re.S)
        date = re.search(r'<span class="date[^"]*">(.*?)</span>', blk, re.S)
        views = re.search(r'icon-eye"></i>\s*([\d.,kKwW万]+)', blk)
        rows.append({"title": txt(a.group(2)), "thread_url": f"{BASE}thread-{tid.group(1)}.htm",
                     "section": fnames.get(fid.group(1), f"forum-{fid.group(1)}") if fid else "",
                     "tags": " ".join(tags), "author": txt(author.group(1)) if author else "",
                     "posted": txt(date.group(1)) if date else "", "views": views.group(1) if views else ""})
    pages = [int(x) for x in re.findall(r'search-[^"]*?-1-(\d+)\.htm', h)]
    return rows, max(pages) if pages else 1

def parse_thread(h):
    m = re.search(r'class="message[^"]*"', h)
    body = h
    if m:
        end = h.find('id="comments"', m.end())
        body = h[m.start(): end if end > 0 else len(h)]
    sec = re.search(r"<title>.*?-([^-<]+)-网盘资源社", h, re.S)
    links, seen = [], set()
    for mm in LINK_RE.finditer(body):
        u = html.unescape(mm.group(0)).rstrip("'\"<>.,;)）】")
        if u in seen:
            continue
        seen.add(u)
        q = re.search(r"[?&]pwd=([A-Za-z0-9]+)", u)
        c = q.group(1) if q else ((CODE_RE.search(txt(body[mm.end():mm.end() + 300])) or [None, ""])[1])
        pt = next((n for n, p in PANS if re.search(p, u, re.I)), "未知")
        links.append((pt, u, c))
    return links, HIDDEN in body, sec.group(1).strip() if sec else ""

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--max-pages", type=int, default=2)
    ap.add_argument("--open", type=int, default=0); ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip()
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    threads, seen, site_pages, fetched = [], set(), 0, 0
    rows = []
    with StealthySession(headless=True, google_search=False) as s:
        for page in range(1, max(1, a.max_pages) + 1):
            url = f"{BASE}search-{quote(kw)}.htm" if page == 1 else f"{BASE}search-{quote(kw)}-1-{page}.htm"
            recs, sp = parse_search(fetch(s, url)); fetched += 1
            site_pages = max(site_pages, sp)
            for r in recs:
                if r["thread_url"] not in seen:
                    seen.add(r["thread_url"]); threads.append(r)
            if not recs or page >= site_pages:
                break
        for t in threads:
            t.update({"status": "未打开", "pan_type": "", "share_url": "", "extraction_code": ""})
        for t in threads[:max(0, a.open)]:
            try:
                links, hidden, sec = parse_thread(fetch(s, t["thread_url"]))
            except Exception as e:
                print(f"[warn] {t['thread_url']}: {e}", file=sys.stderr); t["status"] = "打开失败"; continue
            t["section"] = t["section"] or sec
            if links:
                for pt, u, c in links:
                    rows.append({**t, "status": "可见" + ("（另有需回复内容）" if hidden else ""), "pan_type": pt,
                                 "share_url": u, "extraction_code": c})
                t["status"] = "_expanded"
            else:
                t["status"] = "需回复" if hidden else "无链接"
    out = []
    for t in threads:
        if t["status"] == "_expanded":
            out += [r for r in rows if r["thread_url"] == t["thread_url"]]
        else:
            out.append(t)
    for r in out:
        r["scraped_at"] = ts
    base = os.path.join(a.out, f"kuakezy_{kw}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "scraped_at": ts, "site_pages": site_pages, "pages_fetched": fetched,
                   "threads_found": len(threads), "opened": min(a.open, len(threads)), "rows": out},
                  f, ensure_ascii=False, indent=1)
    cols = ["title", "thread_url", "section", "tags", "author", "posted", "views", "status", "pan_type",
            "share_url", "extraction_code", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(out)
    st = {}
    for r in out:
        st[r["status"]] = st.get(r["status"], 0) + 1
    print(f"{len(threads)} threads from {fetched}/{site_pages} result pages, opened {min(a.open, len(threads))} @ {ts}\n"
          f"{base}.json\n{base}.csv\nstatus: {st}")
    for r in out:
        print(f"{r['status']}\t{r['section'] or '-'}\t{r['share_url'] or '-'}\t{r['thread_url']}\t{r['title'][:50]}")

if __name__ == "__main__":
    main()
