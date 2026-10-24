#!/workspace/scrapling-env/bin/python
"""Z-Library keyword search (metadata only) via a real browser session.

Loads https://z-library.bz/ in Scrapling StealthySession and lets the site's own
"checking browser" JS run normally (no reimplementation, no cookie forging), then
types the keyword into the site's search box and submits it (the only form used).
Parses the first results page only. Never opens /book/ or /dl/ pages, never logs in,
never downloads files. robots.txt (z-library.bz) observed 2026-10-22: Allow: / except
/resources/ and /components/; the z-library.im mirror it may redirect to disallows
/book/ and /dl/ but not /s/.

Usage: zlib_search.py KEYWORD [--top N] [--json]
Env:   PLAYWRIGHT_BROWSERS_PATH=/workspace/scrapling-env/ms-playwright
"""
import os, sys, time
from urllib.parse import urljoin
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/workspace/scrapling-env/ms-playwright")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hubcommon as h  # noqa: E402

HOME = "https://z-library.bz/"


def _txt(node, css):
    el = node.css_first(css) if hasattr(node, "css_first") else (node.css(css) or [None])[0]
    return " ".join(el.get_all_text().split()) if el is not None else None


def parse(html, base):
    from scrapling.parser import Selector
    sel = Selector(html)
    out = []
    # Template A: <z-bookcard ...> web components (attrs carry metadata)
    for c in sel.css("z-bookcard"):
        a = c.attrib
        fmt = (a.get("extension") or "").upper() or None
        out.append({"title": _txt(c, '[slot="title"]'), "author": _txt(c, '[slot="author"]'),
                    "year": a.get("year") or None, "language": a.get("language") or None,
                    "publisher": a.get("publisher") or None,
                    "file": f"{fmt}, {a.get('filesize')}" if fmt else a.get("filesize"),
                    "detail_url": urljoin(base, a.get("href")) if a.get("href") else None,
                    "zlib_id": a.get("id"), "isbn": a.get("isbn") or None})
    # Template B: <div class="book-item resItemBoxBooks"> list rows
    for b in sel.css("#searchResultBox div.book-item"):
        meta = {}
        for m in b.css("span.meta-item"):
            lab = _txt(m, "span.meta-label") or ""
            full = " ".join(m.get_all_text().split())
            meta[lab.rstrip(": ").lower()] = full[len(lab):].strip() if full.startswith(lab) else full
        link = b.css("div.book-title a")
        href = link[0].attrib.get("href") if link else b.attrib.get("data-url")
        out.append({"title": _txt(b, "div.book-title"), "author": _txt(b, "div.book-author"),
                    "year": meta.get("year"), "language": meta.get("language"),
                    "publisher": _txt(b, "div.book-publisher"),
                    "file": meta.get("file"), "detail_url": urljoin(base, href) if href else None,
                    "zlib_id": None, "isbn": None})
    out = [r for r in out if r["title"] and r["detail_url"]]  # drop ad/placeholder rows
    for r in out:
        r["access"] = r["file"]  # for _hubcommon.emit's text output
    return out


def main():
    ap = h.parser("Z-Library keyword search (first results page, metadata only)")
    args = ap.parse_args()
    from scrapling.fetchers import StealthySession
    t_start = time.time()
    st = {}

    def do_search(page):
        # Scrapling runs page_action before wait_selector, so wait here for the real homepage
        # (the site's own interstitial JS sets its cookie and redirects by itself).
        page.wait_for_selector("#searchFieldx", timeout=60000)
        st["home_ready_s"] = round(time.time() - st["t0"], 2)
        h.pace()
        page.fill("#searchFieldx", args.keyword)
        t1 = time.time(); st["search_at"] = h.now_pt()
        with page.expect_navigation(timeout=60000):
            page.press("#searchFieldx", "Enter")  # site's own form submit
        try:
            page.wait_for_selector("z-bookcard, div.book-item, #searchResultBox", timeout=45000)
        except Exception:
            pass
        st["search_s"] = round(time.time() - t1, 2)  # submit -> results DOM present
        try:  # best-effort settle; page keeps loading third-party assets for a long time
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        st["url"], st["title"], st["html"] = page.url, page.title(), page.content()
        cnt = page.locator("#search-results-count")
        st["count_text"] = " ".join(cnt.first.inner_text().split()) if cnt.count() else None
        return page

    with StealthySession(headless=True, timeout=90000, google_search=False) as s:
        st["t0"] = time.time(); ts = h.now_pt()
        r = s.fetch(HOME, wait_selector="#searchFieldx", page_action=do_search)
        h.LOG.append({"url": HOME, "status": r.status, "seconds": st.get("home_ready_s"),
                      "fetched_at": ts, "note": "homepage incl. JS interstitial"})
    html = st.get("html", "")
    h.LOG.append({"url": st.get("url"), "status": None, "seconds": st.get("search_s"),
                  "fetched_at": st.get("search_at"), "note": "site search form submit"})
    blocked = None
    low = html.lower()
    if "get_jhash" in html:
        blocked = "still on JS interstitial"
    elif "captcha" in low and "z-bookcard" not in low and "book-item" not in low:
        blocked = "captcha"
    elif "/login" in (st.get("url") or ""):
        blocked = "login required"
    res = parse(html, st.get("url") or HOME)[: args.top] if not blocked else []
    if blocked:
        print(f"STOP: {blocked} at {st.get('url')}", file=sys.stderr)
    h.emit("zlib", args, "scrapling StealthySession: homepage (site JS interstitial runs) -> site search form",
           res, extra={"search_url": st.get("url"), "page_title": st.get("title"),
                       "results_count_text": st.get("count_text"), "blocked": blocked,
                       "notes": "first results page only; no /book/ or /dl/ requests; no login"},
           t_start=t_start)
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
