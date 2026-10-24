"""Shared helpers for 爱盘搜 aipanso.com scripts (aipanso_activate.py, aipanso_search.py).

How the site gates links (read from the detail page's own Vue template, 2026-10-22):
  /s/<id> renders `had_follow: true|false` server-side from the `_egg` cookie (~7 days).
  Button 「同意声明,继续访问下载」 -> onDownload(): if had_follow, window.open('/cv/<id>', 'target');
  else opens a van-dialog (QR image /img/maoli.jpg, "微信关注发送「动态码」获取动态码", field #activeCode
  placeholder 请输入动态码). Its 确认 button runs beforeClose(): $.post('/active', {code}) -> body '1' = ok
  (then opens /cv/<id>), anything else -> alert '动态码失效,请重新从公众号获取'; empty -> '动态码不能为空'.
  /cv/<id> answers 302 Location: <real share URL>.
Everything is driven through the site's own UI in a headless browser (Cloudflare passes on its own).
Note: patchright evaluates in an isolated world by default; Vue state needs isolated_context=False.
"""
import os, json, re, time, datetime
from zoneinfo import ZoneInfo
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/workspace/scrapling-env/ms-playwright")

BASE = "https://aipanso.com"
COOKIES = os.path.expanduser(os.environ.get("AIPANSO_COOKIES", "~/.secrets/aipanso_cookies.json"))
PT = ZoneInfo("America/Los_Angeles")
NEED_CODE = "爱盘搜需要新动态码，请 Peter 从公众号拿一个（1分钟内有效）"
BTN = "button:has-text('同意声明')"
ACTIVATION_COOKIE = "_egg"

def load_cookies():
    try:
        return [c for c in json.load(open(COOKIES)) if "aipanso" in c.get("domain", "")]
    except FileNotFoundError:
        return []

def save_cookies(cookies):
    os.makedirs(os.path.dirname(COOKIES), exist_ok=True)
    tmp = COOKIES + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600); os.replace(tmp, COOKIES); os.chmod(COOKIES, 0o600)

def fmt_exp(ts):
    if not ts or ts < 0:
        return "session"
    return datetime.datetime.fromtimestamp(ts, PT).strftime("%Y-%m-%d %H:%M %Z")

def activation_expiry(cookies):
    """Latest expiry of the _egg activation cookie, or None."""
    exps = [c.get("expires") or -1 for c in cookies if c.get("name") == ACTIVATION_COOKIE and "aipanso" in c.get("domain", "")]
    return max(exps) if exps else None

def had_follow(page):
    return page.evaluate("typeof app!=='undefined' && app && app._isVue ? !!app.had_follow : null", isolated_context=False)

def click_through(page, timeout=20000):
    """On an activated detail page: click the site's button, return (real_url, cv_status). Closes the popup."""
    ctx = page.context
    hits = []
    def onresp(r):
        if "/cv/" in r.url:
            hits.append((r.status, r.headers.get("location") or ""))
    ctx.on("response", onresp)
    try:
        with ctx.expect_page(timeout=timeout) as pi:
            page.locator(BTN).first.click()
        pop = pi.value
        for _ in range(int(timeout / 250)):
            if hits and hits[-1][1]:
                break
            if not pop.url.startswith(("about:", BASE)):
                break
            pop.wait_for_timeout(250)
        url = (hits[-1][1] if hits and hits[-1][1] else pop.url).split("#")[0]
        status = hits[-1][0] if hits else None
        try:
            pop.close()
        except Exception:
            pass
        return url, status
    finally:
        ctx.remove_listener("response", onresp)
