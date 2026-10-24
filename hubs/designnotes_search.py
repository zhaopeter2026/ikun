#!/usr/bin/env python3
"""Search designnotes.cn via its public JSON API (/api/tools/search).

usage: designnotes_search.py KEYWORD [--cat 分类] [--top N] [--json] [--enrich]

Search results carry only id/title/img/url/depiction(short zh desc)/category.
--enrich pages through /api/tools/category/{id} (60/page, the site's list size)
for the matched categories to add the long description, tags, fee, registration, vpn.

robots.txt (Cloudflare-managed): User-agent: * Allow: /  with
Content-Signal: search=yes,ai-train=no,use=reference. Do not use output for AI training.
Requests are paced ~2 s apart. No cookies / login needed.
Note: the site has no per-resource detail page (details open in an in-page modal;
URL stays https://designnotes.cn/), so detail_url is null; resource id is included.
"""
import argparse, json, sys, time, urllib.parse, urllib.request
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

BASE = "https://designnotes.cn"
UA = "Mozilla/5.0 (compatible; designnotes-search-script/1.0)"
PAGE_SIZE = 24  # same as the site's own client
_last = [0.0]

def get(path):
    wait = 2.0 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=50) as r:
        _last[0] = time.time()
        return json.load(r)

def clean(u):
    s = urlsplit(u or "")
    q = [(k, v) for k, v in parse_qsl(s.query, keep_blank_values=True) if not (k == "ref" and "designnotes" in v)]
    return urlunsplit((s.scheme, s.netloc, s.path, urlencode(q), s.fragment))

def norm(it):
    zh = lambda f: (it.get(f) or {}).get("zh")
    return {
        "id": it.get("id"),
        "name": it.get("title"),
        "depiction_zh": zh("depiction"),
        "description_zh": zh("description"),
        "category": zh("category"),
        "category_en": (it.get("category") or {}).get("en"),
        "category_id": it.get("category_id"),
        "tags": [t.get("zh") for t in it.get("tags") or []],
        "fee": zh("fee"),
        "registration": zh("registration"),
        "needs_vpn": bool(it.get("vpn")),
        "url": it.get("url"),
        "url_clean": clean(it.get("url")),
        "detail_url": None,
        "icon": f"{BASE}/{it['img']}" if it.get("img") else None,
        "createdAt": it.get("createdAt"),
    }

def search(keyword, cat=None, top=10):
    out, page, total = [], 1, None
    while len(out) < top:
        q = urllib.parse.urlencode({"q": keyword, "page": page, "limit": PAGE_SIZE, "descending": "true"})
        d = get(f"/api/tools/search?{q}")
        total = d.get("total")
        for it in d.get("items", []):
            r = norm(it)
            if cat and cat not in (r["category"] or "") and cat.lower() != (r["category_en"] or "").lower():
                continue
            out.append(r)
        if not d.get("hasMore"):
            break
        page += 1
    return out[:top], total

def enrich(res):
    cats = {c["name_zh"]: c["id"] for c in get("/api/categories")}
    full = {}
    for name in sorted({r["category"] for r in res if r["category"] in cats}):
        page = 1
        while True:
            d = get(f"/api/tools/category/{cats[name]}?page={page}&limit=60&descending=true")
            for it in d.get("items", []):
                full[it["id"]] = it
            if not d.get("hasMore") or all(r["id"] in full for r in res if r["category"] == name):
                break
            page += 1
    for i, r in enumerate(res):
        if r["id"] in full:
            n = norm(full[r["id"]]); n["depiction_zh"] = n["depiction_zh"] or r["depiction_zh"]; res[i] = n
        else:
            r["category_id"] = cats.get(r["category"])
    return res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword"); ap.add_argument("--cat"); ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", action="store_true"); ap.add_argument("--enrich", action="store_true")
    a = ap.parse_args()
    res, total = search(a.keyword, a.cat, a.top)
    if a.enrich:
        res = enrich(res)
    if a.json:
        json.dump({"keyword": a.keyword, "cat": a.cat, "api_total": total, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                   "results": res}, sys.stdout, ensure_ascii=False, indent=2); print()
    else:
        print(f"# {a.keyword!r} api_total={total} shown={len(res)}")
        for i, r in enumerate(res, 1):
            print(f"{i}. {r['name']} [{r['category']}] {r['url']}\n   {r['depiction_zh']}" + (f" | {r['description_zh']}" if r['description_zh'] else ""))

if __name__ == "__main__":
    main()
