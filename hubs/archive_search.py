#!/workspace/scrapling-env/bin/python
"""Internet Archive keyword search via advancedsearch.php + Metadata API.

robots.txt only disallows /control/ and /report/. Uses the public JSON APIs
(advancedsearch.php, /metadata/<id>); download links are /download/<id>/<file>.
Items with access-restricted-item=true (collections inlibrary/printdisabled/lending)
are borrow-only: their PDF/EPUB are not freely downloadable, so no links are returned.

usage: archive_search.py KEYWORD [--top N] [--json] [--lang chi|eng|...] [--all-media] [--no-files] [--verify]
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hubcommon as h

API = "https://archive.org/advancedsearch.php"
FL = ["identifier", "title", "creator", "year", "date", "language", "mediatype", "downloads", "collection"]
LEND = {"inlibrary", "printdisabled", "lendinglibrary", "browserlending"}


def build_q(kw, lang, all_media):
    vs = h.zh_variants(kw) if h.has_cjk(kw) else [kw]
    parts = []
    for v in vs:
        v = v.replace('"', "")
        parts += [f'title:("{v}")' if h.has_cjk(v) else f"title:({v})",
                  f'creator:("{v}")' if h.has_cjk(v) else f"creator:({v})"]
    q = "(" + " OR ".join(parts) + ")"
    if not all_media:
        q += " AND mediatype:texts"
    if lang:
        l = {"zh": "chi", "en": "eng"}.get(lang, lang)
        names = {"chi": "(chi OR Chinese OR zh OR zho)", "eng": "(eng OR English OR en)"}
        q += f" AND language:{names.get(l, l)}"
    return q


def main():
    ap = h.parser(__doc__.splitlines()[0])
    ap.add_argument("--all-media", action="store_true", help="include audio/video (default texts only)")
    ap.add_argument("--no-files", action="store_true")
    ap.add_argument("--verify", action="store_true", help="HEAD-check first file link per free item")
    args = ap.parse_args()
    t0 = time.time()
    q = build_q(args.keyword, args.lang, args.all_media)
    params = {"q": q, "fl[]": FL, "rows": args.top, "output": "json", "sort[]": "downloads desc"}
    d = h.get_json(API, params)
    res = []
    for doc in d["response"]["docs"]:
        ident = doc["identifier"]
        coll = doc.get("collection") or []
        coll = [coll] if isinstance(coll, str) else coll
        lend = bool(LEND & set(coll))
        cr = doc.get("creator")
        item = {"identifier": ident, "title": doc.get("title"),
                "author": "; ".join(cr) if isinstance(cr, list) else cr,
                "year": doc.get("year") or (doc.get("date") or "")[:4] or None,
                "language": doc.get("language"), "mediatype": doc.get("mediatype"),
                "downloads_count": doc.get("downloads"), "collections": coll,
                "detail_url": f"https://archive.org/details/{ident}", "downloads": {}}
        if not args.no_files:
            info = h.ia_files(ident)
            lend = lend or info["restricted"]
            item["downloads"] = {} if lend else info["downloads"]
            if args.verify and item["downloads"]:
                item["verify"] = h.head_check(next(iter(item["downloads"].values())))
        item["access"] = "borrow-only (login + loan; files restricted)" if lend else \
            ("free (no login)" if item["downloads"] or args.no_files else "free item but no epub/pdf/txt file")
        res.append(item)
    h.emit("archive", args, f"advancedsearch q={q!r} sort=downloads desc", res,
           extra={"numFound": d["response"]["numFound"]}, t_start=t0)


if __name__ == "__main__":
    main()
