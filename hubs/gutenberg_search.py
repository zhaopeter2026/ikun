#!/workspace/scrapling-env/bin/python
"""Project Gutenberg keyword search using ONLY the sanctioned catalog feed.

PG robot policy (gutenberg.org/policy/robot_access.html): website is for humans only;
automated access allowed only via mirrors, /robot/harvest, and the catalog data feeds.
robots.txt: Disallow /ebooks/search (also covers /ebooks/search.opds).
gutendex.com robots.txt: Disallow /books/ (and /books?search= 301-redirects to /books/?search=),
so gutendex is NOT used.

Method: download https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv.gz (public-domain
catalog data, ~5.6 MB) at most once per 7 days, cache locally, search offline.
Chinese keywords: simplified<->traditional (OpenCC) + pinyin of names (pypinyin) for author match.
File links are built from PG's standard /ebooks/<id>.<format> URL scheme; they are NOT fetched.

usage: gutenberg_search.py KEYWORD [--top N] [--json] [--lang zh|en|...] [--refresh]
"""
import csv, gzip, io, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hubcommon as h

FEED = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv.gz"
CACHE = os.path.join(h.HERE, "cache", "pg_catalog.csv.gz")
MAX_AGE = 7 * 86400


def ensure_catalog(refresh=False):
    if not refresh and os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < MAX_AGE:
        return {"catalog": CACHE, "downloaded": False,
                "catalog_mtime": time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(os.path.getmtime(CACHE)))}
    from scrapling.fetchers import Fetcher
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    h.pace(); t0 = time.time(); ts = h.now_pt()
    r = Fetcher.get(FEED, headers={"User-Agent": h.UA}, timeout=120, stealthy_headers=False)
    h.LOG.append({"url": FEED, "status": r.status, "seconds": round(time.time() - t0, 2), "fetched_at": ts})
    if r.status != 200:
        raise RuntimeError(f"catalog HTTP {r.status}")
    with open(CACHE, "wb") as f:
        f.write(r.body)
    return {"catalog": CACHE, "downloaded": True, "catalog_mtime": ts}


def norm(s):
    return " ".join(s.lower().replace(",", " ").split())


def subseq(needle, hay):
    it = iter(hay)
    return all(c in it for c in needle)


def score(row, kw):
    title, authors = row["Title"], row["Authors"]
    if h.has_cjk(kw):
        best = 0
        for v in h.zh_variants(kw):
            if v in title: best = max(best, 100 - (len(title) - len(v)))
            elif v in authors: best = max(best, 90)
            elif len(v) >= 3 and subseq(v, title): best = max(best, 70 - (len(title) - len(v)))
        py = h.pinyin_name(kw)
        if py and (py[1] in authors.lower() or py[0] in norm(authors)):
            best = max(best, 80)
        return best
    toks = norm(kw).split()
    t, a = norm(title), norm(authors)
    if all(x in t + " " + a for x in toks):
        return 60 + (30 if norm(kw) in t else 0) + (10 if all(x in t for x in toks) else 0)
    return 0


def links(pid):
    b = f"https://www.gutenberg.org/ebooks/{pid}"
    return {"epub3_images": f"{b}.epub3.images", "epub_noimages": f"{b}.epub.noimages",
            "kindle_kf8": f"{b}.kf8.images", "html": f"{b}.html.images", "txt_utf8": f"{b}.txt.utf-8"}


def main():
    ap = h.parser(__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="force re-download of catalog")
    args = ap.parse_args()
    t_start = time.time()
    info = ensure_catalog(args.refresh)
    lang = (args.lang or "").lower()
    lang = {"chi": "zh", "zho": "zh", "eng": "en"}.get(lang, lang)
    hits = []
    with gzip.open(CACHE, "rt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["Type"] != "Text":
                continue
            if lang and lang not in [x.strip() for x in row["Language"].split(";")]:
                continue
            s = score(row, args.keyword)
            if s > 0:
                hits.append((s, int(row["Text#"]), row))
    hits.sort(key=lambda x: (-x[0], x[1]))
    res = []
    for s, pid, row in hits[: args.top]:
        res.append({"id": pid, "title": " ".join(row["Title"].split()), "author": row["Authors"],
                    "year": row["Issued"][:4], "year_note": "PG release date (Issued), not original publication",
                    "language": row["Language"], "subjects": row["Subjects"],
                    "detail_url": f"https://www.gutenberg.org/ebooks/{pid}", "downloads": links(pid),
                    "access": "free (public domain in US, no login)", "match_score": s})
    h.emit("gutenberg", args, "pg_catalog.csv.gz offline search (sanctioned catalog feed)", res,
           extra={"catalog_info": info, "total_matches": len(hits),
                  "links_note": "constructed from PG standard URL scheme; not fetched per robot policy"},
           t_start=t_start)


if __name__ == "__main__":
    main()
