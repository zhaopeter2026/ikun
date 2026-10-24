#!/workspace/scrapling-env/bin/python
"""Search 大盘搜 dapanso.com (Astro SSR netdisk search site) for a keyword.

Access: plain HTTP (Scrapling Fetcher), server-rendered HTML, no login, no anti-bot seen.
  GET /search?keyword=<kw>&page=N[&panType=ALIYUN|BAIDU|QUARK|XUNLEI|UC][&type=1..6][&searchMode=smart|fuzzy]
      15 results/page. NOTE: only `keyword` works - a bare ?q= is silently ignored.
      type: 1 视频 2 图片 3 文档 4 软件 5 音频 6 其他
  Each <div class="item" id="<site id>">: title <a class="title" href="/s/<sid>"> (detail page), category label,
  the share link(s) themselves as <a href="https://pan..." class="label-wrap"> (Baidu links carry ?pwd=), <pre class="desc">,
  .username (uploader), .time (date). The same link can appear twice per item (desktop + mobile layout) - deduped.
  Not called: /api/ (robots.txt Disallow /api/, incl. the /api/resources/click tracker the link's onclick fires).
Limits: pages > 30 redirect to ?page=1&error=limit -> capped at 30, and the script stops if it sees error=limit.
  Empty page text "数据收录中，请稍后再试..." = end of results.
Polite: 1.5-2.5 s random delay between requests; <= --pages requests per run (default 3).
Usage: dapanso_search.py KEYWORD [--pages N (default 3, max 30)] [--pan all|ALIYUN|BAIDU|QUARK|XUNLEI|UC]
                         [--type 0..6 (0=all)] [--mode smart|fuzzy] [--out DIR]
"""
import os, sys, re, csv, json, time, random, datetime, argparse, html
from urllib.parse import urlencode, urlparse, parse_qs
from zoneinfo import ZoneInfo
from scrapling.fetchers import Fetcher

BASE = "https://dapanso.com"
PT = ZoneInfo("America/Los_Angeles")
MAX_PAGES = 30
PANS = ["all", "ALIYUN", "BAIDU", "QUARK", "XUNLEI", "UC"]
PAN_CN = {"all": "全部", "ALIYUN": "阿里云盘", "BAIDU": "百度网盘", "QUARK": "夸克网盘", "XUNLEI": "迅雷云盘", "UC": "UC网盘"}
TYPE_CN = {0: "全部", 1: "视频", 2: "图片", 3: "文档", 4: "软件", 5: "音频", 6: "其他"}
HOSTS = [("quark.cn", "夸克网盘"), ("aliyundrive.com", "阿里云盘"), ("alipan.com", "阿里云盘"), ("baidu.com", "百度网盘"),
         ("xunlei.com", "迅雷云盘"), ("uc.cn", "UC网盘"), ("139.com", "移动云盘"), ("189.cn", "天翼云盘"),
         ("123pan", "123云盘"), ("123684", "123云盘"), ("123865", "123云盘"), ("123912", "123云盘"), ("115", "115网盘"),
         ("lanzou", "蓝奏云"), ("mypikpak", "PikPak")]
EMPTY = "数据收录中"
_last = [0.0]

def get(url):
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    for attempt in (1, 2):
        r = Fetcher.get(url, timeout=40, headers={"Referer": BASE + "/"})
        _last[0] = time.time()
        if r.status == 200:
            return (r.body.decode("utf-8", "replace") if isinstance(r.body, bytes) else str(r.body)), str(r.url)
        if r.status in (429, 500, 502, 503, 504) and attempt == 1:
            print(f"[warn] HTTP {r.status} - 10 秒后重试", file=sys.stderr); time.sleep(10); continue
        raise RuntimeError(f"HTTP {r.status} on {url}")

def txt(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()

def pan_of(u):
    host = urlparse(u).netloc.lower()
    for d, n in HOSTS:
        if d in host:
            return n
    return ""

def norm(u):
    """dedupe key: drop #fragment (except 移动云盘 hash routes) and tracking params, keep ?pwd="""
    if "139.com" in u:
        return u
    p = urlparse(u.split("#")[0]); pwd = parse_qs(p.query).get("pwd")
    return f"{p.netloc.lower()}{p.path.rstrip('/')}" + (f"?pwd={pwd[0]}" if pwd else "")

def parse(h):
    rows = []
    for blk in re.split(r'<div class="item\b', h)[1:]:
        m = re.search(r'<a href="(/s/[^"]+)" class="title"[^>]*>(.*?)</a>', blk, re.S)
        if not m:
            continue
        sid, title = m.group(1)[3:], txt(m.group(2))
        sid_attr = re.search(r'^[^>]*\bid="(\d+)"', blk)
        cat = re.search(r'<span class="text"[^>]*>([^<]+)</span>', blk)
        desc = re.search(r'<pre class="desc"[^>]*>(.*?)</pre>', blk, re.S)
        user = re.search(r'class="username"[^>]*>([^<]*)<', blk)
        date = re.search(r'class="time"[^>]*>([^<]*)<', blk)
        links = []
        for u in re.findall(r'<a href="(https?://[^"]+)"[^>]*class="label-wrap"', blk):
            u = html.unescape(u).strip()
            if pan_of(u) and u not in links:
                links.append(u)
        for u in links:
            code = (parse_qs(urlparse(u).query).get("pwd") or [""])[0]
            rows.append({"title": title, "pan_type": pan_of(u), "share_url": u, "extraction_code": code,
                         "category": cat.group(1).strip() if cat else "", "date": date.group(1).strip() if date else "",
                         "uploader": user.group(1).strip() if user else "",
                         "desc": txt(desc.group(1))[:300] if desc else "", "detail_url": BASE + "/s/" + sid,
                         "site_id": sid_attr.group(1) if sid_attr else ""})
        if not links:
            rows.append({"title": title, "pan_type": "", "share_url": "", "extraction_code": "", "category": "",
                         "date": "", "uploader": "", "desc": "", "detail_url": BASE + "/s/" + sid, "site_id": "",
                         "_nolink": True})
    return rows

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--pan", default="all", type=lambda s: s.upper() if s.lower() != "all" else "all", choices=PANS)
    ap.add_argument("--type", type=int, default=0, choices=list(TYPE_CN)); ap.add_argument("--mode", choices=["smart", "fuzzy"])
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip()
    if a.pages > MAX_PAGES:
        print(f"[提示] 大盘搜最多只能翻 {MAX_PAGES} 页，--pages 已改为 {MAX_PAGES}", file=sys.stderr)
    pages = max(1, min(a.pages, MAX_PAGES))
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows, seen, fetched, items, nolink, stop = [], set(), 0, 0, 0, "已到 --pages 上限"
    for page in range(1, pages + 1):
        q = {"keyword": kw, "page": page}
        if a.pan != "all": q["panType"] = a.pan
        if a.type: q["type"] = a.type
        if a.mode: q["searchMode"] = a.mode
        h, final = get(f"{BASE}/search?{urlencode(q)}")
        fetched += 1
        if "error=limit" in final or (page > 1 and f"page={page}" not in final):
            stop = f"第 {page} 页被网站限制（error=limit）"; break
        recs = parse(h)
        if not recs:
            stop = "没有更多结果（数据收录中）" if EMPTY in h else f"第 {page} 页解析不到结果"; break
        items += len({r["detail_url"] for r in recs})
        for r in recs:
            if r.pop("_nolink", False):
                nolink += 1; continue
            k = norm(r["share_url"])
            if k in seen:
                continue
            seen.add(k); r["page"] = page; r["scraped_at"] = ts; rows.append(r)
        if len({r["detail_url"] for r in recs}) < 15:
            stop = "最后一页（不足 15 条）"; break
    sfx = "".join(f"_{v}" for v in ([PAN_CN[a.pan]] if a.pan != "all" else []) + ([TYPE_CN[a.type]] if a.type else []))
    base = os.path.join(a.out, f"dapanso_{kw}{sfx}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "pan": a.pan, "type": TYPE_CN[a.type], "mode": a.mode or "默认", "scraped_at": ts,
                   "pages_fetched": fetched, "items_seen": items, "items_without_link": nolink, "stop_reason": stop,
                   "results": rows}, f, ensure_ascii=False, indent=1)
    cols = ["title", "pan_type", "share_url", "extraction_code", "category", "date", "uploader", "desc", "detail_url", "site_id", "page", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["pan_type"]] = by.get(r["pan_type"], 0) + 1
    print(f"大盘搜「{kw}」: {len(rows)} 条不重复链接（抓了 {fetched} 页、{items} 个结果；{nolink} 个结果没有链接）@ {ts}")
    print(f"停止原因: {stop}\n{base}.json\n{base}.csv\n按网盘: {by}")
    if not rows:
        print("（没有结果）")
    for r in rows:
        print(f"{r['pan_type']}\t{r['share_url'][:90]}\t码:{r['extraction_code'] or '-'}\t{r['category']}\t{r['date']}\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
