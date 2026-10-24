#!/workspace/scrapling-env/bin/python
"""Search KK网盘 www.kkpans.com (self-run curated catalog, not a PanSou/xyso meta-search) for a keyword.

Endpoints (read from the site's own frontend bundle /assets/index-*.js; called anonymously, no key/login):
  GET https://www.kkpans.com/api/resources/public?page=N&limit=24&search=<kw>[&platform=quark|baidu|guangya|xunlei|uc]
      -> {"data":[{id, file_name, file_size(bytes|null), file_type, share_link, target_platform, share_code,
                   created_at(UTC), updated_at, category_name, work_key, ...}], "total":N, "page":N, "limit":N}
  GET https://www.kkpans.com/api/magnets/public?page=N&limit=24&search=<kw>
      -> {"data":[{id, title, release_name, magnet_uri, info_hash, published_at, created_at}], "total":N}
  Search is a plain substring match on the title (e.g. 宫崎骏合集 -> 0, 宫崎骏 -> 1 on 2026-10-21), so when the exact
  keyword finds nothing the script retries once with trailing generic words (合集/全集/系列/大全/全套/收藏) stripped
  and says so in the .md (--no-fallback to disable).
  robots.txt allows / and only disallows /admin, /api/admin/, /api/auth/. No terms/API docs/quota published;
  no membership/VIP. Polite: 2.5 s between requests, max 5 pages per endpoint, cap 100 rows, dedupe by link.
Usage: kkpans_search.py <keyword> [outdir] [--platform quark] [--no-magnets] [--no-fallback]
"""
import sys, os, csv, re, json, time, datetime, argparse
from urllib.parse import urlencode, urlparse, parse_qs
from zoneinfo import ZoneInfo
from scrapling.fetchers import Fetcher

BASE = "https://www.kkpans.com/api"
CAP, LIMIT, MAX_PAGES, DELAY = 100, 24, 5, 2.5
PT = ZoneInfo("America/Los_Angeles")
TYPE_NAMES = {"quark": "夸克网盘", "baidu": "百度网盘", "guangya": "光鸭云盘", "xunlei": "迅雷云盘", "uc": "UC网盘",
              "aliyun": "阿里云盘", "magnet": "磁力链接"}
GENERIC = re.compile(r"(合集|全集|系列|大全|全套|收藏版?|打包)+$")
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?\s*[GT]B?)\b", re.I)
_last = [0.0]

def get(path, params):
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    url = f"{BASE}{path}?{urlencode(params)}"
    for attempt in (1, 2):
        r = Fetcher.get(url, timeout=40, headers={"Referer": "https://www.kkpans.com/", "Accept": "application/json"})
        _last[0] = time.time()
        if r.status == 200:
            return json.loads(r.body)
        if r.status in (429, 500, 502, 503, 504) and attempt == 1:
            print(f"[warn] HTTP {r.status} on {path} - retrying in 10 s", file=sys.stderr); time.sleep(10); continue
        raise RuntimeError(f"HTTP {r.status} on {url}: {r.body[:200]!r}")

def human(n):
    if not n:
        return ""
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f}{u}" if u != "B" else f"{int(n)}B"
        n /= 1024

def to_pt_date(iso):
    if not iso:
        return ""
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PT).strftime("%Y-%m-%d")
    except Exception:
        return iso[:10]

def paged(path, kw, extra):
    total, page = None, 1
    while page <= MAX_PAGES:
        j = get(path, {"page": page, "limit": LIMIT, "search": kw, **extra})
        total = j.get("total") or 0
        data = j.get("data") or []
        yield from data
        if not data or page * LIMIT >= total:
            break
        page += 1
    paged.total = total

def search(kw, platform=None, magnets=True):
    rows, seen, totals = [], set(), {}
    extra = {"platform": platform} if platform else {}
    for res in paged("/resources/public", kw, extra):
        url = (res.get("share_link") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        pw = (res.get("share_code") or "").strip()
        if not pw:
            q = parse_qs(urlparse(url).query); pw = (q.get("pwd") or q.get("password") or [""])[0]
        title = (res.get("file_name") or "").strip()
        size = human(res.get("file_size"))
        if not size:
            m = SIZE_RE.search(title); size = m.group(1).upper() if m else ""
        rows.append({"title": title, "drive": TYPE_NAMES.get(res.get("target_platform"), res.get("target_platform") or "未知"),
                     "share_url": url, "extraction_code": pw, "size": size, "date": to_pt_date(res.get("created_at")),
                     "source": f"https://www.kkpans.com/resources/{res.get('id')}"})
        if len(rows) >= CAP:
            break
    totals["resources"] = paged.total
    if magnets and not platform and len(rows) < CAP:
        for m in paged("/magnets/public", kw, {}):
            url = (m.get("magnet_uri") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            title = (m.get("release_name") or m.get("title") or "").strip()
            s = SIZE_RE.search(title)
            rows.append({"title": title, "drive": "磁力链接", "share_url": url, "extraction_code": "",
                         "size": s.group(1).upper() if s else "", "date": to_pt_date(m.get("published_at") or m.get("created_at")),
                         "source": f"https://www.kkpans.com/magnets/{m.get('id')}"})
            if len(rows) >= CAP:
                break
        totals["magnets"] = paged.total
    return rows, totals

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("outdir", nargs="?", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--platform", default="", help="quark|baidu|guangya|xunlei|uc (resources only)")
    ap.add_argument("--no-magnets", action="store_true"); ap.add_argument("--no-fallback", action="store_true")
    a = ap.parse_args()
    kw = a.keyword.strip()
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    used = kw
    rows, totals = search(kw, a.platform or None, not a.no_magnets)
    note = ""
    if not rows and not a.no_fallback:
        core = GENERIC.sub("", kw).strip()
        if core and core != kw:
            print(f"[info] 0 results for {kw!r}; retrying with {core!r}", file=sys.stderr)
            rows, totals = search(core, a.platform or None, not a.no_magnets)
            used, note = core, f"Exact keyword '{kw}' returned 0; results below are for '{core}' (site search is substring-only). "
    for r in rows:
        r["scraped_at"] = ts
    cols = ["title", "drive", "share_url", "extraction_code", "size", "date", "source", "scraped_at"]
    base = os.path.join(a.outdir, f"kkpans_{kw}")
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["drive"]] = by.get(r["drive"], 0) + 1
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(f"# KK网盘 www.kkpans.com 搜索: {kw}\n\nScraped: {ts} · Source: GET {BASE}/resources/public + /magnets/public "
                f"(search={used}) · Site totals: {totals} · Unique links kept: {len(rows)}\n\n{note}\n\n")
        f.write("By drive: " + (", ".join(f"{k} {v}" for k, v in sorted(by.items(), key=lambda x: -x[1])) or "—") + "\n\n")
        f.write("| # | 标题 | 网盘 | 分享链接 | 提取码 | 大小 | 日期(PT) | 来源 |\n|---|---|---|---|---|---|---|---|\n")
        for i, r in enumerate(rows, 1):
            t = r["title"].replace("|", "\\|")[:120]
            f.write(f"| {i} | {t} | {r['drive']} | {r['share_url']} | {r['extraction_code'] or '—'} | {r['size'] or '—'} | {r['date'] or '—'} | {r['source']} |\n")
        if not rows:
            f.write("_No results._\n")
    print(f"{len(rows)} links (site totals {totals}, searched {used!r}) @ {ts}\n{base}.csv\n{base}.md\nby drive: {by}")
    for r in rows:
        print(f"{r['drive']}\t{r['share_url']}\t{r['extraction_code'] or '-'}\t{r['size'] or '-'}\t{r['date']}\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
