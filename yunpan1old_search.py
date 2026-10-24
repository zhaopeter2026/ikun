#!/workspace/scrapling-env/bin/python
"""Search the OLD 云盘资源分享社区 forum old.yunpan1.wang (Flarum; new site yunpan1.cc needs login to search) for a keyword.

Endpoint (Flarum's own frontend JSON API, called anonymously, read-only - no login, no posting):
  GET https://old.yunpan1.wang/api/discussions?filter[q]=<kw>&page[limit]=50&include=firstPost,tags
      -> {"links":{"first":..,"next"?:..}, "data":[{type:"discussions", id, attributes:{title, slug, createdAt(UTC), ...},
          relationships:{firstPost:{data:{id}}, tags:{data:[..]}}}], "included":[{type:"posts", id, attributes:{contentHtml}}, {type:"tags",..}]}
  Follows links.next when present (max 5 pages). Post URL = https://old.yunpan1.wang/d/<slug> (slug already starts with <id>-).
  Links are taken from the first post's contentHtml (href attributes + bare URLs in the text). Extraction codes come from
  ?pwd= / ?password= or a nearby "提取码/密码/访问码: xxxx"; sizes are parsed from the title, else the post text.
  Obvious typo domain pan.qualk.cn is rewritten to pan.quark.cn and listed in the .md notes.
  Polite: >=2.5 s between requests, retry once on 429/5xx, stop on 403, dedupe by link, cap 100 rows.
Usage: yunpan1old_search.py <keyword> [outdir]
"""
import sys, os, csv, re, json, time, html, datetime, argparse
from urllib.parse import urlencode, urlparse, parse_qs
from zoneinfo import ZoneInfo
from scrapling.fetchers import Fetcher

SITE = "https://old.yunpan1.wang"
API = f"{SITE}/api/discussions"
CAP, LIMIT, MAX_PAGES, DELAY = 100, 50, 5, 2.5
PT = ZoneInfo("America/Los_Angeles")
DRIVES = [("pan.quark.cn", "夸克网盘"), ("aliyundrive.com", "阿里云盘"), ("alipan.com", "阿里云盘"),
          ("pan.baidu.com", "百度网盘"), ("pan.xunlei.com", "迅雷云盘"), ("drive.uc.cn", "UC网盘"), ("fast.uc.cn", "UC网盘"),
          ("115.com", "115网盘"), ("115cdn.com", "115网盘"), ("anxia.com", "115网盘"),
          ("123pan.com", "123云盘"), ("123pan.cn", "123云盘"), ("123684.com", "123云盘"), ("123865.com", "123云盘"),
          ("123912.com", "123云盘"), ("cloud.189.cn", "天翼云盘"), ("caiyun.139.com", "移动云盘"), ("yun.139.com", "移动云盘"),
          ("guangyapan.com", "光鸭云盘"), ("guangya", "光鸭云盘")]
TYPOS = {"pan.qualk.cn": "pan.quark.cn"}
URL_RE = re.compile(r"https?://[A-Za-z0-9.\-]+(?::\d+)?(?:/[^\s\"'<>，。；、（）()【】\[\]]*)?")
CODE_RE = re.compile(r"(?:提取码|提取碼|密码|密碼|访问码|訪問碼|pwd)\s*[:：=]?\s*([A-Za-z0-9]{4,8})", re.I)
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(TB|GB|MB|T|G|M)(?![A-Za-z])", re.I)
_last = [0.0]

class Forbidden(Exception):
    pass

def get(url):
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    for attempt in (1, 2):
        r = Fetcher.get(url, timeout=40, headers={"Referer": SITE + "/", "Accept": "application/vnd.api+json, application/json"})
        _last[0] = time.time()
        if r.status == 200:
            return json.loads(r.body)
        if r.status == 403:
            raise Forbidden(f"HTTP 403 on {url} - stopping (not retrying)")
        if r.status in (429, 500, 502, 503, 504) and attempt == 1:
            print(f"[warn] HTTP {r.status} - retrying once in 10 s", file=sys.stderr); time.sleep(10)
            _last[0] = time.time(); continue
        raise RuntimeError(f"HTTP {r.status} on {url}: {r.body[:200]!r}")

def drive_of(url):
    host = (urlparse(url).hostname or "").lower()
    for dom, name in DRIVES:
        if dom in host:
            return name
    return None

def to_pt_date(iso):
    if not iso:
        return ""
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PT).strftime("%Y-%m-%d")
    except Exception:
        return iso[:10]

def find_size(*texts):
    for t in texts:
        m = SIZE_RE.search(t or "")
        if m:
            u = m.group(2).upper()
            return f"{m.group(1)}{u if u.endswith('B') else u + 'B'}"
    return ""

def strip_html(h):
    h = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", h, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", h))

def extract_links(content_html):
    """Return [(url, code, fixed_from)] in order of appearance."""
    hrefs = [html.unescape(h) for h in re.findall(r'href="([^"]+)"', content_html)]
    text = strip_html(content_html)
    found, out = [], []
    for u in hrefs + URL_RE.findall(text):
        u = u.strip().rstrip(".,;:!?)）】」”'\"")
        fixed = ""
        host = (urlparse(u).hostname or "").lower()
        if host in TYPOS:
            fixed = u
            u = u.replace(host, TYPOS[host], 1)
        if drive_of(u) and u not in found:
            found.append(u); out.append([u, "", fixed])
    codes_all = CODE_RE.findall(text)
    for item in out:
        u = item[0]
        q = parse_qs(urlparse(u).query)
        code = (q.get("pwd") or q.get("password") or [""])[0]
        if not code:
            # code written right after this link in the text
            orig = item[2] or u
            i = text.find(orig)
            if i >= 0:
                seg = text[i + len(orig): i + len(orig) + 40]
                nxt = URL_RE.search(seg)  # don't borrow the next link's code
                m = CODE_RE.search(seg[:nxt.start()] if nxt else seg)
                code = m.group(1) if m else ""
        if not code and len(out) == 1 and len(codes_all) == 1:
            code = codes_all[0]
        item[1] = code
    return [tuple(x) for x in out]

def search(kw):
    rows, seen, notes, posts_n, pages = [], set(), [], 0, 0
    url = f"{API}?{urlencode({'filter[q]': kw, 'page[limit]': LIMIT, 'include': 'firstPost,tags'})}"
    no_link = []
    stopped = ""
    while url and pages < MAX_PAGES and len(rows) < CAP:
        try:
            j = get(url)
        except Forbidden as e:
            stopped = str(e); print(f"[stop] {e}", file=sys.stderr); break
        pages += 1
        posts = {i["id"]: i["attributes"] for i in j.get("included", []) if i.get("type") == "posts"}
        for d in j.get("data", []):
            posts_n += 1
            a = d.get("attributes", {})
            title = (a.get("title") or "").strip()
            slug = a.get("slug") or ""
            post_url = f"{SITE}/d/{slug if slug.startswith(str(d['id'])) else (d['id'] + '-' + slug).rstrip('-')}"
            fp = ((d.get("relationships") or {}).get("firstPost") or {}).get("data") or {}
            ch = (posts.get(fp.get("id")) or {}).get("contentHtml") or ""
            links = extract_links(ch)
            if not links:
                no_link.append(post_url)
            size = find_size(title, strip_html(ch))
            for u, code, fixed in links:
                key = u  # keep "#" (139 links use hash routes)
                if fixed:
                    notes.append(f"Typo domain fixed: {fixed} -> {u} ({post_url})"
                                 + ("; same link already kept from another post, so not a new row" if key in seen else ""))
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"title": title, "drive": drive_of(u), "share_url": u, "extraction_code": code, "size": size,
                             "date": to_pt_date(a.get("createdAt")), "post_url": post_url})
                if len(rows) >= CAP:
                    break
            if len(rows) >= CAP:
                break
        url = (j.get("links") or {}).get("next")
    return rows, {"discussions": posts_n, "pages": pages, "no_link_posts": no_link, "notes": notes, "stopped": stopped}

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("outdir", nargs="?", default=os.path.dirname(os.path.abspath(__file__)))
    a = ap.parse_args()
    kw = a.keyword.strip()
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows, info = search(kw)
    for r in rows:
        r["scraped_at"] = ts
    cols = ["title", "drive", "share_url", "extraction_code", "size", "date", "post_url", "scraped_at"]
    base = os.path.join(a.outdir, f"yunpan1old_{kw}")
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["drive"]] = by.get(r["drive"], 0) + 1
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(f"# 云盘资源分享社区 旧论坛 old.yunpan1.wang 搜索: {kw}\n\nScraped: {ts} · Source: GET {API}?filter[q]={kw}"
                f"&page[limit]={LIMIT}&include=firstPost,tags · Discussions: {info['discussions']} ({info['pages']} page(s)) · "
                f"Unique links kept: {len(rows)}\n\n")
        f.write("By drive: " + (", ".join(f"{k} {v}" for k, v in sorted(by.items(), key=lambda x: -x[1])) or "—") + "\n\n")
        notes = list(info["notes"])
        if info["no_link_posts"]:
            notes.append(f"{len(info['no_link_posts'])} discussion(s) with no cloud-drive link in the first post: " + ", ".join(info["no_link_posts"]))
        if info["stopped"]:
            notes.append(f"Stopped early: {info['stopped']}")
        if notes:
            f.write("Notes:\n" + "".join(f"- {n}\n" for n in notes) + "\n")
        f.write("| # | 标题 | 网盘 | 分享链接 | 提取码 | 大小 | 日期(PT) | 帖子 |\n|---|---|---|---|---|---|---|---|\n")
        for i, r in enumerate(rows, 1):
            t = r["title"].replace("|", "\\|")[:120]
            f.write(f"| {i} | {t} | {r['drive']} | {r['share_url']} | {r['extraction_code'] or '—'} | {r['size'] or '—'} | {r['date'] or '—'} | {r['post_url']} |\n")
        if not rows:
            f.write("_No results._\n")
    print(f"{len(rows)} links from {info['discussions']} discussions ({info['pages']} page(s)) @ {ts}\n{base}.csv\n{base}.md\nby drive: {by}")
    for n in info["notes"]:
        print("[note]", n)
    if info["no_link_posts"]:
        print("[note] no-link posts:", info["no_link_posts"])
    for r in rows:
        print(f"{r['drive']}\t{r['share_url']}\t{r['extraction_code'] or '-'}\t{r['size'] or '-'}\t{r['date']}\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
