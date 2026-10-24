#!/workspace/scrapling-env/bin/python
"""Search 爱盼综合搜索 search.aipan.me (general netdisk search, all content types - sister of 爱盼（影视）www.aipan.me,
see aipan_search.py) for a keyword.

Access: plain HTTP JSON API (Scrapling Fetcher), no login, no Cloudflare / anti-bot seen. The site's own search page
(/search?keyword=..., client-side) calls these, and so does this script:
  GET  /api/access-control/config            -> {"data":{"enabled":false,"requireLogin":true,"minPoints":1000,...}}
       (checked first: if the owner switches on "登录+积分" the script prints a clear Chinese warning instead of failing)
  POST /api/sources/<src>  JSON {"name": kw}  src = local | xiaokupan | pansou | external-pan
       -> {"list":[{"name": title, "links":[{"service":"QUARK","link":"https://...","pwd":"xxxx"}]}]}   no pagination
  local = site's own library, xiaokupan = 小酷盘, pansou = a PanSou aggregator (overlaps with so.252035.xyz / so252035_search.py),
  external-pan = other external netdisk indexes. service values seen: QUARK ALIYUN BAIDU 123 MAGNET PIKPAK UC XUNLEI.
  Identical links are merged across sources (key: link without ?pwd= / #fragment; magnets by btih hash); every row records
  which source(s) returned it, plus the extraction code (pwd field or ?pwd= in the link).
  Not called: /api/search/record (search-history logging) and /api/moderation/check (front-end keyword check).
robots.txt: Disallow /admin/ only.
Polite: 1.5-2.5 s random delay between requests; 1 + len(--sources) requests per run (default 5).
Usage: aipan_general_search.py KEYWORD [--sources local,xiaokupan,pansou,external-pan] [--pan 夸克|阿里|百度|123|磁力|UC|迅雷|PikPak|...]
                                [--out DIR]
Exit: 0 ok (incl. 0 results), 2 all sources failed, 3 site requires login/points.
"""
import os, sys, re, csv, json, time, random, datetime, argparse
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from zoneinfo import ZoneInfo
from scrapling.fetchers import Fetcher

BASE = "https://search.aipan.me"
PT = ZoneInfo("America/Los_Angeles")
SOURCES = ["local", "xiaokupan", "pansou", "external-pan"]
SRC_CN = {"local": "爱盼站内", "xiaokupan": "小酷盘", "pansou": "PanSou", "external-pan": "外部网盘"}
SERVICE_CN = {"QUARK": "夸克网盘", "ALIYUN": "阿里云盘", "ALIPAN": "阿里云盘", "BAIDU": "百度网盘", "123": "123云盘",
              "MAGNET": "磁力链接", "ED2K": "电驴", "PIKPAK": "PikPak", "UC": "UC网盘", "XUNLEI": "迅雷云盘", "THUNDER": "迅雷云盘",
              "TIANYI": "天翼云盘", "CLOUD189": "天翼云盘", "MOBILE": "移动云盘", "CAIYUN": "移动云盘", "115": "115网盘",
              "LANZOU": "蓝奏云", "GUANGYA": "光鸭云盘"}
HOSTS = [("quark.cn", "夸克网盘"), ("aliyundrive.com", "阿里云盘"), ("alipan.com", "阿里云盘"), ("baidu.com", "百度网盘"),
         ("123pan", "123云盘"), ("123684", "123云盘"), ("123865", "123云盘"), ("123912", "123云盘"), ("mypikpak", "PikPak"),
         ("uc.cn", "UC网盘"), ("xunlei.com", "迅雷云盘"), ("189.cn", "天翼云盘"), ("139.com", "移动云盘"), ("115", "115网盘"),
         ("lanzou", "蓝奏云"), ("guangya", "光鸭云盘")]
# --pan aliases -> canonical Chinese name
PAN_ALIAS = {"夸克": "夸克网盘", "quark": "夸克网盘", "阿里": "阿里云盘", "aliyun": "阿里云盘", "alipan": "阿里云盘",
             "百度": "百度网盘", "baidu": "百度网盘", "123": "123云盘", "磁力": "磁力链接", "magnet": "磁力链接",
             "pikpak": "PikPak", "uc": "UC网盘", "迅雷": "迅雷云盘", "xunlei": "迅雷云盘", "天翼": "天翼云盘", "189": "天翼云盘",
             "移动": "移动云盘", "115": "115网盘", "蓝奏": "蓝奏云", "电驴": "电驴", "光鸭": "光鸭云盘"}
GATE_WORDS = re.compile(r"登录|登陆|积分|login|unauthori[sz]ed|forbidden|permission|points", re.I)
LOGIN_WARN = ("⚠️ 爱盼综合搜索现在要求「登录{pts}」才能搜索（站长已打开访问限制开关）。"
              "本脚本不登录、不刷积分，这个站暂时用不了——请先用表里其他网盘搜索站。")
_last = [0.0]

class Gate(Exception):
    pass

def wait():
    w = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and w > 0:
        time.sleep(w)

def body(r):
    return r.body.decode("utf-8", "replace") if isinstance(r.body, bytes) else str(r.body)

def call(method, path, payload=None):
    hdr = {"Referer": f"{BASE}/search", "Origin": BASE, "Accept": "application/json, text/plain, */*"}
    for attempt in (1, 2):
        wait()
        r = (Fetcher.post(BASE + path, json=payload, timeout=60, headers=hdr) if method == "POST"
             else Fetcher.get(BASE + path, timeout=40, headers=hdr))
        _last[0] = time.time()
        t = body(r)
        if r.status in (401, 402, 403):
            raise Gate(f"HTTP {r.status}: {t[:200]}")
        if r.status == 200:
            try:
                j = json.loads(t)
            except ValueError:
                raise RuntimeError(f"非 JSON 响应 (可能被拦截): {t[:200]}")
            code, msg = j.get("code"), str(j.get("msg") or j.get("message") or "")
            if (code in (401, 402, 403) or (code not in (None, 200, 0) and GATE_WORDS.search(msg))) and "list" not in j:
                raise Gate(f"code {code}: {msg}")
            return j
        if r.status in (429, 500, 502, 503, 504) and attempt == 1:
            print(f"[warn] HTTP {r.status} on {path} - retrying in 10 s", file=sys.stderr); time.sleep(10); continue
        raise RuntimeError(f"HTTP {r.status} on {path}: {t[:200]}")

def pan_of(service, u):
    s = (service or "").upper()
    if s in SERVICE_CN:
        return SERVICE_CN[s]
    if u.startswith("magnet:"):
        return "磁力链接"
    if u.startswith("ed2k:"):
        return "电驴"
    host = urlparse(u).netloc.lower()
    for d, n in HOSTS:
        if d in host:
            return n
    return service or host

def code_of(u, pwd):
    if pwd:
        return str(pwd).strip()
    if u.startswith("http"):
        return (parse_qs(urlparse(u).query).get("pwd") or [""])[0]
    return ""

def key_of(u):
    """Dedupe key: magnet -> btih hash; http -> scheme-less host+path+query without pwd and fragment."""
    u = u.strip()
    m = re.match(r"magnet:\?.*?xt=urn:btih:([A-Za-z0-9]+)", u, re.I)
    if m:
        return "magnet:" + m.group(1).lower()
    p = urlparse(u)
    if p.scheme not in ("http", "https"):
        return u
    q = [(k, v) for k, vs in parse_qs(p.query, keep_blank_values=True).items() for v in vs if k.lower() != "pwd"]
    host = p.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return host + p.path.rstrip("/") + ("?" + urlencode(sorted(q)) if q else "")

def clean_url(u):
    u = u.strip()
    return u[:-1] if u.endswith("#") else u

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword")
    ap.add_argument("--sources", default=",".join(SOURCES), help="comma list of " + ",".join(SOURCES))
    ap.add_argument("--pan", default="", help="only keep this netdisk type, e.g. 夸克 / 阿里 / 百度 / 123 / 磁力 / UC / 迅雷 / PikPak")
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip()
    srcs = [s.strip() for s in a.sources.split(",") if s.strip()]
    bad = [s for s in srcs if s not in SOURCES]
    if bad or not srcs:
        ap.error(f"unknown source(s) {bad}; choose from {SOURCES}")
    pan = PAN_ALIAS.get(a.pan.strip().lower(), PAN_ALIAS.get(a.pan.strip(), a.pan.strip())) if a.pan else ""
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")

    gate_msg, ac = "", {}
    try:
        ac = call("GET", "/api/access-control/config").get("data") or {}
    except Gate as e:
        gate_msg = str(e)
    except Exception as e:
        print(f"[warn] 访问限制配置读取失败（继续搜索）: {e}", file=sys.stderr)
    if ac.get("enabled") and (ac.get("protectedFeatures") or {}).get("netdiskSearch", True) and (ac.get("requireLogin") or ac.get("minPoints")):
        pts = f"+至少 {ac.get('minPoints')} 积分" if ac.get("minPoints") else ""
        print(LOGIN_WARN.format(pts=pts), file=sys.stderr)
        print(f"（访问限制配置: {json.dumps(ac, ensure_ascii=False)}）——仍尝试搜索一次以确认", file=sys.stderr)

    merged, order, per_src, errors, gated = {}, [], {}, {}, []
    for s in srcs:
        try:
            j = call("POST", f"/api/sources/{s}", {"name": kw})
        except Gate as e:
            gated.append(s); errors[s] = f"需要登录/积分: {e}"; continue
        except Exception as e:
            errors[s] = str(e); print(f"[warn] 来源 {s} 失败: {e}", file=sys.stderr); continue
        items = j.get("list") or []
        per_src[s] = {"items": len(items), "links": 0}
        for it in items:
            title = re.sub(r"\s+", " ", str(it.get("name") or "")).strip()
            for l in it.get("links") or []:
                u = clean_url(str(l.get("link") or ""))
                if not u:
                    continue
                per_src[s]["links"] += 1
                k = key_of(u)
                code = code_of(u, l.get("pwd"))
                r = merged.get(k)
                if r is None:
                    r = merged[k] = {"title": title, "pan_type": pan_of(l.get("service"), u), "share_url": u,
                                     "extraction_code": code, "sources": [], "other_titles": [], "site_service": l.get("service") or ""}
                    order.append(k)
                if s not in r["sources"]:
                    r["sources"].append(s)
                if title and title != r["title"] and title not in r["other_titles"]:
                    r["other_titles"].append(title)
                if code and not r["extraction_code"]:
                    r["extraction_code"] = code
                if "pwd=" in u and "pwd=" not in r["share_url"]:
                    r["share_url"] = u   # prefer the variant that already carries the code
    if gated and not per_src:
        pts = f"+至少 {ac.get('minPoints')} 积分" if ac.get("minPoints") else "/积分"
        print(LOGIN_WARN.format(pts=pts), file=sys.stderr)
        for s in gated:
            print(f"  {s}: {errors[s]}", file=sys.stderr)
        sys.exit(3)
    if gated:
        print(f"⚠️ 部分来源要求登录/积分，已跳过: {', '.join(gated)}", file=sys.stderr)
    if not per_src:
        print(f"爱盼综合搜索失败：所有来源都出错 {errors}", file=sys.stderr); sys.exit(2)

    rows_all = [merged[k] for k in order]
    rows = [r for r in rows_all if not pan or r["pan_type"] == pan]
    for r in rows:
        r["sources_cn"] = "+".join(SRC_CN[x] for x in r["sources"])
        r["scraped_at"] = ts
    sfx = f"_{pan}" if pan else ""
    sfx += "" if srcs == SOURCES else "_" + "-".join(srcs)
    base = os.path.join(a.out, f"aipan_general_{kw}{sfx}")
    by, by_src = {}, {}
    for r in rows:
        by[r["pan_type"]] = by.get(r["pan_type"], 0) + 1
        by_src[r["sources_cn"]] = by_src.get(r["sources_cn"], 0) + 1
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "site": BASE + "/", "scraped_at": ts, "sources": srcs, "pan_filter": pan,
                   "method": "plain HTTP JSON: GET /api/access-control/config + POST /api/sources/{src} {\"name\": kw}",
                   "access_control": ac, "per_source_raw": per_src, "errors": errors,
                   "unique_links_before_filter": len(rows_all), "unique_links": len(rows), "by_pan": by, "results": rows},
                  f, ensure_ascii=False, indent=1)
    cols = ["title", "pan_type", "share_url", "extraction_code", "sources_cn", "sources", "other_titles", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in rows:
            w.writerow({**r, "sources": ",".join(r["sources"]), "other_titles": " | ".join(r["other_titles"])})
    raw = sum(v["links"] for v in per_src.values())
    print(f"爱盼综合搜索「{kw}」: {len(rows)} 条不重复链接" + (f"（只看{pan}，全部 {len(rows_all)} 条）" if pan else "")
          + f"，各来源原始 {raw} 条 @ {ts}")
    print("各来源: " + "，".join(f"{SRC_CN[s]} {v['items']} 个结果/{v['links']} 链接" for s, v in per_src.items())
          + ("" if not errors else f"；失败: {errors}"))
    print(f"{base}.json\n{base}.csv\n按网盘: {by}\n按来源: {by_src}")
    if not rows:
        print("（没有结果）")
    for r in rows:
        print(f"{r['pan_type']}\t{r['share_url'][:90]}\t码:{r['extraction_code'] or '-'}\t[{r['sources_cn']}]\t{r['title'][:60]}")

if __name__ == "__main__":
    main()
