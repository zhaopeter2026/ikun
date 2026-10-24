#!/workspace/scrapling-env/bin/python
"""Search 小云搜索 yunso.net (the working "xyso" engine - same program as the abandoned 资源菠萝 daiguaji) for a keyword.

Access: plain HTTP, no login, no browser. The site's own page does exactly this:
  1) GET  /index/user/s?wd=<kw>                       (sets PHPSESSID; behind Cloudflare, no challenge seen)
  2) POST /api/Core/search2?requestID=&mode=90002&scope_content=0&stype=<pan>&wd=<kw>&uk=&page=1&limit=30
          body: xyso_turnstile_token=   (empty - the page only fills it when the server asks for Turnstile)
     -> JSON {code, msg, time, data}; data = HTML cards:
        <a onclick="open_sid(this)" url="<real share link>" pa="<提取码>">title</a>, time, <img class="sr-type" alt="夸克">
  Card times are Beijing time on the site; output column 'time' is converted to PT.
  Unlike daiguaji, yunso puts the plain share URL in the card (no client-side Decrypt needed).
Limits: guests only get the first 30 results per keyword (page 2 -> "-50001 超限制 仅允许显示前30项"), so 1 POST per run.
  If the server starts demanding 人机验证 (Cloudflare Turnstile / captcha), we stop (exit 3) - never solved or bypassed.
robots.txt: allows all. Data is aggregated from third-party search engines (site notice).
Polite: 1.5-2.5 s random delay between the 2 requests.
Usage: yunso_search.py KEYWORD [--pan all|quark|baidu|ali|tianyi|caiyun|xunlei|lanzou|kuaitu|uc] [--smart] [--out DIR]
Exit: 0 ok (incl. 0 results), 2 site error, 3 人机验证 required.
"""
import os, sys, re, csv, json, time, random, datetime, argparse, html, http.cookiejar, urllib.request, urllib.error
from urllib.parse import quote, urlencode, urlparse, parse_qs
from zoneinfo import ZoneInfo

BASE = "https://www.yunso.net"
PT = ZoneInfo("America/Los_Angeles")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
STYPE = {"all": "", "ali": "20100", "baidu": "1", "quark": "20500", "tianyi": "20000", "caiyun": "20300",
         "xunlei": "20400", "lanzou": "20600", "kuaitu": "20503", "uc": "20501"}
PAN_NAME = {"夸克": "夸克网盘", "百度": "百度网盘", "阿里": "阿里云盘", "迅雷": "迅雷云盘", "天翼": "天翼云盘", "UC": "UC网盘",
            "蓝奏": "蓝奏云", "蓝凑": "蓝奏云", "彩和": "移动云盘", "移动": "移动云盘", "快兔": "快兔网盘", "115": "115网盘", "123": "123云盘"}
HOSTS = [("quark.cn", "夸克网盘"), ("aliyundrive.com", "阿里云盘"), ("alipan.com", "阿里云盘"), ("baidu.com", "百度网盘"),
         ("xunlei.com", "迅雷云盘"), ("uc.cn", "UC网盘"), ("189.cn", "天翼云盘"), ("139.com", "移动云盘"), ("lanzou", "蓝奏云"),
         ("115", "115网盘"), ("123pan", "123云盘")]
NEED_HUMAN = "小云搜索要求人机验证（Cloudflare Turnstile），按规则不绕过，已停止；需要时请 Peter 在自己浏览器里搜"
_last = [0.0]
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def req(url, data=None, ref=BASE + "/"):
    wait = random.uniform(1.5, 2.5) - (time.time() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    h = {"User-Agent": UA, "Referer": ref, "Accept-Language": "zh-CN,zh;q=0.9"}
    if data is not None:
        h.update({"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*; q=0.01",
                  "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
    for attempt in (1, 2):
        try:
            r = opener.open(urllib.request.Request(url, data=data, headers=h), timeout=40)
            _last[0] = time.time()
            return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            _last[0] = time.time()
            body = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt == 1:
                print(f"[warn] HTTP {e.code} - retrying in 10 s", file=sys.stderr); time.sleep(10); continue
            return e.code, body

CN = ZoneInfo("Asia/Shanghai")

def cn_to_pt(t):
    """Site times are Beijing time; return PT."""
    try:
        return datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CN).astimezone(PT).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return t

def txt(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()

def pan_of(url, alt):
    host = urlparse(url).netloc.lower()
    for d, n in HOSTS:
        if d in host:
            return n
    for k, n in PAN_NAME.items():
        if k in (alt or ""):
            return n
    return alt or "未知"

def parse(frag):
    rows = []
    for card in re.split(r'<div class="layui-card sr-card"', frag)[1:]:
        a = re.search(r'<a onclick="open_sid\(this\)"[^>]*?url="([^"]*)"[^>]*?pa="([^"]*)"[^>]*>(.*?)</a>', card, re.S)
        if not a:
            continue
        url = html.unescape(a.group(1)).strip()
        url = re.sub(r"[?&]$", "", url)
        qs = parse_qs(urlparse(url).query)
        code = html.unescape(a.group(2)).strip() or (qs.get("pwd") or qs.get("password") or [""])[0]
        tm = re.search(r"layui-icon-time[^>]*></i>\s*([\d-]+ [\d:]+)", card)
        age = re.search(r'<span class="badge[^"]*">([^<]+)</span>', card)
        alt = re.search(r'<img class="sr-type"[^>]*alt="([^"]*)"', card)
        cnt = re.search(r'<span class="sr-count">([^<]*)</span>', card)
        qid = re.search(r'data-qid="(\d+)"', card)
        rows.append({"title": txt(a.group(3)), "pan_type": pan_of(url, alt.group(1) if alt else ""), "share_url": url,
                     "extraction_code": code, "time": cn_to_pt(tm.group(1)) if tm else "", "age": age.group(1).strip() if age else "",
                     "files": txt(cnt.group(1)).replace("所有文件共计：", "") if cnt else "", "qid": qid.group(1) if qid else ""})
    return rows

def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("keyword"); ap.add_argument("--pan", default="all", choices=list(STYPE))
    ap.add_argument("--smart", action="store_true", help="mode 90001 智慧搜索 instead of 90002 精确搜索")
    ap.add_argument("--out", default="/workspace/scrapes/out")
    a = ap.parse_args()
    kw = a.keyword.strip()
    os.makedirs(a.out, exist_ok=True)
    ts = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S %Z")
    page_url = f"{BASE}/index/user/s?wd={quote(kw)}"
    st, body = req(page_url)
    if st != 200:
        if st == 403 and ("challenge" in body or "Just a moment" in body):
            print(NEED_HUMAN, file=sys.stderr); sys.exit(3)
        print(f"小云搜索页面打不开：HTTP {st}", file=sys.stderr); sys.exit(2)
    q = {"requestID": "", "mode": "90001" if a.smart else "90002", "scope_content": "0", "stype": STYPE[a.pan], "wd": kw,
         "uk": "", "page": "1", "limit": "30", "screen_filetype": "", "screen_time": "", "screen_size": "", "screen_sort": ""}
    st, body = req(f"{BASE}/api/Core/search2?{urlencode(q)}", data=b"xyso_turnstile_token=", ref=page_url)
    err, rows = "", []
    try:
        j = json.loads(body)
    except Exception:
        j = None
    if st == 403 or j is None:
        err = NEED_HUMAN if ("challenge" in body or "turnstile" in body.lower()) else f"接口返回异常：HTTP {st}"
    else:
        code, frag = j.get("code"), j.get("data") or ""
        if isinstance(frag, dict):
            frag = json.dumps(frag, ensure_ascii=False)
        low = (str(j.get("msg") or "") + str(frag)[:3000]).lower()
        if code != 0 or "turnstile" in low or "人机验证" in low or "captcha" in low:
            if "turnstile" in low or "人机验证" in low or "captcha" in low or code in (-2, -10):
                err = NEED_HUMAN
            else:
                m = re.search(r'empty-desc">([^<]+)<', str(frag))
                err = f"小云搜索返回 code {code}：{j.get('msg') or (m.group(1) if m else '')}"
        else:
            rows = parse(frag)
            if not rows:
                m = re.search(r'empty-desc">([^<]+)<', frag)
                if m and re.search(r"-\d{3,}|失败|错误|限制", m.group(1)):
                    err = "小云搜索：" + txt(m.group(1))
    for r in rows:
        r["scraped_at"] = ts
    base = os.path.join(a.out, f"yunso_{kw}{'_' + a.pan if a.pan != 'all' else ''}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "pan": a.pan, "mode": "智慧" if a.smart else "精确", "scraped_at": ts,
                   "guest_limit": 30, "error": err or None, "results": rows}, f, ensure_ascii=False, indent=1)
    cols = ["title", "pan_type", "share_url", "extraction_code", "time", "age", "files", "qid", "scraped_at"]
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    by = {}
    for r in rows:
        by[r["pan_type"]] = by.get(r["pan_type"], 0) + 1
    print(f"{len(rows)} results (游客最多30条) @ {ts}" + (f"\nERROR: {err}" if err else "") + f"\n{base}.json\n{base}.csv\nby pan: {by}")
    for r in rows:
        print(f"{r['pan_type']}\t{r['share_url']}\t{r['extraction_code'] or '-'}\t{r['time']}\t{r['title'][:60]}")
    if err:
        sys.exit(3 if err == NEED_HUMAN else 2)

if __name__ == "__main__":
    main()
