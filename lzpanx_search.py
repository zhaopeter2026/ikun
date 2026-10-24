#!/workspace/scrapling-env/bin/python
"""Search 懒盘搜索 lzpanx.com (React SPA netdisk search) for a keyword, logged in with Peter's account (ninitaimei).

Access: real browser (Scrapling StealthySession / Playwright). Site APIs are signed by the page's own JS
  (x-sign / device_sig / fp_data) - this script NEVER calls the APIs itself; it only drives the page and reads
  the site's own responses via page.on('response'):
  - /search?q=<kw>&page=N  -> page fires POST /v1/search/disk -> data.list (15/page: disk_name, doc_id, disk_type,
    files, tags, share_user, shared_time, size; link empty) + data.total
  - click a result's TITLE button (button[class*=_searchItemTitle_]) -> page fires GET /v1/disk/doc/<doc_id>
    -> data.link / data.disk_pass / size / shared_time (only when logged in; guests get no link). Escape closes.
Login: session at ~/.secrets/lzpanx_session.json (cookies + localStorage; token = localStorage user-info
  state.userInfo.token, JWT exp). Restored via localStorage + reload; verified by username in header. If missing/
  expired or the site answers 405 请先登录 -> re-login once via /login form (email from ~/.secrets/lzpanx.env,
  password from env LZPANX_PASSWORD, never printed/logged) and re-save the session (chmod 600).
Polite: robots.txt Crawl-delay 20 -> >= 20 s between every page load and every click.
  So a run takes ~20 s x (2 + pages + links [+2 if re-login]).
Usage: LZPANX_PASSWORD=... lzpanx_search.py KEYWORD [--pages N (default 1)] [--links N (default 5; 0 = titles only)]
                                             [--out DIR] [--headful]
"""
import os, sys, re, csv, json, time, base64, datetime, argparse
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/workspace/scrapling-env/ms-playwright")
from scrapling.fetchers import StealthySession

BASE = "https://www.lzpanx.com"
PT = ZoneInfo("America/Los_Angeles")
SECRETS = os.path.expanduser("~/.secrets")
SESSION = os.path.join(SECRETS, "lzpanx_session.json")
ENVF = os.path.join(SECRETS, "lzpanx.env")
USERNAME = "ninitaimei"
DELAY = 20.0           # robots.txt Crawl-delay
PER_PAGE = 15
TYPE_CN = {"QUARK": "夸克网盘", "ALY": "阿里云盘", "ALIYUN": "阿里云盘", "BDY": "百度网盘", "BAIDU": "百度网盘",
           "XUNLEI": "迅雷云盘", "XL": "迅雷云盘", "UC": "UC网盘", "TIANYI": "天翼云盘", "TY": "天翼云盘",
           "YD": "移动云盘", "MOBILE": "移动云盘", "115": "115网盘", "123": "123云盘", "PIKPAK": "PikPak", "LZY": "蓝奏云"}
HOSTS = [("quark.cn", "夸克网盘"), ("aliyundrive.com", "阿里云盘"), ("alipan.com", "阿里云盘"), ("baidu.com", "百度网盘"),
         ("xunlei.com", "迅雷云盘"), ("uc.cn", "UC网盘"), ("139.com", "移动云盘"), ("189.cn", "天翼云盘"),
         ("123pan", "123云盘"), ("115", "115网盘"), ("lanzou", "蓝奏云"), ("mypikpak", "PikPak")]
TITLE_BTN = 'button[class*="_searchItemTitle_"]'

def now():
    return datetime.datetime.now(PT)

def ts(d=None):
    return (d or now()).strftime("%Y-%m-%d %H:%M:%S %Z")

def strip(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()

def pan_cn(disk_type, link=""):
    if link:
        host = urlparse(link).netloc.lower()
        for d, n in HOSTS:
            if d in host:
                return n
    return TYPE_CN.get((disk_type or "").upper(), disk_type or "")

def human(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024 or u == "TB":
            return f"{n:.2f} {u}" if u != "B" else f"{int(n)} B"
        n /= 1024

def pt_date(s):
    """'2022-03-16T22:42:43Z' or '2025-06-20 00:33:52' (site local) -> 'YYYY-MM-DD HH:MM' (PT for Z times)"""
    if not s:
        return ""
    try:
        if s.endswith("Z"):
            return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(PT).strftime("%Y-%m-%d %H:%M")
        return s[:16]
    except ValueError:
        return s

def norm(u):
    p = urlparse(u.split("#")[0])
    return f"{p.netloc.lower()}{p.path.rstrip('/')}"

class Lz:
    def __init__(self, a):
        self.a = a
        self._last = 0.0
        self.caps = []              # (kind, url, status, json-or-None)
        self.pw = os.environ.get("LZPANX_PASSWORD", "")
        self.relogged = False
        self.session = None
        self.search_pages, self.docs, self.notes = [], {}, []

    # ---- helpers -------------------------------------------------------------
    def pace(self):
        w = DELAY - (time.time() - self._last)
        if self._last and w > 0:
            time.sleep(w)
        self._last = time.time()

    def onresp(self, r):
        u = r.url
        if "lzpanx.com" not in u or "/v1/" not in u:
            return
        kind = "search" if "/v1/search/disk" in u else "doc" if "/v1/disk/doc/" in u else "other"
        try:
            body = r.text()
            j = json.loads(body)
        except Exception:
            j = None
        if j is not None and kind == "other" and not (isinstance(j, dict) and j.get("code") == 405):
            j = None                                       # don't keep unrelated bodies
        self.caps.append((kind, u.split("?")[0], r.status, j))

    def need_login(self, since):
        for kind, u, st, j in self.caps[since:]:
            if st == 405 or (isinstance(j, dict) and (j.get("code") == 405 or "请先登录" in str(j.get("msg", "")))):
                return True
        return False

    def close_popup(self, page):
        try:
            page.evaluate("""(()=>{for(const d of document.querySelectorAll('[role=dialog]')){
                if(d.innerText.includes('网盘拉新')){const b=d.querySelector('button[aria-label]')||d.querySelector('button');if(b)b.click();}}})()""")
            page.wait_for_timeout(600)
        except Exception:
            pass

    def logged_in(self, page):
        try:
            page.wait_for_function(f"document.body && document.body.innerText.includes('{USERNAME}')", timeout=10000)
            return True
        except Exception:
            return False

    def token_exp(self):
        try:
            s = json.load(open(SESSION))
            tok = json.loads(s["localStorage"]["user-info"])["state"]["userInfo"]["token"]
            pl = json.loads(base64.urlsafe_b64decode(tok.split(".")[1] + "=="))
            return datetime.datetime.fromtimestamp(pl["exp"], PT), s
        except Exception:
            return None, None

    def save_session(self, page):
        ls = page.evaluate("Object.fromEntries(Object.entries(localStorage))")
        ss = page.evaluate("Object.fromEntries(Object.entries(sessionStorage))")
        data = {"saved_at": ts(), "origin": BASE, "cookies": page.context.cookies(), "localStorage": ls, "sessionStorage": ss}
        os.makedirs(SECRETS, exist_ok=True)
        fd = os.open(SESSION, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.chmod(SESSION, 0o600)
        exp, _ = self.token_exp()
        print(f"[登录] 已保存新的登录状态 {SESSION}（有效期到 {ts(exp) if exp else '未知'}）", file=sys.stderr)

    def login(self, page):
        if self.relogged:
            raise SystemExit("[错误] 已重新登录过一次仍然不行，停止。")
        self.relogged = True
        if not self.pw:
            raise SystemExit("[错误] 登录状态失效，需要重新登录，但环境变量 LZPANX_PASSWORD 没有设置。")
        email = ""
        for line in open(ENVF, encoding="utf-8"):
            if line.startswith("LZPANX_EMAIL="):
                email = line.split("=", 1)[1].strip()
        if not email:
            raise SystemExit(f"[错误] {ENVF} 里没有 LZPANX_EMAIL")
        print("[登录] 登录状态失效，正在用账号重新登录…", file=sys.stderr)
        self.pace(); page.goto(BASE + "/login"); page.wait_for_timeout(6000)
        self.close_popup(page)
        page.fill("input[name=username]", email)          # 账号或邮箱
        page.fill("input[name=password]", self.pw)        # 密码 (never printed)
        page.click("button[type=submit]")                 # 登录 -> POST /v1/user/login
        page.wait_for_timeout(8000)
        if "/login" in page.url and not self.logged_in(page):
            raise SystemExit("[错误] 重新登录失败（还停在登录页），请人工检查账号/密码。")
        self.save_session(page)
        self.pace(); page.goto(BASE + "/"); page.wait_for_timeout(6000); self.close_popup(page)
        if not self.logged_in(page):
            raise SystemExit("[错误] 重新登录后页面上仍然没有用户名，停止。")

    def ensure_login(self, page):
        exp, s = self.token_exp()
        if s:
            page.evaluate("d=>{for(const[k,v] of Object.entries(d))localStorage.setItem(k,v)}", s.get("localStorage", {}))
            self.pace(); page.reload(); page.wait_for_timeout(7000); self.close_popup(page)
        if s and exp and exp > now() and self.logged_in(page):
            print(f"[登录] 已用保存的登录状态登录为 {USERNAME}（有效期到 {ts(exp)}）", file=sys.stderr)
        else:
            self.login(page)
            exp, _ = self.token_exp()
        if exp and exp - now() < datetime.timedelta(days=2):
            print(f"[警告] 懒盘搜索登录状态将在 {ts(exp)} 过期（不到 2 天），过期后脚本会自动重新登录（需要 LZPANX_PASSWORD）。",
                  file=sys.stderr)

    # ---- actions -------------------------------------------------------------
    def load_search(self, page, kw, pn):
        url = f"{BASE}/search?q={quote(kw)}" + (f"&page={pn}" if pn > 1 else "")
        for attempt in (1, 2):
            n0 = len(self.caps)
            self.pace(); page.goto(url); page.wait_for_timeout(9000); self.close_popup(page)
            if self.need_login(n0) or not self.logged_in(page):
                if attempt == 1:
                    self.login(page); continue
            sr = [c for c in self.caps[n0:] if c[0] == "search" and isinstance(c[3], dict)]
            if not sr:
                page.wait_for_timeout(6000)
                sr = [c for c in self.caps[n0:] if c[0] == "search" and isinstance(c[3], dict)]
            if not sr:
                return None
            return sr[-1][3].get("data") or {}
        return None

    def open_doc(self, page, idx, doc_id):
        for attempt in (1, 2):
            n0 = len(self.caps)
            self.pace(); self.close_popup(page)
            btn = page.locator(TITLE_BTN).nth(idx)
            try:
                btn.scroll_into_view_if_needed(timeout=5000); btn.click(timeout=5000)
            except Exception as e:
                self.notes.append(f"点击第 {idx+1} 条标题失败: {str(e)[:80]}"); return None
            page.wait_for_timeout(6000)
            docs = [c for c in self.caps[n0:] if c[0] == "doc" and isinstance(c[3], dict)]
            page.keyboard.press("Escape"); page.wait_for_timeout(800)
            if "/search" not in page.url:
                page.go_back(); page.wait_for_timeout(5000); self.close_popup(page)
            if self.need_login(n0):
                if attempt == 1:
                    self.login(page)
                    # back to the search page so the list/buttons exist again
                    self.pace(); page.goto(self._cur_url); page.wait_for_timeout(9000); self.close_popup(page)
                    continue
                return None
            for _, u, st, j in docs:
                d = j.get("data") or {}
                if d.get("doc_id"):
                    self.docs[d["doc_id"]] = d
            hit = [j["data"] for _, u, st, j in docs if (j.get("data") or {}).get("doc_id") == doc_id]
            if not hit and docs:
                self.notes.append(f"第 {idx+1} 条点开的详情 doc_id 不一致（期望 {doc_id}）")
            return hit[-1] if hit else None
        return None

    def run(self, page):
        a, kw = self.a, self.a.keyword
        page.on("response", self.onresp)
        self._last = time.time()                           # the initial home load counts as a request
        page.wait_for_timeout(5000)
        self.ensure_login(page)
        budget = a.links
        rows, seen_doc = [], set()
        self.total = None
        for pn in range(1, a.pages + 1):
            self._cur_url = f"{BASE}/search?q={quote(kw)}" + (f"&page={pn}" if pn > 1 else "")
            data = self.load_search(page, kw, pn)
            if data is None:
                self.stop = f"第 {pn} 页没有拿到搜索结果"; break
            lst = data.get("list") or []
            self.total = data.get("total", self.total)
            self.search_pages.append(pn)
            if not lst:
                self.stop = "没有更多结果"; break
            nbtn = page.locator(TITLE_BTN).count()
            for i, it in enumerate(lst):
                did = it.get("doc_id", "")
                if did in seen_doc:
                    continue
                seen_doc.add(did)
                r = {"title": strip(it.get("disk_name")), "pan_type": pan_cn(it.get("disk_type")),
                     "disk_type": it.get("disk_type", ""), "share_url": "", "extraction_code": "",
                     "size": human(it.get("size")), "shared_time": pt_date(it.get("shared_time")),
                     "tags": "、".join(it.get("tags") or []), "share_user": it.get("share_user", ""),
                     "files": strip((it.get("files") or "").replace("file:", "").replace("folder:", "").replace("\n", " | "))[:300],
                     "detail_url": f"{BASE}/doc/{did}", "doc_id": did, "page": pn, "opened": False}
                if budget > 0 and i < nbtn:
                    budget -= 1
                    d = self.open_doc(page, i, did)
                    if d:
                        r["opened"] = True
                        r["share_url"] = d.get("link") or ""
                        r["extraction_code"] = d.get("disk_pass") or ""
                        r["pan_type"] = pan_cn(d.get("disk_type") or it.get("disk_type"), r["share_url"])
                        if d.get("size"): r["size"] = human(d["size"])
                        if d.get("shared_time"): r["shared_time"] = pt_date(d["shared_time"])
                        if not r["share_url"]:
                            self.notes.append(f"「{r['title'][:30]}」详情里没有链接（可能未登录或资源已失效）")
                rows.append(r)
            if len(lst) < PER_PAGE:
                self.stop = "最后一页（不足 15 条）"; break
        else:
            self.stop = "已到 --pages 上限"
        # dedupe identical share links (keep first)
        out, seen_link = [], set()
        for r in rows:
            if r["share_url"]:
                k = norm(r["share_url"])
                if k in seen_link:
                    continue
                seen_link.add(k)
            out.append(r)
        self.rows = out

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--links", type=int, default=5); ap.add_argument("--out", default="/workspace/scrapes/out")
    ap.add_argument("--headful", action="store_true")
    a = ap.parse_args()
    a.keyword = a.keyword.strip(); a.pages = max(1, a.pages); a.links = max(0, a.links)
    os.makedirs(a.out, exist_ok=True)
    steps = 2 + a.pages + min(a.links, a.pages * PER_PAGE)
    print(f"懒盘搜索「{a.keyword}」: {a.pages} 页，点开 {min(a.links, a.pages*PER_PAGE)} 条拿链接。"
          f"网站要求每次请求隔 20 秒，预计约 {steps * 20 // 60} 分 {steps * 20 % 60} 秒（{steps} 步 × 20 秒；如需重新登录再加约 40 秒）。",
          flush=True)
    t0 = time.time(); started = ts()
    lz = Lz(a); lz.stop = ""; lz.rows = []
    with StealthySession(headless=not a.headful) as s:
        s.fetch(BASE + "/", page_action=lz.run, timeout=max(600000, (steps + 6) * 30000))
    base = os.path.join(a.out, f"lzpanx_{a.keyword}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": a.keyword, "scraped_at": started, "site_total": lz.total, "pages_fetched": lz.search_pages,
                   "links_requested": a.links, "links_opened": sum(r["opened"] for r in lz.rows),
                   "stop_reason": lz.stop, "relogged": lz.relogged, "notes": lz.notes, "results": lz.rows},
                  f, ensure_ascii=False, indent=1)
    cols = ["title", "pan_type", "share_url", "extraction_code", "size", "shared_time", "tags", "share_user", "files",
            "detail_url", "doc_id", "disk_type", "page", "opened"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(lz.rows)
    by, links = {}, [r for r in lz.rows if r["share_url"]]
    for r in lz.rows:
        by[r["pan_type"]] = by.get(r["pan_type"], 0) + 1
    print(f"懒盘搜索「{a.keyword}」: 网站共 {lz.total} 条；抓了 {len(lz.search_pages)} 页、{len(lz.rows)} 条不重复结果，"
          f"其中 {len(links)} 条拿到了链接 @ {started}，用时 {int(time.time()-t0)} 秒")
    print(f"停止原因: {lz.stop}{'；中途重新登录过' if lz.relogged else ''}\n{base}.json\n{base}.csv\n按网盘: {by}")
    for n in lz.notes:
        print("[提示]", n)
    if not lz.rows:
        print("（没有结果）")
    for r in lz.rows:
        print(f"{r['pan_type']}\t{(r['share_url'] or '（未点开）')[:60]}\t码:{r['extraction_code'] or '-'}\t{r['size']}\t{r['shared_time'][:10]}\t{r['title'][:50]}")

if __name__ == "__main__":
    main()
