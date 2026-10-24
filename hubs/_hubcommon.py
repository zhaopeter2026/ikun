"""Shared helpers for hub search scripts (gutenberg/openlibrary/archive)."""
import json, os, random, sys, time, datetime, argparse
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "vendor"))  # opencc + pypinyin (pure python)
from opencc import OpenCC  # noqa: E402
from pypinyin import lazy_pinyin  # noqa: E402

UA = "BookHubEval/0.1 (personal research script; low volume)"
PT = ZoneInfo("America/Los_Angeles")
OUT_DIR = "/workspace/scrapes/out/eval_hubs"
_s2t, _t2s = OpenCC("s2t"), OpenCC("t2s")
_last = [0.0]
LOG = []  # per-request log: url, status, seconds, fetched_at (PT)


def now_pt():
    return datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")


def has_cjk(s):
    return any("\u4e00" <= c <= "\u9fff" for c in s)


def zh_variants(s):
    """Return [original, traditional, simplified] unique variants."""
    out = []
    for v in (s, _s2t.convert(s), _t2s.convert(s)):
        if v not in out:
            out.append(v)
    return out


def pinyin_name(s):
    """鲁迅 -> ('lu xun', 'lu, xun')  for matching romanized author names."""
    py = [p for p in lazy_pinyin(_t2s.convert(s)) if p.strip()]
    if not py:
        return None
    return " ".join(py), py[0] + ", " + "".join(py[1:])


def pace():
    gap = random.uniform(1.5, 2.5)
    dt = time.time() - _last[0]
    if dt < gap:
        time.sleep(gap - dt)
    _last[0] = time.time()


def get_json(url, params=None, timeout=60):
    """Paced GET via Scrapling Fetcher (plain HTTP, no browser). Returns parsed JSON."""
    from scrapling.fetchers import Fetcher
    pace()
    t0 = time.time()
    ts = now_pt()
    r = Fetcher.get(url, params=params, headers={"User-Agent": UA, "Accept": "application/json"},
                    timeout=timeout, stealthy_headers=False, follow_redirects=True)
    el = round(time.time() - t0, 2)
    LOG.append({"url": getattr(r, "url", url), "status": r.status, "seconds": el, "fetched_at": ts})
    if r.status in (401, 403, 429) or r.status >= 500:
        raise RuntimeError(f"HTTP {r.status} for {url} (possible block/rate limit)")
    body = r.body if isinstance(r.body, (bytes, bytearray)) else str(r.body).encode()
    return json.loads(body.decode("utf-8"))


def parser(desc):
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("keyword")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--json", action="store_true", help="print JSON to stdout")
    ap.add_argument("--lang", default=None, help="language filter, e.g. zh/chi, en/eng")
    ap.add_argument("--out", default=None, help="override output path")
    return ap


def emit(site, args, method, results, extra=None, t_start=None):
    os.makedirs(OUT_DIR, exist_ok=True)
    doc = {"site": site, "keyword": args.keyword, "lang": args.lang, "top": args.top,
           "method": method, "run_at": now_pt(),
           "elapsed_seconds": round(time.time() - t_start, 2) if t_start else None,
           "requests": LOG, "count": len(results), "results": results}
    if extra:
        doc.update(extra)
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in args.keyword).strip("_")
    path = args.out or os.path.join(OUT_DIR, f"{site}_{safe}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        for i, r in enumerate(results, 1):
            print(f"{i}. {r.get('title')} | {r.get('author')} | {r.get('year')} | {r.get('language')} | {r.get('access')}")
            print(f"   detail: {r.get('detail_url')}")
            for k, v in (r.get('downloads') or {}).items():
                print(f"   {k}: {v}")
        print(f"[{len(results)} results, saved {path}]", file=sys.stderr)
    return path


WANT_FMTS = {"EPUB": "epub", "Text PDF": "pdf", "Additional Text PDF": "pdf", "Image Container PDF": "pdf",
             "DjVuTXT": "txt", "Text": "txt", "Plain Text": "txt", "Kindle": "mobi", "MOBI": "mobi"}


def ia_files(identifier):
    """archive.org Metadata API -> access info + direct download links for epub/pdf/txt."""
    md = get_json(f"https://archive.org/metadata/{identifier}")
    meta = md.get("metadata") or {}
    restricted = str(meta.get("access-restricted-item", "")).lower() == "true"
    dl = {}
    for f in md.get("files", []):
        kind = WANT_FMTS.get(f.get("format", ""))
        name = f.get("name", "")
        if not kind and name.lower().endswith((".epub", ".pdf", ".txt", ".mobi")):
            kind = name.rsplit(".", 1)[-1].lower()
        if not kind or f.get("private") in (True, "true"):
            continue
        if name.endswith(("_encrypted.pdf", "_meta.txt")) or "_lcp" in name:
            continue
        key = kind if kind not in dl else f"{kind}:{name}"
        if len([k for k in dl if k.split(":")[0] == kind]) >= 2:
            continue
        from urllib.parse import quote
        dl[key] = f"https://archive.org/download/{identifier}/{quote(name)}"
    return {"identifier": identifier, "restricted": restricted,
            "lending": meta.get("lending___status") or None,
            "collections": meta.get("collection"), "downloads": dl if not restricted else {},
            "private_or_lending_files_hidden": restricted}


def head_check(url, _retry=True):
    """Paced HEAD (follows redirects) to confirm a file is downloadable without login."""
    import urllib.request
    pace(); t0 = time.time(); ts = now_pt()
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            st, ct, cl = r.status, r.headers.get("Content-Type"), r.headers.get("Content-Length")
    except Exception as e:  # HTTPError has .code
        st, ct, cl = getattr(e, "code", str(e)), None, None
    LOG.append({"url": url, "method": "HEAD", "status": st, "seconds": round(time.time() - t0, 2), "fetched_at": ts})
    if _retry and isinstance(st, int) and st >= 500:
        return head_check(url, _retry=False)
    return {"url": url, "status": st, "content_type": ct, "bytes": cl,
            "free_download_verified": st == 200 and ct is not None and "html" not in ct}
