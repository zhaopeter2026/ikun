#!/workspace/scrapling-env/bin/python
"""Open Library keyword search via the documented Search API (search.json).

Rules (openlibrary.org/developers/api): use API not HTML; identify with User-Agent; 1 req/s
unidentified (3 req/s with UA+email); no bulk harvesting. robots.txt "Disallow: /search" /
"/api" targets crawlers of HTML; search.json is the officially documented endpoint.
Download links: for works with ebook_access=public, the IA scan identifiers are resolved via
archive.org Metadata API; borrowable/printdisabled are reported as borrow-only (no links fetched).

usage: openlibrary_search.py KEYWORD [--top N] [--json] [--lang chi|eng|...] [--no-files] [--verify]
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hubcommon as h

API = "https://openlibrary.org/search.json"
FIELDS = "key,title,author_name,first_publish_year,language,ebook_access,ia,has_fulltext,public_scan_b,edition_count"
ACCESS = {"public": "free (public scan, no login)", "borrowable": "borrow-only (login + loan)",
          "printdisabled": "restricted (print-disabled patrons only)", "no_ebook": "no ebook (metadata only)"}


def main():
    ap = h.parser(__doc__.splitlines()[0])
    ap.add_argument("--no-files", action="store_true", help="skip archive.org file resolution")
    ap.add_argument("--verify", action="store_true", help="HEAD-check first file link of each free item")
    args = ap.parse_args()
    t0 = time.time()
    kw = args.keyword
    q = " OR ".join(f'"{v}"' if " " not in v else f"({v})" for v in h.zh_variants(kw)) if h.has_cjk(kw) else kw
    params = {"q": q, "fields": FIELDS, "limit": max(args.top, 5)}
    if args.lang:
        params["language"] = {"zh": "chi", "en": "eng"}.get(args.lang, args.lang)
    d = h.get_json(API, params)
    res = []
    for doc in d.get("docs", [])[: args.top]:
        acc = doc.get("ebook_access", "no_ebook")
        item = {"title": doc.get("title"), "author": "; ".join(doc.get("author_name", []) or []),
                "year": doc.get("first_publish_year"), "year_note": "first_publish_year of the work",
                "language": doc.get("language"), "edition_count": doc.get("edition_count"),
                "detail_url": "https://openlibrary.org" + doc["key"], "ebook_access": acc,
                "access": ACCESS.get(acc, acc), "downloads": {}, "ia_ids_count": len(doc.get("ia") or [])}
        if acc == "public" and doc.get("ia") and not args.no_files:
            for ia in doc["ia"][:4]:  # find first non-restricted scan
                info = h.ia_files(ia)
                if not info["restricted"] and info["downloads"]:
                    item["downloads"] = info["downloads"]
                    item["ia_identifier"] = ia
                    item["ia_detail_url"] = f"https://archive.org/details/{ia}"
                    if args.verify:
                        item["verify"] = h.head_check(next(iter(info["downloads"].values())))
                    break
        res.append(item)
    h.emit("openlibrary", args, f"search.json q={q!r}", res, extra={"numFound": d.get("numFound")}, t_start=t0)


if __name__ == "__main__":
    main()
