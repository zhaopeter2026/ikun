#!/workspace/scrapling-env/bin/python
"""Activate 爱盘搜 aipanso.com with a WeChat 动态码 (valid ~1 minute) through the site's own dialog, then save cookies.

Flow (one headless browser, ~10-20 s; Cloudflare passes automatically, sometimes the first load is slow):
  open /s/<id> -> if already activated (had_follow) just verify by clicking through;
  else click 「同意声明,继续访问下载」 -> dialog (公众号 QR /img/maoli.jpg, 「微信关注发送"动态码"获取动态码」)
  -> type code into the 请输入动态码 field (#activeCode) -> click the dialog's own 确认 button
  -> page posts /active (body '1' = success) and opens /cv/<id> (302 -> real share URL).
Captures the /active status+body, the /cv/ redirect target, and the alert text on failure.
Saves cookies to ~/.secrets/aipanso_cookies.json (chmod 600) and prints the _egg activation cookie expiry.
Usage: aipanso_activate.py CODE [--url https://aipanso.com/s/<id>] [--fresh (ignore saved cookies)] [--no-save]
Exit: 0 activated/verified, 2 code rejected, 3 page/dialog problem.
"""
import sys, os, time, json, argparse, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aipanso_common import (BASE, BTN, PT, load_cookies, save_cookies, activation_expiry, fmt_exp, had_follow,
                            click_through, COOKIES)
from scrapling.fetchers import StealthyFetcher

DEFAULT = f"{BASE}/s/SzoPAyHYjom1cuezzGZiKnL"   # 宫崎骏 (夸克 folder), any /s/ page works

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("code"); ap.add_argument("--url", default=DEFAULT)
    ap.add_argument("--fresh", action="store_true"); ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    res = {"started": datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z"), "url": a.url}
    def act(page):
        ctx = page.context
        def onresp(r):
            if r.url.rstrip("/").endswith("/active"):
                try:
                    body = r.text()[:200]
                except Exception as e:
                    body = f"<unreadable: {e}>"
                res["active"] = {"status": r.status, "body": body}
        ctx.on("response", onresp)
        try:
            page.wait_for_selector(BTN, timeout=45000)
            res["t_page"] = round(time.time() - t0, 1)
            res["had_follow_before"] = had_follow(page)
            if res["had_follow_before"]:
                res["note"] = "already activated (saved cookie); code not used"
            else:
                page.locator(BTN).first.click()
                fld = page.locator("input[placeholder='请输入动态码']").first
                fld.wait_for(state="visible", timeout=8000)
                res["dialog_text"] = page.locator(".van-dialog:visible").first.inner_text()[:300]
                fld.fill(a.code)
                with ctx.expect_page(timeout=12000) as pi:
                    try:
                        page.locator(".van-dialog:visible .van-dialog__confirm").first.click()
                    except Exception as e:
                        res["confirm_err"] = str(e)[:150]
                    page.wait_for_timeout(3000)
                    if not res.get("active") or res["active"]["body"].strip() != "1":
                        alerts = page.locator(".van-dialog:visible .van-dialog__message").all_inner_texts()
                        res["alert"] = alerts
                        raise RuntimeError("no popup (code rejected?)")
                pop = pi.value
                for _ in range(40):
                    if not pop.url.startswith(("about:", BASE)):
                        break
                    pop.wait_for_timeout(250)
                res["real_url"] = pop.url.split("#")[0]
                pop.close()
            res["had_follow_after"] = had_follow(page) if not res.get("had_follow_before") else True
            if res.get("had_follow_before"):
                res["real_url"], res["cv_status"] = click_through(page)
        except Exception as e:
            res.setdefault("error", str(e)[:300])
        res["cookies"] = ctx.cookies()
        return page
    StealthyFetcher.fetch(a.url, headless=True, network_idle=False, timeout=90000, page_action=act, google_search=False,
                          cookies=[] if a.fresh else load_cookies())
    cookies = res.pop("cookies", [])
    res["t_total"] = round(time.time() - t0, 1)
    ok = bool(res.get("had_follow_before") or (res.get("active", {}).get("body", "").strip() == "1"))
    exp = activation_expiry(cookies)
    res["activation_cookie"] = f"_egg until {fmt_exp(exp)}" if exp else "no _egg cookie"
    if ok and not a.no_save and cookies:
        save_cookies(cookies); res["saved"] = COOKIES
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if ok:
        print(f"OK 激活有效（_egg 到 {fmt_exp(exp)}）；真实链接 {res.get('real_url','-')}；用时 {res['t_total']}s")
        sys.exit(0)
    if res.get("active"):
        print(f"FAILED 动态码被拒：/active -> {res['active']}; 提示 {res.get('alert')}", file=sys.stderr); sys.exit(2)
    print(f"FAILED 页面/弹窗问题：{res.get('error')}", file=sys.stderr); sys.exit(3)

if __name__ == "__main__":
    main()
