#!/workspace/scrapling-env/bin/python
"""Search 地瓜搜 dg.digua.fit (http://dg.digua.fit/#/) for a keyword.

Access: plain HTTP JSON API that the site's own page JS calls; no login, no token, no captcha.
  GET http://dg.digua.fit/api/digua/url?search=<kw>&tagId=&cateId=&page=N
      -> {code:1, data:{list:[...]}}, 20 items/page, empty list = end.
      item: id, title, share_url (real pan link; 百度/迅雷 carry ?pwd=), pan_type (10 夸克 30 百度 40 迅雷),
            pan_type_text, submit_time (Beijing time), vip_level, auth_text.
  GET /api/digua/urldetail?id=<id>   (detail page; not needed - the list already has the link)
  Items with non-empty auth_text or vip_level>0 are gated by the site (link hidden in the UI): their link is NOT
  extracted, they are only counted. The urlfailure / contribute* endpoints are never called.
robots.txt: "User-agent: * / Disallow:" (everything allowed).
Network: site is HTTP-only and the box's direct connection times out, so requests go through the box egress
  CONNECT proxy with HTTP proxy tunnelling (curl_cffi). Order: --proxy URL > http://$SAND_EGRESS_TUNNEL_PROXY_ADDR
  > http://127.0.0.1:8791. A clear error is printed if the proxy is not reachable.
Polite: 1.5-2.5 s random delay between requests; <= --max-pages requests per run (default 5).
Usage: digua_search.py KEYWORD [--max-pages N (default 5)] [--out DIR] [--proxy URL]
"""
import os, sys, csv, json, time, random, socket, datetime, argparse
from urllib.parse import quote, urlparse, parse_qs
from zoneinfo import ZoneInfo
from curl_cffi import requests, CurlOpt

BASE = "http://dg.digua.fit"
PT = ZoneInfo("America/Los_Angeles")
MAX_PAGES = 50
PAN_CN = {10: "夸克网盘", 30: "百度网盘", 40: "迅雷网盘"}
HEADERS = {"Referer": BASE + "/", "Accept": "*/*", "x-device": "PC"}
_last = [0.0]
_s = None

def pick_proxy(cli):
    if cli:
        return cli if "://" in cli else "http://" + cli
    return "http://" + (os.environ.get("SAND_EGRESS_TUNNEL_PROXY_ADDR") or "127.0.0.1:8791")

def check_proxy(proxy):
    p = urlparse(proxy)
    try:
        socket.create_connection((p.hostname, p.port or 80), timeout=5).close()
    except OSError as e:
        sys.exit(f"[错误] 代理 {proxy} 连不上（{e}）。地瓜搜只能走代理访问（本机直连会超时）。"
                 f"请确认 SAND_EGRESS_TUNNEL_PROXY_ADDR 对应的代理在运行，或用 --proxy 指定。")

def get_json(path):
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    for attempt in (1, 2):
        try:
            r = _s.get(BASE + path, timeout=40, headers=HEADERS)
        except Exception as e:
            _last[0] = time.time()
            if attempt == 1:
                print(f"[warn] {e} - 10 秒后重试", file=sys.stderr); time.sleep(10); continue
            raise RuntimeError(f"请求失败（经代理）: {e}")
        _last[0] = time.time()
        if r.status_code == 200:
            j = json.loads(r.text.strip())
            if j.get("code") != 1:
                raise RuntimeError(f"API code {j.get('code')}: {j.get('msg')}")
            return j
        if r.status_code in (429, 500, 502, 503, 504) and attempt == 1:
            print(f"[warn] HTTP {r.status_code} - 10 秒后重试", file=sys.stderr); time.sleep(10); continue
        raise RuntimeError(f"HTTP {r.status_code} on {path}")

def pwd_of(u):
    return (parse_qs(urlparse(u).query).get("pwd") or [""])[0]

def norm(u):
    p = urlparse(u.split("#")[0]); pwd = pwd_of(u)
    return f"{p.netloc.lower()}{p.path.rstrip('/')}" + (f"?pwd={pwd}" if pwd else "")

def main():
    global _s
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--out", default="/workspace/scrapes/out")
    ap.add_argument("--proxy", default="", help="proxy URL (default: http://$SAND_EGRESS_TUNNEL_PROXY_ADDR)")
    a = ap.parse_args()
    kw = a.keyword.strip()
    if a.max_pages > MAX_PAGES:
        print(f"[提示] --max-pages 最多 {MAX_PAGES}，已改为 {MAX_PAGES}", file=sys.stderr)
    pages = max(1, min(a.max_pages, MAX_PAGES))
    proxy = pick_proxy(a.proxy)
    if proxy:
        check_proxy(proxy)
        _s = requests.Session(impersonate="chrome", proxy=proxy, curl_options={CurlOpt.HTTPPROXYTUNNEL: 1})
    else:
        _s = requests.Session(impersonate="chrome")
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows, seen, gated, fetched, items, stop = [], set(), [], 0, 0, "已到 --max-pages 上限"
    for page in range(1, pages + 1):
        lst = (get_json(f"/api/digua/url?search={quote(kw)}&tagId=&cateId=&page={page}").get("data") or {}).get("list") or []
        fetched += 1
        if not lst:
            stop = "没有更多结果" if page > 1 else "没有结果"; break
        items += len(lst)
        for it in lst:
            title = " ".join((it.get("title") or "").split())
            if (it.get("auth_text") or "").strip() or int(it.get("vip_level") or 0) > 0:
                gated.append({"id": it.get("id"), "title": title, "auth_text": it.get("auth_text"),
                              "vip_level": it.get("vip_level")})
                continue   # link hidden by the site -> not extracted
            u = (it.get("share_url") or "").strip()
            if not u or norm(u) in seen:
                continue
            seen.add(norm(u))
            st = it.get("submit_time") or ""
            rows.append({"网盘": PAN_CN.get(it.get("pan_type")) or it.get("pan_type_text") or "",
                         "title": title, "share_url": u, "提取码": pwd_of(u), "date": st[:10],
                         "submit_time_北京": st, "tags": it.get("tag_ids_text") or "", "detail_id": it.get("id"), "page": page,
                         "抓取时间_太平洋": ts})
    base = os.path.join(a.out, f"digua_{kw}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"site": "地瓜搜 http://dg.digua.fit/#/", "keyword": kw, "scraped_at": ts, "proxy": proxy,
                   "pages_fetched": fetched, "items_seen": items, "gated_skipped": len(gated), "stop_reason": stop,
                   "results": rows, "gated_items_no_link": gated}, f, ensure_ascii=False, indent=1)
    cols = ["网盘", "title", "share_url", "提取码", "date", "submit_time_北京", "tags", "detail_id",
            "page", "抓取时间_太平洋"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["网盘"]] = by.get(r["网盘"], 0) + 1
    print(f"地瓜搜「{kw}」: {len(rows)} 条不重复链接（抓了 {fetched} 页、{items} 个结果；{len(gated)} 个受限结果没取链接）@ {ts}")
    print(f"停止原因: {stop}\n{base}.json\n{base}.csv\n按网盘: {by}")
    if not rows:
        print("（没有结果）")
    for r in rows:
        print(f"{r['网盘']}\t{r['share_url'][:90]}\t码:{r['提取码'] or '-'}\t{r['date']}\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
