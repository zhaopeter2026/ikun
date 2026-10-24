#!/workspace/scrapling-env/bin/python
"""Search so.252035.xyz (a public PanSou 盘搜 instance, github.com/fish2018/pansou) for a keyword.

API (documented in the PanSou README, "API文档 → 搜索API"; the site's own frontend calls it anonymously):
  POST https://so.252035.xyz/api/search   Content-Type: application/json
       {"kw": "<keyword>", "res": "all", "src": "all", "cloud_types": [...optional], "refresh": false}
  (GET /api/search?kw=...&res=all also works.)  Reply: {"code":0,"message":"success","data":{
       "total": N, "results":[{channel, datetime, title, content, links:[{type,url,password,work_title}], tags, images}],
       "merged_by_type": {...}}}
  Auth: only if the instance sets AUTH_ENABLED; GET /api/health on this instance reports "auth_enabled": false.
  No published quota/pricing; README suggests nginx 60 req/min per IP for deployers -> this script makes 3 search
  requests per run (5 s apart, union of results; --tries N) plus one retry after 5 s on 302/400/429/5xx,
  and up to 2 extra calls when every call so far came back HTTP 200 but empty (seen 2026-10-22: 0, then 922 posts).
  Response bodies are decoded leniently (server truncates post text in the middle of UTF-8 characters).
Sources: 60+ Telegram channels + plugins (labi, wanou, zhizhen, duoduo, muou, shandian) per /api/health.

Rows = one per unique share URL. Size is parsed from the post text (e.g. "大小：61.5GB" or "191.64GB" in title).
Date = the post time (UTC from the API) converted to Pacific date. Bulk "index" posts with >4 links whose
per-link work_title doesn't mention the keyword are skipped (they are unrelated link dumps) and counted in the .md.
Usage: so252035_search.py <keyword> [outdir] [--types quark,baidu,...]
"""
import sys, os, csv, re, json, time, datetime, argparse
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo
from scrapling.fetchers import Fetcher

API = "https://so.252035.xyz/api/search"
CAP = 100
PT = ZoneInfo("America/Los_Angeles")
TYPE_NAMES = {"baidu": "百度网盘", "aliyun": "阿里云盘", "quark": "夸克网盘", "guangya": "光鸭云盘", "tianyi": "天翼云盘",
              "uc": "UC网盘", "mobile": "移动云盘", "115": "115网盘", "pikpak": "PikPak", "xunlei": "迅雷云盘",
              "123": "123云盘", "magnet": "磁力链接", "ed2k": "电驴ed2k", "lanzou": "蓝奏云"}
SIZE_RE = re.compile(r"(?:大小|容量|size)\s*[：:]\s*(\d+(?:\.\d+)?\s*[KMGT]i?B)|(\d+(?:\.\d+)?\s*[GT]B)\b", re.I)
URL_RE = re.compile(r"(?:https?|magnet|ed2k):[^\s\u4e00-\u9fff，。；、【】《》()（）\"'<>]+")

def norm_kw(s):
    return re.sub(r"\s+", "", s).lower()

def kw_hit(text, kw):
    """keyword or its first 2+ chars core (e.g. 宫崎骏合集 -> 宫崎骏) appears in text"""
    t = norm_kw(text); k = norm_kw(kw)
    return k in t or (len(k) > 2 and k[:3] in t)

def size_of(*texts):
    for t in texts:
        m = SIZE_RE.search(t or "")
        if m:
            return (m.group(1) or m.group(2)).replace(" ", "").upper()
    return ""

def to_pt_date(iso):
    if not iso or iso.startswith("0001"):
        return ""
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PT).strftime("%Y-%m-%d")
    except Exception:
        return iso[:10]

def call_api(kw, types):
    body = {"kw": kw, "res": "all", "src": "all"}
    if types:
        body["cloud_types"] = types
    last = ""
    for attempt in (1, 2, 3):
        # the front nginx intermittently answers 302 -> http://...:443 (then "400 plain HTTP sent to HTTPS port");
        # don't follow that redirect, just retry after a pause
        r = Fetcher.post(API, json=body, timeout=90, follow_redirects=False,
                         headers={"Referer": "https://so.252035.xyz/"})
        if r.status == 200:
            body = r.body if isinstance(r.body, (bytes, bytearray)) else str(r.body).encode()
            # the server cuts long post texts byte-wise ("…" after half a UTF-8 char) -> decode leniently
            j = json.loads(body.decode("utf-8", "replace"))
            if j.get("code", 0) != 0 or "data" not in j:
                raise RuntimeError(f"API error: {j}")
            return j["data"]
        last = f"HTTP {r.status}: {r.body[:200]!r}"
        if r.status in (301, 302, 307, 400, 429, 500, 502, 503, 504) and attempt < 3:
            print(f"[warn] {last[:60]} - retrying in 5 s", file=sys.stderr); time.sleep(5); continue
        break
    raise RuntimeError("API failed: " + last)

def search(kw, types=None, tries=3):
    # The instance's answers vary a lot call-to-call (observed 3/4/30/43/63 posts for the same keyword
    # within a minute: Telegram/plugin fetches time out server-side), so ask up to `tries` times,
    # 5 s apart, and union the posts by unique_id. Stays far below the 60 req/min nginx guideline.
    merged, totals, blocked = {}, [], False
    for i in range(tries):
        if i:
            time.sleep(5)
        try:
            d = call_api(kw, types)
        except RuntimeError as e:
            print(f"[warn] call {i + 1} failed: {str(e)[:120]}", file=sys.stderr); totals.append("x")
            if "HTTP 403" in str(e):          # treat 403 as "back off", don't keep hammering
                blocked = True
                break
            continue
        totals.append(d.get("total") or 0)
        for res in d.get("results") or []:
            merged.setdefault(res.get("unique_id") or json.dumps(res.get("links"), sort_keys=True), res)
    # 2026-10-22: the instance often answers HTTP 200 with total 0 (its Telegram/plugin fetches timed out),
    # and the next call a few seconds later returns hundreds of posts -> if everything so far was empty,
    # ask up to 2 more times (5 s apart) before giving up.
    extra = 0
    while not merged and extra < 2 and not blocked:
        extra += 1
        time.sleep(5)
        print(f"[warn] 0 posts so far - extra call {extra}/2", file=sys.stderr)
        try:
            d = call_api(kw, types)
        except RuntimeError as e:
            print(f"[warn] extra call failed: {str(e)[:120]}", file=sys.stderr); totals.append("x")
            if "HTTP 403" in str(e):
                blocked = True
            continue
        totals.append(d.get("total") or 0)
        for res in d.get("results") or []:
            merged.setdefault(res.get("unique_id") or json.dumps(res.get("links"), sort_keys=True), res)
    if not merged and all(t == "x" for t in totals):
        raise SystemExit("API failed on every call: " + "/".join(map(str, totals)))
    data = {"results": sorted(merged.values(), key=lambda r: r.get("datetime") or "", reverse=True),
            "total": f"{len(merged)} unique (per call: {'/'.join(map(str, totals))})"}
    rows, seen, skipped = [], set(), 0
    for res in data.get("results") or []:
        links = res.get("links") or []
        bulk = len(links) > 4
        for l in links:
            raw = (l.get("url") or "").strip()
            m = URL_RE.search(raw)          # API sometimes appends "提取码:xxxx" / tags / newlines to the url
            url = m.group(0).rstrip(".,;:&#") if m else raw
            if not url or url in seen:
                continue
            wt = l.get("work_title") or ""
            if bulk and not (wt and wt != res.get("title") and kw_hit(wt, kw)):
                skipped += 1
                continue
            seen.add(url)
            pw = (l.get("password") or "").strip()
            if pw.lower().startswith("http"):   # parser artefact seen in API output
                pw = ""
            if not pw:
                q = parse_qs(urlparse(url).query)
                pw = (q.get("pwd") or q.get("password") or [""])[0]
            title = (wt if bulk else res.get("title") or wt).strip()
            if not title or title.startswith("http"):   # some posts have only a link as title
                title = URL_RE.sub("", res.get("content") or "").strip()[:80] or title
            rows.append({"title": title,
                         "drive": TYPE_NAMES.get(l.get("type"), l.get("type") or "未知"),
                         "share_url": url, "extraction_code": pw,
                         "size": size_of(res.get("content"), res.get("title")),
                         "date": to_pt_date(res.get("datetime") or l.get("datetime")),
                         "source": "tg:" + res["channel"] if res.get("channel") else "plugin"})
            if len(rows) >= CAP:
                return rows, data.get("total"), skipped
    return rows, data.get("total"), skipped

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("outdir", nargs="?", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--tries", type=int, default=3, help="API calls to union (default 3)")
    ap.add_argument("--types", default="", help="comma list: baidu,aliyun,quark,guangya,tianyi,uc,mobile,115,pikpak,xunlei,123,magnet,ed2k")
    a = ap.parse_args()
    kw = a.keyword
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows, total, skipped = search(kw, [t for t in a.types.split(",") if t] or None, max(1, a.tries))
    for r in rows:
        r["scraped_at"] = ts
    cols = ["title", "drive", "share_url", "extraction_code", "size", "date", "source", "scraped_at"]
    base = os.path.join(a.outdir, f"so252035_{kw}")
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["drive"]] = by.get(r["drive"], 0) + 1
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(f"# so.252035.xyz (PanSou) 网盘搜索: {kw}\n\nScraped: {ts} · Source: POST {API} (res=all) · "
                f"Posts returned: {total} · Unique links kept: {len(rows)} · Skipped links from unrelated bulk posts: {skipped}\n\n")
        f.write("By drive: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items(), key=lambda x: -x[1])) + "\n\n")
        f.write("| # | 标题 | 网盘 | 分享链接 | 提取码 | 大小 | 日期(PT) | 来源 |\n|---|---|---|---|---|---|---|---|\n")
        for i, r in enumerate(rows, 1):
            t = r["title"].replace("|", "\\|")[:120]
            f.write(f"| {i} | {t} | {r['drive']} | {r['share_url']} | {r['extraction_code'] or '—'} | {r['size'] or '—'} | {r['date'] or '—'} | {r['source']} |\n")
        if not rows:
            f.write("_No results._\n")
    print(f"{len(rows)} links ({total} posts, {skipped} bulk links skipped) @ {ts}\n{base}.csv\n{base}.md\nby drive: {by}")
    for r in rows:
        print(f"{r['drive']}\t{r['share_url']}\t{r['extraction_code'] or '-'}\t{r['size'] or '-'}\t{r['date']}\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
