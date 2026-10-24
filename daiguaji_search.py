#!/workspace/scrapling-env/bin/python
"""Search daiguaji.com (资源菠萝 / xyso "yunso" engine) for a keyword and save results.

How the site works (reverse-engineered 2026-10-21 from /assets/js/frontend/search.js and
search-results.js):
  * The search box is NOT a <form>. JS reads #keyword and loads /index/user/s?wd=<keyword>;
    that page then AJAX-POSTs (empty body, X-Requested-With) to
      /api/Core/search2?wd=<kw>&uk=&page=<n>&limit=20&mode=90002&screen_filetype=&
        screen_brickType_time=&screen_brickType_size=&screen_brickType_sort=&stype=0&
        scope_content=0&searchModescope=&token=
    mode 90002=精确搜索, 90001=智慧搜索; stype = drive filter (0 全部, 20100 阿里, 1 百度, 20500 夸克,
    20000 天翼, 20300 移动/彩云, 20400 迅雷, 20600 蓝奏, 20503 快兔, 20501 UC); scope_content 0=仅标题 1=标题+内容.
  * JSON reply {code, msg, data}: data is an HTML fragment for #Searchshow.
      code 0 = OK; -3 = keyword rejected; -2 = reCAPTCHA v2 challenge; -10 = reCAPTCHA v3 check.
  * Result cards (.layui-card / .result-card) hold <a url="<ciphertext>" pa="<提取码>">title</a>;
    the share URL is decrypted client-side by window.Decrypt() from /assets/xyso/yunso.secure.js
    (obfuscated, contains runtime/bot checks), so it must run in a real browser page.
So this script drives a headless browser (Scrapling StealthyFetcher) through the site's own
search page, reads the decrypted links with the page's own Decrypt(), and never clicks links
or opens the drives. It does not log in, submit, or solve captchas: if the site answers with a
captcha (-2/-10 that does not clear by itself) or an error, it stops and reports it.
Pagination: 20/page, >=2.5 s between pages (site JS rejects <2 s), cap 100 rows.

Usage: daiguaji_search.py <keyword> [outdir]   (uses the site default: 精确搜索, 全部网盘, 仅标题)
Exit codes: 0 ok (incl. 0 results), 2 site error / blocked (details printed + written to the .md).
"""
import sys, os, csv, re, json, time, base64, datetime, argparse
from urllib.parse import quote, urlparse, parse_qs
from zoneinfo import ZoneInfo
from scrapling.fetchers import StealthyFetcher

BASE = "https://www.daiguaji.com"
CAP, PER_PAGE, DELAY = 100, 20, 2.5
DOMAINS = [("quark.cn", "夸克网盘"), ("aliyundrive.com", "阿里云盘"), ("alipan.com", "阿里云盘"),
           ("baidu.com", "百度网盘"), ("xunlei.com", "迅雷云盘"), ("uc.cn", "UC网盘"),
           ("123pan", "123云盘"), ("123684.com", "123云盘"), ("123865.com", "123云盘"), ("123912.com", "123云盘"),
           ("115.com", "115网盘"), ("115cdn.com", "115网盘"), ("189.cn", "天翼云盘"),
           ("139.com", "移动云盘"), ("caiyun", "移动云盘"), ("lanzou", "蓝奏云"), ("pikpak", "PikPak"),
           ("guangya", "光鸭云盘"), ("kuaitu", "快兔网盘")]
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?\s*(?:[KMGT]i?B|[KMGT]))\b", re.I)
DATE_RE = re.compile(r"(20\d\d[-/.]\d{1,2}[-/.]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)")

# JS run inside the page after results render: returns cards with decrypted links.
EXTRACT_JS = r"""
() => {
  const out = [];
  const box = document.querySelector('#Searchshow');
  if (!box) return {err: 'no #Searchshow'};
  const empty = box.querySelector('.empty-desc, .ui-msg-title');
  const cards = box.querySelectorAll('.layui-card, .result-card');
  cards.forEach(c => {
    const a = c.querySelector('a[url], a.result-link');
    if (!a) return;
    const raw = a.getAttribute('url') || a.getAttribute('href') || '';
    let url = '';
    try { url = (typeof window.Decrypt === 'function') ? window.Decrypt(raw) : ''; } catch (e) { url = ''; }
    out.push({title: (a.textContent || '').trim(), raw: raw, url: url || '', pa: a.getAttribute('pa') || '',
              qid: c.getAttribute('data-qid') || c.getAttribute('id') || '',
              img: (c.querySelector('img') || {}).alt || '', text: (c.innerText || '').replace(/\s+/g, ' ').trim()});
  });
  const total = (document.getElementById('laypage') || {getAttribute: () => null}).getAttribute('data-count')
                || ((box.querySelector('strong') || {}).textContent || '');
  return {cards: out, total: total, empty: empty ? empty.innerText.trim() : '',
          hasDecrypt: typeof window.Decrypt === 'function',
          captcha: !!document.querySelector('#recaptchaContainer')};
}
"""

def drive_of(url, hint=""):
    host = urlparse(url).netloc.lower()
    for d, name in DOMAINS:
        if d in host:
            return name
    return hint or "未知"

def local_decode(raw):
    """Fallback only: some xyso skins put base64 or plain URLs in the attribute."""
    if raw.startswith("http"):
        return raw
    try:
        s = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
        return s if s.startswith("http") else ""
    except Exception:
        return ""

def search(keyword):
    state = {"pages": [], "api": [], "error": ""}

    def action(page):
        page.on("response", lambda r: state["api"].append(r) if "/api/Core/search2" in r.url else None)

        def wait_result(n_before):
            for _ in range(60):                       # up to ~30 s per page
                page.wait_for_timeout(500)
                if len(state["api"]) > n_before:
                    page.wait_for_timeout(800)
                    return True
            return False

        def last_json():
            try:
                return state["api"][-1].json()
            except Exception:
                return None

        wait_result(0)
        for pg in range(1, CAP // PER_PAGE + 1):
            j = last_json()
            code = j.get("code") if isinstance(j, dict) else None
            if code in (-2, -10):
                # give the site's own invisible reCAPTCHA v3 flow one chance to complete by itself
                n = len(state["api"])
                if code == -10 and wait_result(n) and (last_json() or {}).get("code") == 0:
                    pass
                else:
                    state["error"] = f"站点要求人机验证 (code {code}: {'reCAPTCHA v2 勾选/图片验证' if code == -2 else 'reCAPTCHA v3'})；按规则不绕过，已停止"
                    break
            elif code == -3:
                state["error"] = "站点拒绝该关键词 (code -3: 关键词不符合人类习惯)"
                break
            elif code not in (0, None):
                state["error"] = f"站点返回 code {code}: {j.get('msg') if isinstance(j, dict) else ''}"
                break
            res = page.evaluate(EXTRACT_JS)
            state["pages"].append(res)
            if res.get("empty") and not res.get("cards"):
                if pg == 1 and re.search(r"failed|refused|错误|失败|-\d+", res["empty"]):
                    state["error"] = "站点搜索后端报错: " + res["empty"]
                break
            if res.get("captcha"):
                state["error"] = "站点弹出人机验证；按规则不绕过，已停止"
                break
            got = sum(len(p.get("cards", [])) for p in state["pages"])
            if len(res.get("cards", [])) < PER_PAGE or got >= CAP:
                break
            time.sleep(DELAY)
            n = len(state["api"])
            page.evaluate(f"() => window.setSearchParamsFromExternal({{page: {pg + 1}}})")
            if not wait_result(n):
                state["error"] = f"第 {pg + 1} 页超时"
                break
        return page

    url = f"{BASE}/index/user/s?wd={quote(keyword)}"
    StealthyFetcher.fetch(url, headless=True, network_idle=True, wait=1500, timeout=90000,
                          page_action=action, disable_resources=False)

    rows, seen = [], set()
    for p in state["pages"]:
        for c in p.get("cards", []):
            share = c["url"] or local_decode(c["raw"])
            key = share or c["raw"]
            if not key or key in seen:
                continue
            seen.add(key)
            qs = parse_qs(urlparse(share).query) if share else {}
            code = c["pa"] or (qs.get("pwd") or qs.get("password") or [""])[0]
            m_size, m_date = SIZE_RE.search(c["text"]), DATE_RE.search(c["text"])
            rows.append({"title": c["title"], "drive": drive_of(share, c["img"]), "share_url": share,
                         "extraction_code": code, "size": m_size.group(1) if m_size else "",
                         "date": m_date.group(1) if m_date else "",
                         "daiguaji_url": f"{BASE}/d/{c['qid'].split('-')[0]}" if c["qid"] else "",
                         "note": "" if share else "链接未能解密(Decrypt 不可用)"})
            if len(rows) >= CAP:
                break
    total = state["pages"][0].get("total", "") if state["pages"] else ""
    return rows, state["error"], total

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("outdir", nargs="?", default=os.path.dirname(os.path.abspath(__file__)))
    a = ap.parse_args()
    kw = a.keyword
    ts = datetime.datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M:%S %Z")
    rows, err, total = search(kw)
    for r in rows:
        r["scraped_at"] = ts
    cols = ["title", "drive", "share_url", "extraction_code", "size", "date", "daiguaji_url", "note", "scraped_at"]
    base = os.path.join(a.outdir, f"daiguaji_{kw}")
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(f"# daiguaji.com 网盘搜索: {kw}\n\nScraped: {ts} · Source: {BASE}/index/user/s → POST /api/Core/search2 · "
                f"Results: {len(rows)}" + (f" (site total: {total})" if total else "") + "\n\n")
        if err:
            f.write(f"**站点状态 / 错误:** {err}\n\n")
        f.write("| # | 标题 | 网盘 | 分享链接 | 提取码 | 大小 | 日期 |\n|---|---|---|---|---|---|---|\n")
        for i, r in enumerate(rows, 1):
            t = r["title"].replace("|", "\\|")
            f.write(f"| {i} | {t} | {r['drive']} | {r['share_url']} | {r['extraction_code'] or '—'} | {r['size'] or '—'} | {r['date'] or '—'} |\n")
        if not rows:
            f.write("_No results._\n")
    print(f"{len(rows)} results @ {ts}" + (f"\nERROR: {err}" if err else "") + f"\n{base}.csv\n{base}.md")
    for r in rows:
        print(f"{r['drive']}\t{r['share_url']}\t{r['extraction_code'] or '-'}\t{r['size'] or '-'}\t{r['title']}")
    sys.exit(2 if err and not rows else 0)

if __name__ == "__main__":
    main()
