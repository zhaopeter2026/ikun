#!/workspace/scrapling-env/bin/python
"""Search 夸夸盘搜 kuakuaso.com (Vue SPA netdisk search engine; same program/operator as 盘了个盘 panlegepan.com) with
Peter's account. LAST-priority source: real links cost the account's daily free quota (免费用户 2次/天 as shown).

Access: browser only (Scrapling StealthySession). The site's /api/* requests are signed and the responses encrypted by
  the page JS; we never call or decode the API ourselves - we drive the page UI and read what the page shows:
  - titles: the search page DOM (.result-card -> .result-title, .file-name, .pan-badge, .filetype-badge, .result-time)
  - links:  the site's own 「复制链接」 button -> read the clipboard (the page puts the share link there)
Session: ~/.secrets/kuakuaso_session.json (chmod 600) = cookies + localStorage (pd_token, pd_email, pd_vip_time,
  pd_down_count; pd_saved_pwd is never stored). Restored by setting localStorage on the origin before loading.
  If the page shows no logged-in user badge, re-login once via the site's own login form using
  KUAKUASO_EMAIL (env, else read by the script itself from ~/.secrets/kuakuaso.env) and KUAKUASO_PASSWORD
  (env var only; never read from files, printed or saved; missing -> clear message, exit 2),
  The site auto-opens its login modal for logged-out visitors (2026-10-22); we fill that modal.
  then save the new session. Captcha/2FA in the login form -> stop (exit 3).
Quota: free account 2次/天; when used up the page answers 429 with a 「开通会员」 toast -> we stop taking links (exit 0,
  results saved, rows marked 额度用完). VIP is paid - never opened or bought by this script.
Polite: 1.5-2.5 s random delays between page loads and clicks.
Usage: kuakuaso_search.py KEYWORD [--pan all|kuake|baidu|ali|xunlei|tianyi|uc] [--max-pages N (default 1)]
                          [--links N (default 0; each link costs quota)] [--out DIR]
Exit: 0 ok, 2 login failed, 3 captcha/2FA or other manual step needed.
"""
import os, sys, re, csv, json, time, random, datetime, argparse
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/workspace/scrapling-env/ms-playwright")
from scrapling.fetchers import StealthySession

BASE = "https://www.kuakuaso.com"
PT = ZoneInfo("America/Los_Angeles")
SESSION = os.path.expanduser(os.environ.get("KUAKUASO_SESSION", "~/.secrets/kuakuaso_session.json"))
ENVFILE = os.path.expanduser("~/.secrets/kuakuaso.env")
PANS = ["all", "kuake", "baidu", "ali", "xunlei", "tianyi", "uc"]
BLOCK = {"hm.baidu.com"}
_last = [0.0]

def pause():
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    _last[0] = time.time()

def load_envfile():
    """Fill missing KUAKUASO_* vars (except the password) from ~/.secrets/kuakuaso.env, so the script works
    without `set -a; . ~/.secrets/kuakuaso.env`. The password is only ever taken from the environment."""
    if not os.path.exists(ENVFILE):
        return
    for line in open(ENVFILE, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k.startswith("KUAKUASO_") and k != "KUAKUASO_PASSWORD" and not os.environ.get(k):
            os.environ[k] = v

def email():
    return os.environ.get("KUAKUASO_EMAIL", "").strip()

def load_session():
    try:
        return json.load(open(SESSION, encoding="utf-8"))
    except Exception:
        return {}

def save_session(ctx, ls):
    ls = {k: v for k, v in ls.items() if k.startswith("pd_") and k != "pd_saved_pwd"}
    data = {"saved_at": datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z"), "cookies": ctx.cookies(), "localStorage": ls}
    os.makedirs(os.path.dirname(SESSION), exist_ok=True)
    fd = os.open(SESSION, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.chmod(SESSION, 0o600)

def close_promo(page):
    try:
        page.locator(".promo-overlay").get_by_text("关闭").first.click(timeout=3000)
    except Exception:
        pass
    page.evaluate("document.querySelectorAll('.promo-overlay').forEach(e=>e.remove())")

def logged_in(page):
    return page.locator(".user-badge").count() > 0

def login_modal(page):
    """The visible login modal (.modal-overlay with a password field), or None."""
    ms = page.locator(".modal-overlay:visible").filter(has=page.locator("input[type=password]"))
    return ms.last if ms.count() else None

def login(page, state):
    em, pw = email(), os.environ.get("KUAKUASO_PASSWORD", "")
    if not em:
        state["fatal"] = (2, f"夸夸盘搜登录失效：没有 KUAKUASO_EMAIL（环境变量或 {ENVFILE}）"); return False
    if not pw:
        state["fatal"] = (2, "夸夸盘搜登录失效：环境变量里没有 KUAKUASO_PASSWORD（密码只从环境变量读；"
                             "请先 export KUAKUASO_PASSWORD=… 再运行）"); return False
    close_promo(page)
    # since 2026-10-22 the site pops the login modal by itself for logged-out visitors; that overlay
    # intercepts clicks on the header 「登录 / 注册」 button, so use the open modal if there is one
    m = login_modal(page)
    if m is None:
        page.get_by_text("登录 / 注册").first.click(); page.wait_for_timeout(1500)
        m = login_modal(page) or page.locator(".modal-overlay").last
    try:
        m.locator(".modal-tabs .tab", has_text="登录").first.click(timeout=3000); page.wait_for_timeout(500)
    except Exception:
        pass
    if m.locator("img[src*=captcha], [class*=captcha], [class*=verify], iframe").count():
        state["fatal"] = (3, "夸夸盘搜登录出现验证码/二次验证，需要 Peter 手动处理"); return False
    m.locator("input[type=email], input[placeholder*=邮箱], input[type=text]").first.fill(em); time.sleep(0.8)
    m.locator("input[type=password]").first.fill(pw); time.sleep(1.2)
    m.locator('button.form-submit, button:has-text("登录")').last.click(); page.wait_for_timeout(5000)
    if page.locator("[class*=captcha], [class*=verify]").count():
        state["fatal"] = (3, "夸夸盘搜登录出现验证码/二次验证，需要 Peter 手动处理"); return False
    if not page.evaluate("localStorage.getItem('pd_token')"):
        t = page.evaluate("Array.from(document.querySelectorAll('[class*=toast]')).map(e=>e.innerText).join(' ')")
        state["fatal"] = (2, f"夸夸盘搜登录失败：{t.strip()[:80] or '没有拿到 token'}"); return False
    state["relogin"] = True
    return True

def read_quota(page):
    try:
        page.evaluate("window.scrollTo(0,0)"); time.sleep(0.5)
        page.locator(".user-badge").first.click(); page.wait_for_timeout(1200)
        txt = page.locator(".dropdown-user-plate").first.inner_text()
        page.locator(".user-badge").first.click()
    except Exception:
        txt = ""
    ls = page.evaluate("({vip: localStorage.getItem('pd_vip_time'), dc: localStorage.getItem('pd_down_count')})")
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    level = next((l for l in lines if "用户" in l), "")
    per_day = next((l for l in lines if "次/天" in l), (ls.get("dc") or "?") + "次/天")
    vip = ls.get("vip") or ""
    return {"level": level or ("VIP用户" if vip and vip > "2021-01-02" else "免费用户"), "quota": per_day,
            "vip_time": "" if vip.startswith("2021-01-01") else vip}

def parse_cards(page):
    return page.evaluate("""() => Array.from(document.querySelectorAll('.result-card')).filter(c=>c.querySelector('.result-actions')).map((c,i)=>({
        dom_index: i,
        title: (c.querySelector('.result-title')||{}).innerText||'',
        files: Array.from(c.querySelectorAll('.file-name')).map(e=>e.innerText.trim()).slice(0,8).join(' / '),
        more: ((c.querySelector('.file-list-box')||{}).innerText||'').match(/共\\s*\\d+\\s*个文件\\/文件夹/)?.[0]||'',
        pan_type: ((c.querySelector('.pan-badge')||{}).innerText||'').trim(),
        file_type: ((c.querySelector('.filetype-badge')||{}).innerText||'').trim(),
        date: ((c.querySelector('.result-time')||{}).innerText||'').trim()}))""")

def get_link(page, idx):
    """Click the site's own 复制链接 on card idx; return (link, note)."""
    cards = page.locator(".result-card")
    btn = cards.nth(idx).locator('button:has-text("复制链接")')
    page.evaluate("navigator.clipboard.writeText('')")
    page.evaluate("document.querySelectorAll('[class*=toast]').forEach(e=>e.remove())")
    btn.evaluate("e=>e.scrollIntoView({block:'center'})"); page.wait_for_timeout(600)
    pause()
    try:
        btn.click(timeout=6000)
    except Exception:
        close_promo(page); btn.click(timeout=6000)
    clip = ""
    for _ in range(12):
        page.wait_for_timeout(500)
        try:
            clip = page.evaluate("navigator.clipboard.readText()") or ""
        except Exception:
            clip = ""
        if clip:
            break
    toast = page.evaluate("Array.from(document.querySelectorAll('[class*=toast]')).map(e=>e.innerText).join(' ')").strip()
    modal = page.evaluate("Array.from(document.querySelectorAll('.modal-overlay')).filter(e=>e.offsetParent!==null).map(e=>e.innerText.slice(0,120)).join(' ')")
    if re.match(r"https?://", clip.strip()):
        return clip.strip(), ""
    if "开通会员" in toast or "开通会员" in modal or "会员中心" in modal or "额度" in toast:
        page.keyboard.press("Escape")
        return "", "额度用完"
    if "登录" in modal and "邮箱" in modal:
        page.keyboard.press("Escape")
        return "", "登录失效"
    return "", (toast or modal or "没拿到链接")[:60]

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--pan", default="all", choices=PANS)
    ap.add_argument("--max-pages", type=int, default=1); ap.add_argument("--links", type=int, default=0)
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    load_envfile()
    kw = a.keyword.strip()
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    sess = load_session()
    state = {"rows": [], "quota": {}, "relogin": False, "fatal": None, "total": "", "links_tried": 0, "stop_links": ""}

    def surl(page_no):
        q = {"keyword": kw}
        if a.pan != "all":
            q["pan_type"] = a.pan
        if page_no > 1:
            q["page"] = page_no
        return f"{BASE}/search?{urlencode(q)}"

    def act(page):
        try:
            return _act(page)
        except Exception as e:      # surface page errors instead of silently returning 0 results
            state["fatal"] = state["fatal"] or (3, f"夸夸盘搜页面操作失败：{type(e).__name__}: {str(e).splitlines()[0][:160]}")
            return page

    def _act(page):
        ctx = page.context; page.set_default_timeout(15000)
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
        if sess.get("localStorage"):
            page.evaluate("d=>{for(const k in d) localStorage.setItem(k,d[k])}", sess["localStorage"])
        seen = set()
        for p in range(1, max(1, a.max_pages) + 1):
            pause()
            page.goto(surl(p), wait_until="domcontentloaded")
            try:
                page.wait_for_selector(".result-card .result-title, .empty-state, [class*=empty]", timeout=25000)
            except Exception:
                pass
            page.wait_for_timeout(2500)
            close_promo(page)
            if p == 1 and not logged_in(page):
                if not login(page, state):
                    return page
                save_session(ctx, page.evaluate("Object.fromEntries(Object.keys(localStorage).map(k=>[k,localStorage.getItem(k)]))"))
                pause(); page.goto(surl(p), wait_until="domcontentloaded")
                page.wait_for_selector(".result-card .result-title", timeout=25000); page.wait_for_timeout(2500); close_promo(page)
            if p == 1:
                m = re.search(r"找到\s*([\d,]+)\s*个相关资源", page.evaluate("document.body.innerText"))
                state["total"] = m.group(1) if m else ""
            cards = parse_cards(page)
            new = 0
            for c in cards:
                key = (c["title"], c["files"], c["date"], c["pan_type"])
                if key in seen:
                    continue
                seen.add(key); new += 1
                c.update({"page": p, "share_url": "", "extraction_code": "", "link_note": "", "scraped_at": ts})
                if state["links_tried"] < a.links and not state["stop_links"]:
                    state["links_tried"] += 1
                    link, note = get_link(page, c["dom_index"])
                    if link:
                        c["share_url"] = link
                        q = re.search(r"[?&]pwd=([A-Za-z0-9]+)", link)
                        c["extraction_code"] = q.group(1) if q else ""
                    else:
                        c["link_note"] = note
                        if note in ("额度用完", "登录失效"):
                            state["stop_links"] = note
                state["rows"].append(c)
            if not new:
                break
        state["quota"] = read_quota(page)
        save_session(ctx, page.evaluate("Object.fromEntries(Object.keys(localStorage).map(k=>[k,localStorage.getItem(k)]))"))
        return page

    with StealthySession(headless=True, google_search=False, network_idle=False, timeout=60000, blocked_domains=BLOCK) as s:
        s.fetch(f"{BASE}/robots.txt", page_action=act)   # small same-origin page to set localStorage, then navigate
    if state["fatal"]:
        code, msg = state["fatal"]; print(msg, file=sys.stderr); sys.exit(code)
    rows = state["rows"]
    for r in rows:
        r.pop("dom_index", None)
    base = os.path.join(a.out, f"kuakuaso_{kw}{'_' + a.pan if a.pan != 'all' else ''}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "pan": a.pan, "scraped_at": ts, "site_total": state["total"], "account": state["quota"],
                   "relogin": state["relogin"], "links_tried": state["links_tried"], "links_stopped": state["stop_links"] or None,
                   "results": rows}, f, ensure_ascii=False, indent=1)
    cols = ["title", "pan_type", "file_type", "share_url", "extraction_code", "date", "files", "more", "page", "link_note", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    got = sum(1 for r in rows if r["share_url"])
    qd = state["quota"]
    print(f"{len(rows)} results (site says 找到 {state['total']}); links {got}/{state['links_tried']} tried"
          f"{' - stopped: ' + state['stop_links'] if state['stop_links'] else ''}{' (re-logged in)' if state['relogin'] else ''} @ {ts}")
    print(f"account: {qd.get('level','?')} {qd.get('quota','?')}{' VIP到 ' + qd['vip_time'] if qd.get('vip_time') else ''} (site shows no separate remaining counter)")
    print(f"{base}.json\n{base}.csv")
    for r in rows:
        print(f"{r['pan_type']}\t{r['file_type']}\t{r['share_url'] or '-'}\t{r['date']}\t{r['title'][:40]}\t{r['files'][:50]}\t{r['link_note']}")

if __name__ == "__main__":
    main()
