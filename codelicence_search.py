#!/workspace/scrapling-env/bin/python
"""Search 云盘4K codelicence.cn (Spring Boot / server-rendered netdisk search site) for a keyword.

Access: real headless browser (Scrapling StealthySession / Playwright). The home-page form POSTs /search with
  `keyboard` + `encryptedKeyboard`; encryptedKeyboard is computed by the site's own obfuscated /js/search.js.
  This script NEVER computes/forges it: it types the keyword into the real box (#search-word) and presses Enter,
  so the page's JS submits. Server answers 302 -> /<18-digit id> (results page, 25 per page, page 1 only).
  Detail pages /d/<id>: 来源 (netdisk), 时间, 用户, 提取码 (if any), and a button to /go/<id> (redirects to the real
  link). Checked 2026-10-22: the detail page does NOT show the real share link (only the /go/ button), so share_url
  stays empty; open detail_url and click the button yourself. (A pan.quark.cn link lower on every detail page is a
  site ad and is ignored.)
robots.txt (checked 2026-10-22): Disallow /go/ and /*?*  -> page 1 only (no ?p=N), /go/ is never requested
  (also blocked in-browser via route abort). The share URL is only recorded if it is shown on the detail page
  itself; otherwise share_url is empty and you click the button on the detail page (detail_url) yourself.
Gate 「操作频繁验证」 (title 操作频繁验证 - 云盘4K, input #verificationCode): code comes from WeChat 公众号「盘盘订阅」
  (reply 验证码). Without --code the script exits with code 2. With --code XXXX it fills #verificationCode and
  clicks 验证 (page's own validateCode() -> /api/visit/checkCode) in the same session, then continues.
  Observed 2026-10-22: the site sets NO cookies (fresh browser and plain HTTP both gated, no Set-Cookie), so the
  verification looks IP-based; this box's shell egress IP rotates, so a code may not "stick" between runs.
  Cookies (if any) are still saved to ~/.secrets/codelicence_cookies.json (chmod 600, values never printed)
  and loaded on the next run.
Proxy (added 2026-10-22): the gate pass is per exit IP. The box desktop Chrome runs with
  --proxy-server=http://127.0.0.1:8791 (the box egress tunnel, env SAND_EGRESS_TUNNEL_PROXY_ADDR) and exits from a
  stable IP (38.64.60.48), while plain shell/headless traffic exits from rotating IPs. So by default this script uses
  the SAME proxy as the desktop browser, so a verification done once in the desktop browser also covers the script.
  Order: --proxy URL  >  env CODELICENCE_PROXY  >  http://$SAND_EGRESS_TUNNEL_PROXY_ADDR (if set)  >  no proxy.
  --proxy none (or CODELICENCE_PROXY=none) disables it. The exit IP is printed at start (checked via api.ipify.org).
Polite: 1.5-2.5 s random delay between page loads/clicks; 1 search + --details N detail pages per run.
Usage: codelicence_search.py KEYWORD [--details N (default 5, max 25; 0 = list only)] [--code XXXX]
                                     [--out DIR] [--dump] [--headful] [--proxy URL|none]
Exit codes: 0 ok, 2 gate needs a code (or code rejected), 1 other error.
"""
import os, sys, re, csv, json, time, random, datetime, argparse
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/workspace/scrapling-env/ms-playwright")
from scrapling.fetchers import StealthySession

BASE = "https://www.codelicence.cn"
PT = ZoneInfo("America/Los_Angeles")
SECRETS = os.path.expanduser("~/.secrets")
COOKIES = os.path.join(SECRETS, "codelicence_cookies.json")
PER_PAGE = 25
GATE_TITLE = "操作频繁验证"
HOSTS = [("quark.cn", "夸克网盘"), ("aliyundrive.com", "阿里云盘"), ("alipan.com", "阿里云盘"), ("pan.baidu.com", "百度网盘"),
         ("xunlei.com", "迅雷云盘"), ("drive.uc.cn", "UC网盘"), ("fast.uc.cn", "UC网盘"), ("139.com", "移动云盘"),
         ("189.cn", "天翼云盘"), ("123pan", "123云盘"), ("115", "115网盘"), ("lanzou", "蓝奏云"), ("mypikpak", "PikPak")]
# netdisk names as they appear in icons (src/alt) or text
PAN_WORDS = [("夸克", "夸克网盘"), ("quark", "夸克网盘"), ("kuake", "夸克网盘"), ("阿里", "阿里云盘"), ("aliyun", "阿里云盘"),
             ("alipan", "阿里云盘"), ("ali", "阿里云盘"), ("迅雷", "迅雷云盘"), ("xunlei", "迅雷云盘"), ("thunder", "迅雷云盘"),
             ("百度", "百度网盘"), ("baidu", "百度网盘"), ("bdy", "百度网盘"), ("uc", "UC网盘")]
PAN_URL_RE = r"https?://(?:pan\.quark\.cn|www\.aliyundrive\.com|www\.alipan\.com|pan\.baidu\.com|pan\.xunlei\.com|drive\.uc\.cn|fast\.uc\.cn)/[^\s\"'<>]+"
DATE_RE = r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"

# Real markup (checked 2026-10-22 via --dump):
#  results: .list li.r[data_type][data_size=" | 用户 | YYYY-MM-DD"] > a.l[href=/d/<id>] (title, <em> highlights), span.badge-secondary 热门
#           data_type: 1 阿里, 2 百度, 3 迅雷, 4 夸克, 5 UC (same as the ?t= filter tabs)
#  detail:  .showbox > h1, dl dd 来源：/时间：/用户：, .down a[href=/go/<n>] (button), div 提取码：XXXX (only if any)
#           The real share link is NOT on the page (only behind /go/). The pan.quark.cn link further down
#           (".list > a" "1000部最新影视资源…") is a site-wide ad and must be ignored.
TYPE_PAN = {"1": "阿里云盘", "2": "百度网盘", "3": "迅雷云盘", "4": "夸克网盘", "5": "UC网盘"}

JS_LIST = r"""() => [...document.querySelectorAll('.list li.r')].map(li => {
  const a = li.querySelector('a.l[href*="/d/"]') || li.querySelector('a[href*="/d/"]');
  const id = a ? ((a.getAttribute('href') || '').match(/\/d\/(\d+)/) || [])[1] : '';
  const b = li.querySelector('.badge-secondary');
  return {id, title: a ? a.innerText.trim() : '', type: li.getAttribute('data_type') || '',
          size: li.getAttribute('data_size') || (li.querySelector('.size') || {}).innerText || '',
          badge: b ? b.innerText.trim() : ''};
}).filter(x => x.id)"""

JS_DETAIL = r"""() => {
  const box = document.querySelector('.showbox');
  if (!box) return null;
  const h = box.querySelector('h1');
  return {h: h ? h.innerText.trim() : '', text: box.innerText,
          anchors: [...box.querySelectorAll('a[href]')].map(a => [a.href, (a.innerText || '').trim()])};
}"""

def now():
    return datetime.datetime.now(PT)

def ts(d=None):
    return (d or now()).strftime("%Y-%m-%d %H:%M:%S %Z")

def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()

def pan_of_url(u):
    host = urlparse(u).netloc.lower()
    for d, n in HOSTS:
        if d in host:
            return n
    return ""

def pan_of_words(*parts):
    s = " ".join(p for p in parts if p).lower()
    for w, n in PAN_WORDS:
        if w == "uc":
            if re.search(r"(?<![a-z])uc(?![a-z])", s):
                return n
        elif w == "ali":
            if re.search(r"(?<![a-z])ali(?:pan|yun)?(?![a-z])", s):
                return n
        elif w in s:
            return n
    return ""

class Site:
    def __init__(self, a):
        self.a = a
        self._last = 0.0
        self.notes, self.rows, self.total_text = [], [], ""
        self.stop, self.result_url, self.gate_hits, self.code_used = "", "", 0, False
        self.exit_code, self.exit_msg = 0, ""
        self.checkcode = []          # (status, ok)

    def pace(self):
        w = random.uniform(1.5, 2.5) - (time.time() - self._last)
        if self._last and w > 0:
            time.sleep(w)
        self._last = time.time()

    def gated(self, page):
        try:
            return GATE_TITLE in (page.title() or "") or page.locator("#verificationCode").count() > 0
        except Exception:
            return False

    def onresp(self, r):
        if "/api/visit/checkCode" in r.url:
            ok = None
            try:
                j = json.loads(r.text()); ok = bool(j.get("data"))
            except Exception:
                pass
            self.checkcode.append((r.status, ok))

    # ---- cookies -------------------------------------------------------------
    def load_cookies(self, page):
        if not os.path.exists(COOKIES):
            return 0
        try:
            cs = json.load(open(COOKIES)).get("cookies") or []
            if cs:
                page.context.add_cookies(cs)
            return len(cs)
        except Exception as e:
            self.notes.append(f"读取 cookies 失败: {type(e).__name__}")
            return 0

    def save_cookies(self, page):
        try:
            cs = [c for c in page.context.cookies() if "codelicence" in c.get("domain", "")]
        except Exception:
            return
        os.makedirs(SECRETS, exist_ok=True)
        fd = os.open(COOKIES, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"saved_at": ts(), "origin": BASE, "cookies": cs}, f, ensure_ascii=False, indent=1)
        os.chmod(COOKIES, 0o600)
        print(f"[cookies] 已保存 {len(cs)} 个 cookie 到 {COOKIES}（名字: {', '.join(sorted({c['name'] for c in cs})) or '无'}）",
              file=sys.stderr)

    # ---- gate ----------------------------------------------------------------
    def pass_gate(self, page, where):
        """True if page is (now) not gated. Uses --code once."""
        if not self.gated(page):
            return True
        self.gate_hits += 1
        if not self.a.code or self.code_used:
            self.exit_code = 2
            self.exit_msg = (f"云盘4K 显示「操作频繁验证」（在{where}）。请微信关注公众号「盘盘订阅」并回复“验证码”拿到验证码，"
                             f"然后重新运行: codelicence_search.py {self.a.keyword} --code 验证码"
                             if not self.code_used else
                             f"用过一次验证码后，在{where}又出现了「操作频繁验证」。请拿新的验证码再运行一次（--code 新验证码）。")
            return False
        self.code_used = True
        print(f"[验证] {where}出现「操作频繁验证」，正在填写验证码…", file=sys.stderr)
        self.pace()
        page.fill("#verificationCode", self.a.code)
        n0 = len(self.checkcode)
        page.click("button[onclick*='validateCode']")    # the page's own 验证 button
        page.wait_for_timeout(6000)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        res = self.checkcode[n0:]
        if res and res[-1][1] is False:
            self.exit_code = 2
            self.exit_msg = "验证码不对（网站回复 验证码错误）。请从公众号「盘盘订阅」拿新的验证码再运行。"
            return False
        if self.gated(page):
            self.exit_code = 2
            self.exit_msg = "填了验证码，但页面仍然是「操作频繁验证」。请拿新的验证码再运行。"
            return False
        print("[验证] 验证通过。", file=sys.stderr)
        return True

    # ---- parsing ---------------------------------------------------------------
    def parse_list(self, page):
        items = page.evaluate(JS_LIST)
        rows = []
        for it in items[:PER_PAGE]:
            parts = [clean(x) for x in it["size"].split("|")]
            parts = [x for x in parts if x]
            date = next((x for x in parts if re.fullmatch(DATE_RE, x)), "")
            user = next((x for x in parts if x != date and x != "-"), "")
            rows.append({"title": clean(it["title"]), "pan_type": TYPE_PAN.get(it["type"], ""),
                         "share_url": "", "extraction_code": "", "date": date, "uploader": user,
                         "info": " | ".join(x for x in [it["badge"], f"data_type={it['type']}"] if x),
                         "detail_url": f"{BASE}/d/{it['id']}", "site_id": it["id"],
                         "detail_opened": False, "detail_source": "", "detail_time": "", "detail_user": "", "go_url": ""})
        self.total_text = f"第 1 页 {len(rows)} 条" if rows else ""
        return rows

    def parse_detail(self, page, r):
        d = page.evaluate(JS_DETAIL)
        if not d:
            self.notes.append(f"详情页没有 .showbox（可能改版）: {r['detail_url']}"); return
        t = d["text"]
        def field(name, pat=r"([^\n]+)"):
            m = re.search(r"%s\s*[:：]\s*%s" % (name, pat), t)
            return clean(m.group(1)) if m else ""
        src = field("来源")
        r["detail_source"] = src
        r["detail_time"] = field("时间")
        u = field("用户"); r["detail_user"] = "" if u == "-" else u
        r["extraction_code"] = field("提取码", r"([A-Za-z0-9]{3,8})")
        r["go_url"] = next((h for h, _ in d["anchors"] if "/go/" in urlparse(h).path), "")
        links = [h for h, _ in d["anchors"] if pan_of_url(h) and "codelicence.cn" not in urlparse(h).netloc]
        links += [u for u in re.findall(PAN_URL_RE, t) if u not in links]   # only inside .showbox (ads excluded)
        if links:
            r["share_url"] = links[0]
            if not r["extraction_code"]:
                m = re.search(r"[?&]pwd=([A-Za-z0-9]{3,8})", links[0]); r["extraction_code"] = m.group(1) if m else ""
        r["pan_type"] = pan_of_url(r["share_url"]) or pan_of_words(src) or r["pan_type"]
        if d["h"]:
            r["title"] = clean(d["h"])
        if not r["uploader"] and r["detail_user"]:
            r["uploader"] = r["detail_user"]
        if not r["date"] and r["detail_time"]:
            r["date"] = r["detail_time"]
        r["detail_opened"] = True

    def dump(self, page, name):
        if self.a.dump:
            p = os.path.join(self.a.out, f"codelicence_debug_{name}.html")
            open(p, "w", encoding="utf-8").write(page.content())

    # ---- main flow -------------------------------------------------------------
    def run(self, page):
        a = self.a
        page.on("response", self.onresp)
        page.route(re.compile(r"codelicence\.cn/go/"), lambda route: route.abort())   # robots: never hit /go/
        self._last = time.time()                     # the initial home load counts as a request
        nck = self.load_cookies(page)
        if nck:
            print(f"[cookies] 已载入上次保存的 {nck} 个 cookie", file=sys.stderr)
        try:
            page.wait_for_selector("#search-word", timeout=20000)
        except Exception:
            if not self.pass_gate(page, "首页"):
                return
            self.pace(); page.goto(BASE + "/"); page.wait_for_selector("#search-word", timeout=20000)
        page.wait_for_timeout(1500)                  # let /js/search.js attach its submit handler
        self.pace()
        page.click("#search-word")
        page.keyboard.type(a.keyword, delay=random.randint(90, 160))
        page.wait_for_timeout(600)
        page.keyboard.press("Enter")                 # the page's own JS adds encryptedKeyboard and submits
        try:
            page.wait_for_url(re.compile(r"codelicence\.cn/\d{15,}"), timeout=25000)
        except Exception:
            pass
        page.wait_for_timeout(2500)
        if not re.search(r"/\d{15,}", urlparse(page.url).path) and not self.gated(page):
            self.stop = f"提交搜索后没有跳到结果页（停在 {page.url}）"; return
        self.result_url = page.url if re.search(r"/\d{15,}", urlparse(page.url).path) else ""
        if not self.pass_gate(page, "搜索结果页"):
            self.save_cookies(page); return
        if re.search(r"/\d{15,}", urlparse(page.url).path):
            self.result_url = page.url.split("?")[0]
        elif self.result_url:                        # validateCode() reloaded somewhere else -> back to results
            self.pace(); page.goto(self.result_url); page.wait_for_timeout(2000)
            if not self.pass_gate(page, "搜索结果页"):
                self.save_cookies(page); return
        self.dump(page, "results")
        self.rows = self.parse_list(page)
        if not self.rows:
            body = page.evaluate("document.body ? document.body.innerText : ''")
            self.stop = "没有结果" if re.search(r"没有|暂无|未找到", body) else "结果页解析不到条目（网站可能改版，可用 --dump 保存页面）"
            self.save_cookies(page); return
        self.stop = "只抓第 1 页（robots.txt 禁止 ?p=N 翻页）"
        for i, r in enumerate(self.rows[:a.details]):
            self.pace()
            try:
                page.goto(r["detail_url"]); page.wait_for_timeout(1500)
            except Exception as e:
                self.notes.append(f"打开详情 {r['detail_url']} 失败: {str(e)[:80]}"); continue
            if not self.pass_gate(page, f"第 {i+1} 个详情页"):
                self.notes.append(f"第 {i+1} 个详情页起被「操作频繁验证」挡住，后面的详情没抓")
                if self.exit_code == 2 and i > 0:     # keep what we have, but still report the gate
                    pass
                break
            if "/d/" not in page.url:                # code flow may redirect; go back to the detail page
                self.pace(); page.goto(r["detail_url"]); page.wait_for_timeout(1500)
            self.dump(page, f"detail_{i+1}")
            self.parse_detail(page, r)
            if not r["share_url"]:
                self.notes.append(f"「{r['title'][:30]}」详情页上没有直接显示网盘链接，要在详情页点按钮打开: {r['detail_url']}")
        self.save_cookies(page)

def pick_proxy(cli):
    v = (cli or os.environ.get("CODELICENCE_PROXY") or "").strip()
    if not v:
        addr = os.environ.get("SAND_EGRESS_TUNNEL_PROXY_ADDR", "").strip()   # what the desktop Chrome uses
        v = f"http://{addr}" if addr else ""
    if v.lower() in ("none", "off", "direct", "0"):
        return ""
    return v if "://" in v else "http://" + v

def exit_ip(proxy):
    import urllib.request
    try:
        h = urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
        return urllib.request.build_opener(h).open("https://api.ipify.org", timeout=15).read().decode().strip()
    except Exception as e:
        return f"?({type(e).__name__})"

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--details", type=int, default=5)
    ap.add_argument("--code", default=""); ap.add_argument("--out", default="/workspace/scrapes/out")
    ap.add_argument("--dump", action="store_true", help="save raw HTML of the pages to OUT for debugging")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--proxy", default="", help="proxy URL, or 'none'; default: $CODELICENCE_PROXY, else the desktop browser's proxy")
    a = ap.parse_args()
    a.keyword = a.keyword.strip(); a.details = max(0, min(a.details, PER_PAGE)); a.code = a.code.strip()
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time(); started = ts()
    print(f"云盘4K「{a.keyword}」: 抓第 1 页（最多 {PER_PAGE} 条），打开前 {a.details} 个详情页。", flush=True)
    proxy = pick_proxy(a.proxy)
    print(f"代理: {proxy or '不用代理'}，出口 IP: {exit_ip(proxy)}", flush=True)
    st = Site(a)
    kw = {"proxy": proxy} if proxy else {}
    with StealthySession(headless=not a.headful, **kw) as s:
        s.fetch(BASE + "/", page_action=st.run, timeout=max(180000, (a.details + 6) * 30000))
    if st.exit_code == 2 and not st.rows:
        print(f"[需要验证码] {st.exit_msg}", file=sys.stderr)
        sys.exit(2)
    base = os.path.join(a.out, f"codelicence_{a.keyword}")
    opened = sum(r["detail_opened"] for r in st.rows)
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": a.keyword, "scraped_at": started, "results_url": st.result_url, "site_total_text": st.total_text,
                   "details_requested": a.details, "details_opened": opened, "gate_hits": st.gate_hits,
                   "code_used": st.code_used, "stop_reason": st.stop, "notes": st.notes, "results": st.rows},
                  f, ensure_ascii=False, indent=1)
    cols = ["title", "pan_type", "share_url", "extraction_code", "date", "uploader", "detail_url", "detail_source",
            "detail_time", "detail_user", "detail_opened", "go_url", "site_id", "info"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in st.rows:
            w.writerow({**r, "scraped_at": started})
    by = {}
    for r in st.rows:
        by[r["pan_type"] or "未知"] = by.get(r["pan_type"] or "未知", 0) + 1
    print(f"云盘4K「{a.keyword}」: {len(st.rows)} 条结果（{st.total_text or '网站未显示总数'}），打开了 {opened} 个详情页 @ {started}，"
          f"用时 {int(time.time()-t0)} 秒")
    print(f"结果页: {st.result_url}\n停止原因: {st.stop}\n{base}.json\n{base}.csv\n按网盘: {by}")
    for n in st.notes:
        print("[提示]", n)
    for r in st.rows:
        print(f"{r['pan_type'] or '-'}\t{r['date'][:10] or '-'}\t{(r['share_url'] or r['detail_url'])[:60]}\t"
              f"码:{r['extraction_code'] or '-'}\t{r['uploader'][:12] or '-'}\t{r['title'][:50]}")
    if st.exit_code == 2:
        print(f"[需要验证码] {st.exit_msg}", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
