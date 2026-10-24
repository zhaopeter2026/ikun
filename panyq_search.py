#!/workspace/scrapling-env/bin/python
"""Search 盘友圈 panyq.com (Next.js netdisk search site) for a keyword, in a browser like a visitor.

Access (no login, no robots.txt - /robots.txt is 404):
  Everything runs in ONE Scrapling StealthySession (Chromium) that uses the site's own UI:
  1. open https://panyq.com/, type the keyword in the search box, press Enter. The site's Next.js server action
     redirects to /search/<hash>; that page's own JS calls GET /api/search?sign=... and gets
     {"data":{"hits":[{eid, desc, password, group}], "maxPageNum": N}} - 10 results/page. The script only LISTENS to
     that response (titles, file list, truncated link like https://pan.quark.cn/s/6d365478e..., 提取码, netdisk group).
     More pages (--pages) = clicking the site's own ">" pagination button.
  2. Full links (--links N): click result i's own link button (button.netdisk) and capture what the page receives -
     the new tab's URL, or the full link in the page's own /api/link-fetch response. The site re-shares the file
     at click time, so the real link is a NEW share URL, different from the truncated one in the list.
     The script never calls /api/link-fetch itself and never computes/forges its fingerprint headers.
  Anti-bot: /api/link-fetch answers 403 {"error":"robot detected"} to automated browsers. Tested 2026-10-22:
     refused both headless and headful (box X display), also with system Chrome - so in practice --links usually
     yields nothing and the script prints a Chinese warning and still saves titles + truncated links.
     Mode auto = headless first, then once more headful on $DISPLAY if refused. Stops clicking after the first refusal.
  Rate limit: re-loading search pages gives HTTP 429 on /api/search and a "请验证您是人类" click-captcha. In tests
     2026-10-22 page 2 ALWAYS hit it (even with --links 0 and 5-8 s wait), so in practice only page 1 (10 results)
     is reachable - keep --pages 1 (default). Back-to-back runs within a few minutes also hit it.
     The script does not solve the captcha - it detects it, warns, and stops (keeps an earlier good output file).
Polite: 1.5-2.5 s random delay between page loads / clicks, 5-8 s before each next page; waits ~20 s per click for the new tab; one browser at a time.
Usage: panyq_search.py KEYWORD [--pages N (default 1)] [--links N (default 5; 0 = titles only)]
                       [--pan all|ali|baidu|quark|xunlei] [--mode auto|headless|headful] [--out DIR]
"""
import os, sys, re, csv, json, time, random, datetime, argparse, html
from zoneinfo import ZoneInfo

BASE = "https://panyq.com"
PT = ZoneInfo("America/Los_Angeles")
GROUP_CN = {"quark": "夸克网盘", "baidu": "百度网盘", "ali": "阿里云盘", "aliyun": "阿里云盘", "alipan": "阿里云盘",
            "xunlei": "迅雷云盘", "uc": "UC网盘", "115": "115网盘", "123": "123云盘", "tianyi": "天翼云盘"}
PAN_CAT = {"all": "全部", "ali": "阿里云盘", "baidu": "百度网盘", "quark": "夸克网盘", "xunlei": "迅雷云盘"}
HOSTS = [("quark.cn", "夸克网盘"), ("aliyundrive.com", "阿里云盘"), ("alipan.com", "阿里云盘"), ("baidu.com", "百度网盘"),
         ("xunlei.com", "迅雷云盘"), ("uc.cn", "UC网盘"), ("189.cn", "天翼云盘"), ("139.com", "移动云盘"), ("123pan", "123云盘")]
PAN_RE = re.compile(r"https?://(?:pan\.quark\.cn|pan\.baidu\.com|www\.aliyundrive\.com|www\.alipan\.com|pan\.xunlei\.com|"
                    r"drive\.uc\.cn|cloud\.189\.cn|yun\.139\.com|www\.123pan\.com)/[^\s\"'<>\\]+")
CAPTCHA = "验证您是人类"
REFUSED_WARN = "⚠️ 盘友圈拒绝了浏览器取链接（/api/link-fetch 返回 403 robot detected），这次拿不到完整链接；已照常保存标题和列表里的截断链接。"
_last = [0.0]

def pause():
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    _last[0] = time.time()

def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()

def pan_of_url(u):
    for d, n in HOSTS:
        if d in u:
            return n
    return ""

def hit_row(h, page_no, pos):
    raw = h.get("desc") or ""
    parts = [clean(p) for p in raw.split("✔")]
    trunc = ""
    m = re.search(r"https?://\S+", clean(raw))
    if m:
        trunc = m.group(0)
    body = [p for p in parts if p and not re.match(r"https?://", p)]
    grp = (h.get("group") or "").lower()
    return {"title": body[0] if body else "", "pan_type": GROUP_CN.get(grp, grp or pan_of_url(trunc)),
            "share_url": "", "listed_url": trunc, "extraction_code": h.get("password") or "",
            "files": " / ".join(body[1:])[:400], "eid": h.get("eid") or "", "page": page_no, "pos": pos, "link_note": ""}

def run(kw, pages, links, pan, headless):
    """One browser session. Returns (rows, max_page, refused, captcha, notes)."""
    from scrapling.fetchers import StealthySession
    st = {"rows": [], "max_page": None, "refused": False, "captcha": False, "notes": [], "api": [], "lf": []}

    def act(page):
        ctx = page.context

        def on_resp(r):
            u = r.url
            try:
                if u.startswith(BASE + "/api/search"):
                    st["api"].append((r.status, r.json() if r.status == 200 else None))
                elif u.startswith(BASE + "/api/link-fetch"):
                    try:
                        body = r.text()
                    except Exception:
                        body = ""
                    st["lf"].append((r.status, body[:2000]))
            except Exception as e:
                st["notes"].append(f"响应解析失败: {str(e)[:60]}")
        ctx.on("response", on_resp)

        def wait_api(n_before, secs=25):
            end = time.time() + secs
            while time.time() < end and len(st["api"]) <= n_before:
                page.wait_for_timeout(300)
            return st["api"][n_before] if len(st["api"]) > n_before else None

        def captcha_check():
            try:
                if CAPTCHA in page.content():
                    st["captcha"] = True
            except Exception:
                pass
            return st["captcha"]

        page.wait_for_timeout(3000)
        # let the site's own "安全验证" interstitial (if any) finish on its own
        for _ in range(15):
            if page.locator("input[name=query]").count() and "安全验证" not in page.content():
                break
            page.wait_for_timeout(1000)
        if pan != "all":
            pause(); page.locator(f'button[name=cat][value="{pan}"]').first.click()
        n0 = len(st["api"])
        pause()
        inp = page.locator("input[name=query]").first
        inp.click(); inp.fill(kw); page.wait_for_timeout(500); inp.press("Enter")
        clicks_left = links
        for p in range(1, pages + 1):
            res = wait_api(n0)
            if res is None or res[0] != 200:
                if captcha_check() or (res and res[0] == 429):
                    st["captcha"] = True
                else:
                    st["notes"].append(f"第 {p} 页没拿到搜索结果（{res[0] if res else '超时'}）")
                break
            data = (res[1] or {}).get("data") or {}
            st["max_page"] = data.get("maxPageNum", st["max_page"])
            hits = data.get("hits") or []
            rows = [hit_row(h, p, i) for i, h in enumerate(hits)]
            st["rows"] += rows
            if not hits:
                break
            try:
                page.wait_for_selector("button.netdisk", timeout=20000)
            except Exception:
                st["notes"].append(f"第 {p} 页没有出现链接按钮")
            # click this page's link buttons (the site's own UI)
            for i, r in enumerate(rows):
                if clicks_left <= 0 or st["refused"] or st["captcha"]:
                    break
                clicks_left -= 1
                try:
                    btn = page.locator("button.netdisk").nth(i)
                    label = btn.inner_text(timeout=5000).strip()
                    if r["listed_url"] and label.rstrip(".") and not r["listed_url"].startswith(label.rstrip(".")[:25]):
                        r["link_note"] = "按钮和结果对不上，跳过"; continue
                    card = btn.locator("xpath=..")
                    lf0 = len(st["lf"])
                    pause()
                    btn.scroll_into_view_if_needed()
                    pop = None
                    try:
                        with ctx.expect_page(timeout=15000) as pi:
                            btn.click()
                        pop = pi.value
                    except Exception:
                        pass
                    got, end = "", time.time() + 25
                    while time.time() < end and not got:
                        if pop is not None:
                            try:
                                if PAN_RE.match(pop.url or ""):
                                    got = pop.url
                            except Exception:
                                pass
                        for s_, b_ in st["lf"][lf0:]:
                            if s_ == 200:
                                m = PAN_RE.search(b_.replace("\\/", "/"))
                                if m: got = m.group(0)
                            elif s_ == 403 or "robot" in b_:
                                st["refused"] = True
                        if st["refused"]:
                            break
                        # the button may get replaced by the full link inside the card
                        try:
                            m = PAN_RE.search(card.inner_text(timeout=1000))
                            if m and not m.group(0).endswith("..."):
                                got = m.group(0)
                        except Exception:
                            pass
                        page.wait_for_timeout(500)
                    if pop is not None:
                        try: pop.close()
                        except Exception: pass
                    _last[0] = time.time()
                    if got:
                        r["share_url"] = got.rstrip(".")
                        r["pan_type"] = pan_of_url(got) or r["pan_type"]
                        r["link_note"] = "点击后拿到（网站转存后的新分享链接）"
                    elif st["refused"]:
                        r["link_note"] = "网站判定机器人（403 robot detected）"
                    else:
                        r["link_note"] = "点击后没等到链接"
                        captcha_check()
                except Exception as e:
                    r["link_note"] = f"点击失败: {str(e)[:80]}"
            if p >= pages or st["captcha"] or (st["max_page"] and p >= st["max_page"]):
                break
            nxt = page.locator(f'form:has(input[name=pageNum][value="{p + 1}"]) button').first
            if not nxt.count():
                break
            n0 = len(st["api"])
            time.sleep(random.uniform(5, 8))  # /api/search rate-limits fast paging (429 + captcha)
            pause(); nxt.click()
        return page

    with StealthySession(headless=headless, google_search=False, network_idle=False, timeout=90000,
                         blocked_domains={"googlesyndication.com", "doubleclick.net", "hm.baidu.com"}) as s:
        pause()
        s.fetch(BASE + "/", page_action=act)
    return st

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--pages", type=int, default=1); ap.add_argument("--links", type=int, default=5)
    ap.add_argument("--pan", default="all", choices=list(PAN_CAT)); ap.add_argument("--mode", default="auto", choices=["auto", "headless", "headful"])
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip()
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    has_display = bool(os.environ.get("DISPLAY")) or os.path.isdir("/tmp/.X11-unix")
    if a.mode != "headless" and not os.environ.get("DISPLAY") and os.path.isdir("/tmp/.X11-unix"):
        xs = sorted(os.listdir("/tmp/.X11-unix"))
        if xs: os.environ["DISPLAY"] = ":" + xs[0].lstrip("X")
    first_headless = a.mode != "headful"
    st = run(kw, max(1, a.pages), max(0, a.links), a.pan, headless=first_headless)
    tried = ["无头" if first_headless else "有界面"]
    if a.mode == "auto" and a.links > 0 and st["refused"] and not st["captcha"] and has_display:
        print("[提示] 无头浏览器取链接被拒，改用有界面浏览器（DISPLAY=%s）再试一次…" % os.environ.get("DISPLAY"), file=sys.stderr)
        time.sleep(random.uniform(4, 6))
        st2 = run(kw, max(1, -(-a.links // 10)), a.links, a.pan, headless=False)
        tried.append("有界面")
        by_eid = {r["eid"]: r for r in st2["rows"]}
        for r in st["rows"]:
            r2 = by_eid.get(r["eid"])
            if r2 and r2["link_note"]:
                r["share_url"], r["link_note"] = r2["share_url"], r2["link_note"] + "（有界面）"
                if r2["share_url"]: r["pan_type"] = r2["pan_type"]
        st["refused"], st["captcha"] = st2["refused"], st["captcha"] or st2["captcha"]
        st["notes"] += st2["notes"]
    # dedupe: same eid, or same resolved link
    rows, seen = [], set()
    for r in st["rows"]:
        k = r["share_url"] or ("eid:" + r["eid"]) if r["eid"] else r["listed_url"] + r["title"]
        if k in seen:
            continue
        seen.add(k); r["scraped_at"] = ts; rows.append(r)
    got = sum(1 for r in rows if r["share_url"])
    tried_n = sum(1 for r in rows if r["link_note"])
    sfx = f"_{PAN_CAT[a.pan]}" if a.pan != "all" else ""
    base = os.path.join(a.out, f"panyq_{kw}{sfx}")
    if not rows and st["captcha"] and os.path.exists(base + ".json"):
        base += "_blocked"  # keep the last good output instead of overwriting it with an empty one
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "pan": a.pan, "scraped_at": ts, "site_pages": st["max_page"],
                   "site_total_approx": (st["max_page"] or 0) * 10, "browser_modes": tried, "links_requested": a.links,
                   "links_resolved": got, "link_fetch_refused": st["refused"], "captcha_or_429": st["captcha"],
                   "notes": st["notes"], "results": rows}, f, ensure_ascii=False, indent=1)
    cols = ["title", "pan_type", "share_url", "listed_url", "extraction_code", "files", "eid", "page", "pos", "link_note", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["pan_type"]] = by.get(r["pan_type"], 0) + 1
    print(f"盘友圈「{kw}」: {len(rows)} 条结果（网站共约 {st['max_page'] or '?'} 页 ≈ {(st['max_page'] or 0) * 10} 条）；"
          f"完整链接 {got}/{tried_n} 个点开（浏览器: {'→'.join(tried)}）@ {ts}")
    if st["refused"] and a.links > 0:
        print(REFUSED_WARN)
    if st["captcha"]:
        print("⚠️ 盘友圈弹出了“请验证您是人类”/429 限流，脚本没有去过验证，已停止；请隔几分钟再试。")
    for n in st["notes"]:
        print("注意: " + n)
    print(f"{base}.json\n{base}.csv\n按网盘: {by}")
    if not rows:
        print("（没有结果）")
    for r in rows:
        print(f"{r['pan_type']}\t{r['share_url'] or '-'}\t列表:{r['listed_url']}\t码:{r['extraction_code'] or '-'}\t{r['title'][:50]}\t{r['link_note']}")

if __name__ == "__main__":
    main()
