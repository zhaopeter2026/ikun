#!/workspace/scrapling-env/bin/python
"""Search yangtuo88.top (羊驼小站, a user-posted 网盘 resource community; custom Vue 3 SPA + JSON API, not PanSou) for a keyword.

Endpoints (found in the site's JS bundle /assets/index.7ddb12b6.js + Search/AIGuide chunks, checked 2026-10-21):
  Website frontend (what the /#/search page calls anonymously, no login/key):
    GET https://yangtuo88.top/api/v1/posts?q=<kw>&sort=relevance&limit=20[&cursor=<meta.nextCursor>]
        -> {"data":{"items":[{id,title,category{id,name},author,createdAt,...}]},"meta":{"nextCursor":...}}
        (search items carry NO links; links come from the post detail)
    GET https://yangtuo88.top/api/v1/posts/<id>
        -> {"data":{..., "content":"<html>", "cloudDriveLinks":[{url, platform: quark|baidu|alipan, status: valid|invalid|unknown}]}}
    Response headers show X-Ratelimit-Limit 90 (search) / 150 (detail) per window.
  Official AI/Agent API (doc: https://yangtuo88.top/skills/yangtuo-search.md, page https://yangtuo88.top/#/ai-guide):
    POST https://yangtuo88.top/api/v1/agent/search  {"query": kw, "limit": 1-20, "cursor": ...}
    Authorization: Bearer yt_pat_...  (personal token created in 个人中心 after registering; free, 100 calls/day/account
    shared by search+post+comment; GET /api/v1/agent/capabilities). The site states "There is no anonymous Agent mode".
NOTE: https://yangtuo88.top/robots.txt has "Disallow: /api/" and the Agent doc says never to fall back to scraping.
  Default mode below uses the anonymous website endpoints (same calls the browser makes) at low volume; if env var
  YANGTUO_TOKEN (yt_pat_...) is set, the search step uses the official Agent API instead (detail step unchanged).

Rows = one per unique share URL. Size parsed from title/content ("【159GB】", "（146G）", "大小：..."). Extraction code from
content ("提取码: xxxx") or ?pwd= in the URL. Date = post createdAt (UTC) -> Pacific date. link_status = site's link check.
Usage: yangtuo88_search.py <keyword> [outdir] [--max-posts N]   (default 30 post details, 2 s apart; cap 100 rows)
"""
import sys, os, csv, re, json, time, html, datetime, argparse
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo
from scrapling.fetchers import Fetcher

SITE = "https://yangtuo88.top"
API = SITE + "/api/v1"
CAP = 100
DELAY = 2.0
PT = ZoneInfo("America/Los_Angeles")
TYPE_NAMES = {"quark": "夸克网盘", "baidu": "百度网盘", "alipan": "阿里云盘", "aliyun": "阿里云盘", "xunlei": "迅雷云盘",
              "uc": "UC网盘", "tianyi": "天翼云盘", "115": "115网盘", "123": "123云盘", "mobile": "移动云盘"}
HOST_TYPES = [("pan.quark.cn", "quark"), ("pan.baidu.com", "baidu"), ("alipan.com", "alipan"), ("aliyundrive.com", "alipan"),
              ("pan.xunlei.com", "xunlei"), ("drive.uc.cn", "uc"), ("cloud.189.cn", "tianyi"), ("115", "115"),
              ("123pan", "123"), ("123684", "123"), ("123865", "123"), ("caiyun.139.com", "mobile"), ("yun.139.com", "mobile")]
SIZE_RE = re.compile(r"(?:大小|容量|size)\s*[：:]\s*(\d+(?:\.\d+)?\s*[KMGT]i?B?)|[【\[（(](\d+(?:\.\d+)?\s*[GT]B?)[】\]）)]|(\d+(?:\.\d+)?\s*[GT]B)\b", re.I)
CODE_RE = re.compile(r"(?:提取码|提取碼|访问码|密码|pwd)\s*[：:=]?\s*([A-Za-z0-9]{4,8})", re.I)
URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fff，。；、【】《》()（）\"'<>]+")
HDRS = {"Accept": "application/json", "Referer": SITE + "/"}

def get_json(url, params=None, method="GET", body=None, headers=None):
    h = dict(HDRS, **(headers or {}))
    for attempt in (1, 2):
        r = (Fetcher.post(url, json=body, headers=h, timeout=30) if method == "POST"
             else Fetcher.get(url, params=params, headers=h, timeout=30))
        if r.status == 200:
            return json.loads(r.body)
        if r.status in (429, 500, 502, 503, 504) and attempt == 1:
            wait = 10 if r.status == 429 else 5
            print(f"[warn] HTTP {r.status} on {url} - retrying in {wait} s", file=sys.stderr); time.sleep(wait); continue
        raise RuntimeError(f"HTTP {r.status} {url}: {r.body[:200]!r}")

def drive_of(url, platform=""):
    if platform:
        return TYPE_NAMES.get(platform, platform)
    host = (urlparse(url).hostname or "").lower()
    for k, t in HOST_TYPES:
        if k in host:
            return TYPE_NAMES[t]
    return "其他"

def size_of(*texts):
    for t in texts:
        m = SIZE_RE.search(t or "")
        if m:
            s = next(g for g in m.groups() if g).replace(" ", "").upper()
            return s + "B" if s[-1] in "GT" else s
    return ""

def to_pt_date(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PT).strftime("%Y-%m-%d")
    except Exception:
        return (iso or "")[:10]

def search_ids(kw, max_posts):
    """Return [(id, title, category, createdAt)] in relevance order, up to max_posts."""
    out, cursor, token = [], None, os.environ.get("YANGTUO_TOKEN", "").strip()
    while len(out) < max_posts:
        if out:
            time.sleep(DELAY)
        if token:
            body = {"query": kw, "limit": 20}
            if cursor:
                body["cursor"] = cursor
            j = get_json(API + "/agent/search", method="POST", body=body, headers={"Authorization": "Bearer " + token})
            d = j.get("data") or {}
            items, cursor = d.get("organicResults") or [], d.get("nextCursor")
            for it in items:   # organic results carry a relative post url like /posts/123 (doc); take the id from it
                pid = it.get("id") or (re.findall(r"(\d+)", it.get("url") or "") or [None])[-1]
                if pid:
                    out.append((int(pid), it.get("title", ""), (it.get("category") or {}).get("name", ""), it.get("createdAt", "")))
        else:
            p = {"q": kw, "sort": "relevance", "limit": 20}
            if cursor:
                p["cursor"] = cursor
            j = get_json(API + "/posts", params=p)
            items, cursor = (j.get("data") or {}).get("items") or [], (j.get("meta") or {}).get("nextCursor")
            for it in items:
                out.append((it["id"], it.get("title", ""), (it.get("category") or {}).get("name", ""), it.get("createdAt", "")))
        if not items or not cursor:
            break
    seen, uniq = set(), []
    for t in out:
        if t[0] not in seen:
            seen.add(t[0]); uniq.append(t)
    return uniq[:max_posts]

def search(kw, max_posts=30):
    posts = search_ids(kw, max_posts)
    rows, seen, nolink = [], set(), 0
    for i, (pid, title, cat, created) in enumerate(posts):
        time.sleep(DELAY)
        try:
            d = get_json(f"{API}/posts/{pid}")["data"]
        except RuntimeError as e:
            print(f"[warn] post {pid}: {str(e)[:120]}", file=sys.stderr)
            if "HTTP 429" in str(e) or "HTTP 403" in str(e):
                break
            continue
        content = d.get("content") or ""
        text = html.unescape(re.sub(r"<[^>]+>", " ", content))
        links = d.get("cloudDriveLinks") or [{"url": u, "platform": "", "status": ""} for u in URL_RE.findall(text)]
        if not links:
            nolink += 1
        for l in links:
            url = (l.get("url") or "").strip().rstrip(".,;:&#")
            if not url or url in seen:
                continue
            seen.add(url)
            q = parse_qs(urlparse(url).query)
            code = (q.get("pwd") or q.get("password") or [""])[0]
            if not code:
                m = CODE_RE.search(text)
                code = m.group(1) if m else ""
            rows.append({"title": (d.get("title") or title).strip(), "drive": drive_of(url, l.get("platform") or ""),
                         "share_url": url, "extraction_code": code,
                         "size": size_of(d.get("title") or title, text),
                         "date": to_pt_date(d.get("createdAt") or created),
                         "category": (d.get("category") or {}).get("name", cat),
                         "link_status": l.get("status") or "", "post_url": f"{SITE}/#/posts/{pid}"})
            if len(rows) >= CAP:
                return rows, len(posts), nolink
    return rows, len(posts), nolink

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("outdir", nargs="?", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--max-posts", type=int, default=30, help="post details to fetch (default 30, max 100)")
    a = ap.parse_args()
    kw = a.keyword
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows, nposts, nolink = search(kw, max(1, min(100, a.max_posts)))
    for r in rows:
        r["scraped_at"] = ts
    cols = ["title", "drive", "share_url", "extraction_code", "size", "date", "category", "link_status", "post_url", "scraped_at"]
    base = os.path.join(a.outdir, f"yangtuo88_{kw}")
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["drive"]] = by.get(r["drive"], 0) + 1
    mode = "POST /api/v1/agent/search (token)" if os.environ.get("YANGTUO_TOKEN") else "GET /api/v1/posts?q= (anonymous web)"
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(f"# yangtuo88.top 羊驼小站 搜索: {kw}\n\nScraped: {ts} · Search: {mode} + GET /api/v1/posts/<id> · "
                f"Posts checked: {nposts} · Posts without links: {nolink} · Unique links: {len(rows)}\n\n")
        f.write("By drive: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items(), key=lambda x: -x[1])) + "\n\n")
        f.write("| # | 标题 | 网盘 | 分享链接 | 提取码 | 大小 | 日期(PT) | 分区 | 链接状态 | 帖子 |\n|---|---|---|---|---|---|---|---|---|---|\n")
        for i, r in enumerate(rows, 1):
            t = r["title"].replace("|", "\\|")[:120]
            f.write(f"| {i} | {t} | {r['drive']} | {r['share_url']} | {r['extraction_code'] or '—'} | {r['size'] or '—'} | "
                    f"{r['date'] or '—'} | {r['category']} | {r['link_status'] or '—'} | {r['post_url']} |\n")
        if not rows:
            f.write("_No results._\n")
    print(f"{len(rows)} links from {nposts} posts ({nolink} without links) @ {ts}\n{base}.csv\n{base}.md\nby drive: {by}")
    for r in rows:
        print(f"{r['drive']}\t{r['share_url']}\t{r['extraction_code'] or '-'}\t{r['size'] or '-'}\t{r['date']}\t{r['link_status']}\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
