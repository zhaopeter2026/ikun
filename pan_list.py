#!/workspace/scrapling-env/bin/python
"""List files in a public netdisk share link anonymously (read-only).

Supported: 百度 pan.baidu.com, 夸克 pan.quark.cn, 阿里 aliyundrive/alipan, UC drive.uc.cn, 天翼 cloud.189.cn,
115 115.com/115cdn.com, 123云盘 123pan.com/123684.com/123912.com/123865.com..., 光鸭 guangyapan.com
(plain anonymous JSON), 迅雷 pan.xunlei.com and 移动云盘 yun.139.com/caiyun.139.com (headless browser:
the page's own JS makes its calls; we read the responses / DOM).

Usage: pan_list.py <url> [pwd] [--code PWD] [--depth N] [--cap N] [--csv out.csv] [--proxy URL]
Extraction code: ?pwd= / ?password= in the URL, positional pwd, or --code.
Replicates the anonymous calls the share web page makes; never logs in,
never saves/transfers/downloads, 1.5-2.5 s between requests. Status: OK / expired or deleted / needs login /
blocked or captcha / error.
"""
import sys, os, json, time, re, csv, argparse, html, random
from urllib.parse import urlparse, parse_qs, quote
from scrapling.fetchers import Fetcher

PROXY = None  # set by --proxy


def nap():
    time.sleep(random.uniform(1.5, 2.5))


def GET(url, **kw):
    if PROXY:
        kw["proxy"] = PROXY
    return Fetcher.get(url, **kw)


def POST(url, **kw):
    if PROXY:
        kw["proxy"] = PROXY
    return Fetcher.post(url, **kw)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _j(r):
    b = r.body if isinstance(r.body, (bytes, bytearray)) else str(r.body).encode()
    t = b.decode("utf-8", "replace").strip()
    try:
        return json.loads(t)
    except Exception:
        raise RuntimeError(f"HTTP {r.status} non-JSON: {t[:200]!r}")


def human(n):
    if n is None or n == "":
        return ""
    if isinstance(n, str) and not n.isdigit():
        return n  # already human-readable (DOM-scraped)
    n = float(n)
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.2f} {u}"
        n /= 1024


class Result:
    def __init__(self, drive):
        self.drive = drive; self.status = "OK"; self.message = ""
        self.title = ""; self.entries = []; self.truncated = False

    def add(self, path, name, typ, size):
        self.entries.append({"path": path, "name": name, "type": typ,
                             "size": (int(size) if str(size).isdigit() else size) if size not in (None, "") else None})


# ---------------- Quark ----------------
def quark(url, pwd, depth, cap):
    res = Result("夸克网盘")
    pwd_id = re.search(r"/s/([0-9a-zA-Z]+)", url).group(1)
    H = {"Referer": "https://pan.quark.cn/", "Origin": "https://pan.quark.cn", "User-Agent": UA}
    base = "https://drive-pc.quark.cn/1/clouddrive/share/sharepage"
    d = _j(POST(f"{base}/token?pr=ucpro&fr=pc", json={"pwd_id": pwd_id, "passcode": pwd or ""}, headers=H))
    if d.get("code") != 0:
        msg = f"token code={d.get('code')} status={d.get('status')} message={d.get('message')}"
        res.message = msg
        m = str(d.get("message", ""))
        if any(k in m for k in ["失效", "取消", "不存在", "过期", "删除", "违规", "封禁"]) or d.get("code") in (41004, 41006, 41011, 41012, 41010):
            res.status = "expired or deleted"
        elif "提取码" in m or "passcode" in m.lower() or d.get("code") == 41008:
            res.status = "needs password"
        elif "验证" in m or "频繁" in m:
            res.status = "blocked or captcha"
        else:
            res.status = "error"
        return res
    stoken = d["data"]["stoken"]; res.title = d["data"].get("title", "")

    def ls(fid, path, lvl):
        page = 1
        while True:
            if len(res.entries) >= cap:
                res.truncated = True; return
            nap()
            q = (f"{base}/detail?pr=ucpro&fr=pc&pwd_id={pwd_id}&stoken={quote(stoken)}&pdir_fid={fid}"
                 f"&force=0&_page={page}&_size=50&_fetch_banner=0&_fetch_share=0&_fetch_total=1"
                 f"&_sort=file_type:asc,file_name:asc")
            r = _j(GET(q, headers=H))
            if r.get("code") != 0:
                raise RuntimeError(f"detail code={r.get('code')} message={r.get('message')}")
            items = r["data"]["list"]
            subs = []
            for it in items:
                if len(res.entries) >= cap:
                    res.truncated = True; break
                isdir = bool(it.get("dir"))
                nm = html.unescape(it["file_name"])  # Quark HTML-escapes names (e.g. &#39;)
                p = f"{path}/{nm}"
                res.add(path or "/", nm, "folder" if isdir else "file", None if isdir else it.get("size"))
                if isdir:
                    subs.append((it["fid"], p))
            for fid2, p in subs:
                if lvl < depth:
                    ls(fid2, p, lvl + 1)
            total = r.get("metadata", {}).get("_total", 0)
            if page * 50 >= total or not items:
                return
            page += 1
    ls("0", "", 0)
    return res


# ---------------- Aliyun ----------------
def aliyun(url, pwd, depth, cap):
    res = Result("阿里云盘")
    sid = re.search(r"/s/([0-9a-zA-Z]+)", url).group(1)
    H = {"Referer": "https://www.aliyundrive.com/", "Origin": "https://www.aliyundrive.com",
         "Content-Type": "application/json", "User-Agent": UA}
    api = "https://api.aliyundrive.com"
    r = POST(f"{api}/adrive/v3/share_link/get_share_by_anonymous?share_id={sid}", json={"share_id": sid}, headers=H)
    d = _j(r)
    if "code" in d and "file_infos" not in d:
        res.message = f"HTTP {r.status} code={d.get('code')} message={d.get('message')}"
        c = str(d.get("code", ""))
        res.status = "expired or deleted" if any(k in c for k in ["Expired", "Cancelled", "NotFound", "Forbidden", "ShareLink"]) else "error"
        return res
    res.title = d.get("share_name") or d.get("share_title", "")
    nap()
    t = _j(POST(f"{api}/v2/share_link/get_share_token", json={"share_id": sid, "share_pwd": pwd or ""}, headers=H))
    if "share_token" not in t:
        res.status = "needs password" if d.get("has_pwd") else "error"
        res.message = f"get_share_token code={t.get('code')} message={t.get('message')}"
        return res
    HT = {**H, "x-share-token": t["share_token"]}

    def ls(fid, path, lvl):
        marker = ""
        while True:
            if len(res.entries) >= cap:
                res.truncated = True; return
            nap()
            body = {"share_id": sid, "parent_file_id": fid, "limit": 100, "order_by": "name",
                    "order_direction": "ASC", "marker": marker}
            r = _j(POST(f"{api}/adrive/v2/file/list_by_share", json=body, headers=HT))
            if "items" not in r:
                raise RuntimeError(f"list_by_share code={r.get('code')} message={r.get('message')}")
            subs = []
            for it in r["items"]:
                if len(res.entries) >= cap:
                    res.truncated = True; break
                isdir = it["type"] == "folder"
                res.add(path or "/", it["name"], "folder" if isdir else "file", None if isdir else it.get("size"))
                if isdir:
                    subs.append((it["file_id"], f"{path}/{it['name']}"))
            for f2, p in subs:
                if lvl < depth:
                    ls(f2, p, lvl + 1)
            marker = r.get("next_marker") or ""
            if not marker:
                return
    ls("root", "", 0)
    return res


# ---------------- Baidu ----------------
BAIDU_ERR = {-9: "wrong or missing extraction code", -12: "extraction code required",
             -62: "too many attempts / captcha required", 105: "link invalid", 2: "parameter error",
             -21: "share cancelled", 115: "file banned", 117: "share expired", -7: "share deleted",
             145: "share expired"}


def baidu(url, pwd, depth, cap):
    res = Result("百度网盘")
    u = urlparse(url)
    m = re.search(r"/s/1([0-9A-Za-z_\-]+)", u.path)
    surl = m.group(1) if m else parse_qs(u.query).get("surl", [""])[0]
    pwd = pwd or parse_qs(u.query).get("pwd", [""])[0]
    H = {"Referer": f"https://pan.baidu.com/s/1{surl}", "Origin": "https://pan.baidu.com", "User-Agent": UA}
    ck = {}
    if pwd:
        t = int(time.time() * 1000)
        d = _j(POST(f"https://pan.baidu.com/share/verify?surl={surl}&t={t}&channel=chunlei&web=1&app_id=250528&clienttype=0",
                            data={"pwd": pwd, "vcode": "", "vcode_str": ""}, headers=H))
        if d.get("errno") != 0:
            e = d.get("errno")
            res.message = f"verify errno={e} ({BAIDU_ERR.get(e, d.get('err_msg', ''))})"
            res.status = {-62: "blocked or captcha", -9: "needs password", -12: "needs password"}.get(e, "expired or deleted")
            return res
        ck = {"BDCLND": d["randsk"]}
        nap()
    common = "web=5&app_id=250528&desc=1&showempty=0&num=100&order=name&channel=chunlei&clienttype=0"
    r = _j(GET(f"https://pan.baidu.com/share/list?{common}&page=1&shorturl={surl}&root=1", headers=H, cookies=ck))
    if r.get("errno") != 0:
        e = r.get("errno")
        res.message = f"list errno={e} ({BAIDU_ERR.get(e, r.get('show_msg', ''))})"
        res.status = "needs password" if e in (-9, -12) else ("blocked or captcha" if e == -62 else "expired or deleted")
        return res
    uk, shareid = r["uk"], r["share_id"]
    res.title = r.get("title", "")

    def emit(items, path, lvl):
        subs = []
        for it in items:
            if len(res.entries) >= cap:
                res.truncated = True; break
            isdir = str(it["isdir"]) == "1"
            res.add(path or "/", it["server_filename"], "folder" if isdir else "file", None if isdir else it.get("size"))
            if isdir:
                subs.append((it["path"], f"{path}/{it['server_filename']}"))
        for full, p in subs:
            if lvl < depth:
                ls(full, p, lvl + 1)

    def ls(full, path, lvl):
        page = 1
        while True:
            if len(res.entries) >= cap:
                res.truncated = True; return
            nap()
            x = _j(GET(f"https://pan.baidu.com/share/list?{common}&page={page}&uk={uk}&shareid={shareid}&dir={quote(full)}",
                               headers=H, cookies=ck))
            if x.get("errno") != 0:
                raise RuntimeError(f"list dir errno={x.get('errno')}")
            emit(x["list"], path, lvl)
            if len(x["list"]) < 100:
                return
            page += 1
    emit(r["list"], "", 0)
    return res


# ---------------- helpers for the new drives ----------------
def _qs(url, *keys):
    q = parse_qs(urlparse(url).query)
    for k in keys:
        if q.get(k):
            return q[k][0]
    return ""


class Walker:
    """Shared recursion bookkeeping: depth/cap limits."""
    def __init__(self, res, depth, cap):
        self.res, self.depth, self.cap = res, depth, cap

    def full(self):
        if len(self.res.entries) >= self.cap:
            self.res.truncated = True
            return True
        return False


EXPIRED_WORDS = ["暂无文件", "banned", "失效", "取消", "不存在", "过期", "删除", "违规", "封禁", "屏蔽", "无效", "expired", "not found", "notfound", "cancel"]


def _classify(msg, default="error"):
    m = str(msg).lower()
    if any(k in m for k in ["提取码", "访问码", "密码", "passcode", "pass_code", "password", "receive_code"]):
        return "needs password"
    if any(k.lower() in m for k in EXPIRED_WORDS):
        return "expired or deleted"
    if any(k in m for k in ["验证码", "频繁", "captcha", "too many", "blocked"]):
        return "blocked or captcha"
    if "登录" in m or "login" in m:
        return "needs login"
    return default


# ---------------- UC 网盘 (drive.uc.cn) ----------------
def uc(url, pwd, depth, cap):
    """Same family as Quark: POST pc-api.uc.cn/1/clouddrive/share/sharepage/v2/detail (pwd_id + passcode + pdir_fid)."""
    res = Result("UC网盘"); w = Walker(res, depth, cap)
    pwd_id = re.search(r"/s/([0-9a-zA-Z]+)", url).group(1)
    H = {"Referer": "https://drive.uc.cn/", "Origin": "https://drive.uc.cn", "User-Agent": UA,
         "Content-Type": "application/json;charset=UTF-8"}
    api = "https://pc-api.uc.cn/1/clouddrive/share/sharepage/v2/detail?pr=UCBrowser&fr=pc"

    def call(fid, page):
        body = {"pwd_id": pwd_id, "passcode": pwd or "", "force": 0, "page": page, "size": 50,
                "fetch_banner": 0, "fetch_share": 1 if fid == "0" else 0, "fetch_total": 1,
                "sort": "file_type:asc,file_name:asc", "pdir_fid": fid}
        return _j(POST(api, json=body, headers=H))

    first = {"v": True}

    def ls(fid, path, lvl):
        page = 1
        while True:
            if w.full(): return
            if not first["v"]:
                nap()
            first["v"] = False
            r = call(fid, page)
            if r.get("code") != 0:
                if fid == "0":
                    res.message = f"code={r.get('code')} message={r.get('message')}"
                    res.status = _classify(r.get("message", ""))
                    return
                raise RuntimeError(f"detail code={r.get('code')} message={r.get('message')}")
            di = r["data"].get("detail_info") or {}
            if fid == "0":
                res.title = (r["data"].get("token_info") or {}).get("title", "")
            items = di.get("list") or []
            subs = []
            for it in items:
                if w.full(): break
                isdir = bool(it.get("dir"))
                nm = html.unescape(it["file_name"])
                res.add(path or "/", nm, "folder" if isdir else "file", None if isdir else it.get("size"))
                if isdir:
                    subs.append((it["fid"], f"{path}/{nm}"))
            for f2, p in subs:
                if lvl < depth:
                    ls(f2, p, lvl + 1)
            total = (r.get("metadata", {}).get("detail_meta") or {}).get("_total", 0)
            if page * 50 >= total or not items:
                return
            page += 1
    ls("0", "", 0)
    return res


# ---------------- 天翼云盘 (cloud.189.cn) ----------------
def tianyi(url, pwd, depth, cap):
    """getShareInfoByCodeV2 -> (checkAccessCode) -> listShareDir.action, all anonymous JSON on cloud.189.cn."""
    res = Result("天翼云盘"); w = Walker(res, depth, cap)
    m = re.search(r"/t/([0-9A-Za-z]+)", url)
    code = m.group(1) if m else _qs(url, "code", "shareCode")
    pwd = pwd or _qs(url, "pwd", "accessCode")
    H = {"Accept": "application/json;charset=UTF-8", "Referer": f"https://cloud.189.cn/web/share?code={code}",
         "User-Agent": UA, "sign-type": "1"}
    api = "https://cloud.189.cn/api/open/share"
    info = _j(GET(f"{api}/getShareInfoByCodeV2.action?noCache={random.random()}&shareCode={code}", headers=H))
    if str(info.get("res_code")) != "0":
        res.message = f"res_code={info.get('res_code')} {info.get('res_message', '')[:120]}"
        c = str(info.get("res_code"))
        res.status = "expired or deleted" if any(k in c for k in ["ShareNotFound", "ShareExpired", "ShareAuditNotPass", "FileNotFound", "ShareInfoNotFound"]) else _classify(info.get("res_message", ""))
        return res
    res.title = info.get("fileName", "")
    share_id = info.get("shareId")
    if not share_id:
        if not pwd:
            res.status = "needs password"; res.message = "private share (needAccessCode), pass ?pwd= or --code"; return res
        nap()
        ck = _j(GET(f"{api}/checkAccessCode.action?noCache={random.random()}&shareCode={code}&accessCode={pwd}", headers=H))
        share_id = ck.get("shareId")
        if not share_id:
            res.status = "needs password"; res.message = f"checkAccessCode: {ck}"[:200]; return res
    mode = info.get("shareMode", 1)
    if not info.get("isFolder"):
        res.add("/", info.get("fileName"), "file", info.get("fileSize")); return res

    def ls(fid, path, lvl):
        page = 1
        while True:
            if w.full(): return
            nap()
            q = (f"{api}/listShareDir.action?noCache={random.random()}&pageNum={page}&pageSize=60&fileId={fid}"
                 f"&shareDirFileId={fid}&isFolder=true&shareId={share_id}&shareMode={mode}&iconOption=5"
                 f"&orderBy=filename&descending=false&accessCode={pwd or ''}")
            r = _j(GET(q, headers=H))
            if str(r.get("res_code")) != "0":
                if lvl == 0:
                    res.status = _classify(r.get("res_message", "")); res.message = f"listShareDir {r.get('res_code')} {r.get('res_message')}"; return
                raise RuntimeError(f"listShareDir res_code={r.get('res_code')} {r.get('res_message')}")
            fl = r.get("fileListAO") or {}
            folders, files = fl.get("folderList") or [], fl.get("fileList") or []
            subs = []
            for it in folders:
                if w.full(): break
                res.add(path or "/", it["name"], "folder", None); subs.append((it["id"], f"{path}/{it['name']}"))
            for it in files:
                if w.full(): break
                res.add(path or "/", it["name"], "file", it.get("size"))
            for f2, p in subs:
                if lvl < depth:
                    ls(f2, p, lvl + 1)
            if page * 60 >= int(fl.get("count") or 0) or not (folders or files):
                return
            page += 1
    ls(info["fileId"], "", 0)
    return res


# ---------------- 115 (115.com / 115cdn.com) ----------------
def pan115(url, pwd, depth, cap):
    """GET https://<host>/webapi/share/snap?share_code=&receive_code=&cid=&offset=&limit= (anonymous)."""
    res = Result("115网盘"); w = Walker(res, depth, cap)
    host = "115cdn.com"  # 115.com/s/* 302-redirects to 115cdn.com; its /webapi is only on 115cdn.com
    sc = re.search(r"/s/([0-9a-zA-Z]+)", url).group(1)
    pwd = pwd or _qs(url, "password", "pwd")
    H = {"Referer": f"https://{host}/s/{sc}", "User-Agent": UA, "Accept": "application/json"}
    first = {"v": True}

    def ls(cid, path, lvl):
        off = 0
        while True:
            if w.full(): return
            if not first["v"]:
                nap()
            first["v"] = False
            r = _j(GET(f"https://{host}/webapi/share/snap?share_code={sc}&receive_code={pwd}&offset={off}&limit=100&asc=1&o=file_name&cid={cid}&format=json", headers=H))
            if not r.get("state"):
                msg = f"errno={r.get('errno')} error={r.get('error')}"
                if cid == "0":
                    res.message = msg
                    res.status = "needs password" if (not pwd or "提取码" in str(r.get("error")) or "访问码" in str(r.get("error"))) and r.get("errno") in (4100012, 4100019, 4100030) else _classify(r.get("error", ""), "expired or deleted")
                    return
                raise RuntimeError(msg)
            d = r["data"]
            if cid == "0":
                si = d.get("shareinfo") or {}
                res.title = si.get("share_title", "")
                if str(si.get("share_state", d.get("share_state", 1))) not in ("1",):
                    res.status = "expired or deleted"; res.message = f"share_state={si.get('share_state')} {si.get('forbid_reason', '')}"; return
            items = d.get("list") or []
            subs = []
            for it in items:
                if w.full(): break
                isdir = "fid" not in it
                res.add(path or "/", it["n"], "folder" if isdir else "file", None if isdir else it.get("s"))
                if isdir:
                    subs.append((str(it["cid"]), f"{path}/{it['n']}"))
            for c2, p in subs:
                if lvl < depth:
                    ls(c2, p, lvl + 1)
            off += len(items)
            if off >= int(d.get("count") or 0) or not items:
                return
    ls("0", "", 0)
    return res


# ---------------- 123云盘 (123pan.com / 123684.com / 123912.com / 123865.com ...) ----------------
P123_ERR = {5103: "wrong or missing extraction code", 5104: "share expired", 5113: "share cancelled/deleted"}


def pan123(url, pwd, depth, cap):
    """GET https://www.123684.com/b/api/share/get?shareKey=&SharePwd=&ParentFileId=&Page=&next= (same call the share page makes)."""
    res = Result("123云盘"); w = Walker(res, depth, cap)
    key = re.search(r"/s/([0-9A-Za-z_\-]+)", url).group(1).replace(".html", "")
    pwd = pwd or _qs(url, "pwd", "SharePwd")
    api = "https://www.123684.com/b/api/share/get"
    H = {"Referer": "https://www.123684.com/", "User-Agent": UA, "platform": "web", "App-Version": "3"}
    first = {"v": True}

    def ls(pid, path, lvl):
        page, nxt = 1, "0"
        while True:
            if w.full(): return
            if not first["v"]:
                nap()
            first["v"] = False
            r = _j(GET(f"{api}?limit=100&next={nxt}&orderBy=file_name&orderDirection=asc&shareKey={key}&SharePwd={quote(pwd or '')}"
                       f"&ParentFileId={pid}&Page={page}&event=homeListFile&operateType=1", headers=H))
            if r.get("code") != 0:
                msg = f"code={r.get('code')} message={r.get('message')}"
                if pid == "0":
                    res.message = msg
                    c = r.get("code")
                    res.status = "needs password" if c == 5103 or "提取码" in str(r.get("message")) else _classify(r.get("message", ""), "expired or deleted")
                    return
                raise RuntimeError(msg)
            d = r["data"]
            if d.get("Expired"):
                res.status = "expired or deleted"; res.message = "Expired=true"; return
            items = d.get("InfoList") or []
            subs = []
            for it in items:
                if w.full(): break
                isdir = it.get("Type") == 1
                res.add(path or "/", it["FileName"], "folder" if isdir else "file", None if isdir else it.get("Size"))
                if isdir:
                    subs.append((str(it["FileId"]), f"{path}/{it['FileName']}"))
            for p2, p in subs:
                if lvl < depth:
                    ls(p2, p, lvl + 1)
            nxt = str(d.get("Next", "-1"))
            if nxt == "-1" or not items:
                return
            page += 1
    ls("0", "", 0)
    if res.status == "OK" and not res.entries:
        res.message = "share returned an empty list"
    return res


# ---------------- 光鸭云盘 (guangyapan.com) ----------------
def guangya(url, pwd, depth, cap):
    """POST api.guangyapan.com/userres/v1/get_share_summary, get_share_access_token, get_share_page_files_list (anonymous)."""
    res = Result("光鸭云盘"); w = Walker(res, depth, cap)
    sid = re.search(r"/s/([0-9A-Za-z_]+)", url).group(1)
    api = "https://api.guangyapan.com/userres/v1"
    H = {"Referer": "https://www.guangyapan.com/", "Origin": "https://www.guangyapan.com", "User-Agent": UA,
         "Content-Type": "application/json", "dt": "4"}
    body = {"shareId": sid}
    if pwd:
        body["code"] = pwd
    s = _j(POST(f"{api}/get_share_summary", json={"shareId": sid}, headers=H))
    sd = s.get("data") or {}
    if s.get("msg") != "success" or not sd:
        res.status = _classify(s.get("msg", ""), "expired or deleted"); res.message = f"summary: {json.dumps(s, ensure_ascii=False)[:200]}"; return res
    res.title = sd.get("title", "")
    if sd.get("shareStatus") not in (1, None):
        res.status = "expired or deleted"; res.message = f"shareStatus={sd.get('shareStatus')}"; return res
    res.message = f"share says {sd.get('totalFileNum')} files, {human(sd.get('totalFileSize'))}"
    nap()
    t = _j(POST(f"{api}/get_share_access_token", json=body, headers=H))
    tok = (t.get("data") or {}).get("accessToken")
    if not tok:
        res.status = _classify(t.get("msg", ""), "needs password"); res.message = f"access_token: {json.dumps(t, ensure_ascii=False)[:200]}"; return res

    def ls(pid, path, lvl):
        cursor = None
        while True:
            if w.full(): return
            nap()
            b = {"pageSize": 100, "accessToken": tok, "orderBy": 0, "sortType": 0, "parentId": pid}
            if cursor:
                b["cursor"] = cursor
            r = _j(POST(f"{api}/get_share_page_files_list", json=b, headers=H))
            if r.get("msg") != "success":
                raise RuntimeError(f"files_list: {json.dumps(r, ensure_ascii=False)[:200]}")
            d = r.get("data") or {}
            items = d.get("list") or []
            subs = []
            for it in items:
                if w.full(): break
                isdir = it.get("resType") == 2  # resType 1=file, 2=folder
                size = it.get("fileSize", it.get("size"))
                res.add(path or "/", it["fileName"], "folder" if isdir else "file", None if isdir else size)
                if isdir:
                    subs.append((it["fileId"], f"{path}/{it['fileName']}"))
            for p2, p in subs:
                if lvl < depth:
                    ls(p2, p, lvl + 1)
            total = int(d.get("total") or 0)
            got = d.get("cursor")
            if not items or not got or len(items) < 100 or (isinstance(got, int) and got >= total):
                return
            cursor = got
    ls("", "", 0)
    return res


# ---------------- headless-browser drives (page's own JS makes the calls) ----------------
def _browser(proxy):
    from playwright.sync_api import sync_playwright
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/workspace/scrapling-env/ms-playwright")
    pw = sync_playwright().start()
    b = pw.chromium.launch(headless=True, proxy={"server": proxy} if proxy else None)
    ctx = b.new_context(locale="zh-CN", user_agent=UA, viewport={"width": 1400, "height": 900})
    return pw, b, ctx.new_page()


def xunlei(url, pwd, depth, cap):
    """迅雷云盘: api-pan.xunlei.com/drive/v1/share needs an x-captcha-token that the page obtains via
    xluser-ssl.xunlei.com/v1/shield/captcha/init with a JS-computed captcha_sign. We do NOT compute that;
    we render the share page in headless Chromium, let the page's own JS make its calls, read the JSON
    responses the page receives, and click folders in the DOM to recurse. Box IP needs the proxy."""
    res = Result("迅雷云盘"); w = Walker(res, depth, cap)
    sid = re.search(r"/s/([0-9A-Za-z_\-]+)", url).group(1)
    pwd = pwd or _qs(url, "pwd")
    proxy = PROXY or os.environ.get("XUNLEI_PROXY") or None
    pw, b, pg = _browser(proxy)
    try:
        is_root = lambda r: "api-pan.xunlei.com/drive/v1/share?" in r.url and f"share_id={sid}" in r.url
        with pg.expect_response(is_root, timeout=60000) as ri:
            pg.goto(f"https://pan.xunlei.com/s/{sid}" + (f"?pwd={pwd}" if pwd else ""), timeout=60000)
        root = ri.value.json()
        if root.get("error"):
            res.status = _classify(root.get("error_description", "") + root.get("error", ""))
            res.message = f"{root.get('error')} {root.get('error_description')}"; return res
        st = root.get("share_status")
        if st != "OK":
            res.message = f"share_status={st} {root.get('share_status_text', '')}"
            res.status = "needs password" if "PASS_CODE" in str(st) else "expired or deleted"
            return res
        res.title = root.get("title") or ""

        def more(first_json, pred):
            """collect a folder listing; scroll to make the page fetch further pages."""
            files = list(first_json.get("files") or [])
            tok = first_json.get("next_page_token") or ""
            while tok and not w.full():
                try:
                    with pg.expect_response(lambda r: pred(r) and "page_token=" + quote(tok) in r.url or (pred(r) and f"page_token={tok}" in r.url), timeout=20000) as nr:
                        for _ in range(6):
                            pg.mouse.move(700, 600); pg.mouse.wheel(0, 4000); pg.wait_for_timeout(400)
                    j = nr.value.json(); files += j.get("files") or []; tok = j.get("next_page_token") or ""
                except Exception:
                    res.message += f" (could not load page after {len(files)} items)"; break
            return files

        def emit(files, path, lvl, crumbs):
            subs = []
            for it in files:
                if w.full(): break
                isdir = it.get("kind") == "drive#folder"
                res.add(path or "/", it["name"], "folder" if isdir else "file", None if isdir else it.get("size"))
                if isdir:
                    subs.append(it)
            for it in subs:
                if lvl >= depth or w.full(): continue
                nap()
                pred = lambda r, i=it["id"]: "/drive/v1/share/detail" in r.url and f"parent_id={i}" in r.url
                try:
                    with pg.expect_response(pred, timeout=30000) as dr:
                        pg.locator("[class*=SourceListItem__name]").get_by_text(it["name"], exact=True).first.click(timeout=15000)
                    j = dr.value.json()
                except Exception as ex:
                    res.message += f" [skip {path}/{it['name']}: {type(ex).__name__}]"; continue
                if j.get("share_status") not in (None, "OK"):
                    res.message += f" [{it['name']}: {j.get('share_status')}]"
                else:
                    emit(more(j, pred), f"{path}/{it['name']}", lvl + 1, crumbs + [it["name"]])
                pg.go_back(); pg.wait_for_timeout(1500)
                if files:
                    try:
                        pg.locator("[class*=SourceListItem__name]").get_by_text(files[0]["name"], exact=True).first.wait_for(timeout=20000)
                    except Exception:
                        pass
        emit(more(root, is_root), "", 0, [])
    finally:
        b.close(); pw.stop()
    return res


def yidong(url, pwd, depth, cap):
    """移动云盘/和彩云: share API (share-kd-njs.yun.139.com .../IOutLink/getOutLinkInfoV6) uses an encrypted body and
    an mcloud-sign header computed in JS. We do NOT reproduce that; we render yun.139.com/shareweb in headless
    Chromium and read the file table from the DOM, clicking folders / breadcrumbs to recurse."""
    res = Result("移动云盘"); w = Walker(res, depth, cap)
    m = re.search(r"/i/([0-9A-Za-z]+)", url) or re.search(r"[?&]linkID=([0-9A-Za-z]+)", url) or re.search(r"/m/i\?([0-9A-Za-z]+)", url)
    if not m:
        res.status = "error"; res.message = "cannot find linkID in url"; return res
    lid = m.group(1)
    pwd = pwd or _qs(url, "pwd", "code")
    pw, b, pg = _browser(PROXY)
    # folder rows carry .document_table_list_item, file rows are plain divs; both contain a .document_table_list_name cell
    ROWS = "() => [...document.querySelectorAll('.document_table_list .document_table_list_name')].map(n => [...n.parentElement.children].map(c => c.innerText.trim()))"

    def wait_list(timeout=30):
        t0 = time.time()
        while time.time() - t0 < timeout:
            txt = pg.inner_text("body")
            mm = re.search(r"共\s*(\d+)\s*项", txt)
            if mm:
                return int(mm.group(1)), txt
            if re.search(r"(已失效|不存在|已取消|已过期|违规|已被删除|无法访问)", txt):
                return -1, txt
            if re.search(r"(提取码|请输入.{0,4}码)", txt) and pg.locator("input").count():
                return -2, txt
            pg.wait_for_timeout(700)
        return None, pg.inner_text("body")

    def read_rows(total):
        rows, stable = pg.evaluate(ROWS), 0
        while len(rows) < total and stable < 4:
            pg.mouse.move(700, 600); pg.mouse.wheel(0, 3000); pg.wait_for_timeout(900)
            n = pg.evaluate(ROWS)
            stable = stable + 1 if len(n) == len(rows) else 0
            rows = n
        return rows

    try:
        pg.goto(f"https://yun.139.com/shareweb/#/w/i/{lid}", timeout=60000, wait_until="domcontentloaded")
        total, txt = wait_list(40)
        if total == -2:
            if not pwd:
                res.status = "needs password"; res.message = "page asks for 提取码"; return res
            pg.locator("input").first.fill(pwd); pg.keyboard.press("Enter"); pg.wait_for_timeout(1500)
            for lbl in ["确定", "提取文件", "确认"]:
                if pg.get_by_text(lbl, exact=True).count():
                    pg.get_by_text(lbl, exact=True).first.click(); break
            total, txt = wait_list(30)
        if total is None or total < 0:
            line = re.search(r"[^\n]*(失效|不存在|取消|过期|违规|删除|无法访问|提取码)[^\n]*", txt)
            res.status = "expired or deleted" if total == -1 else ("needs password" if total == -2 else "error")
            res.message = (line.group(0).strip() if line else txt.strip()[:120].replace("\n", " ")); return res
        mt = re.search(r"\n(.+)的分享", txt)
        res.title = (mt.group(1).strip() + "的分享") if mt else ""

        def ls(path, lvl, total):
            rows = read_rows(total)
            subs = []
            for r in rows:
                if w.full(): break
                name = r[0].split("\n")[0].strip(); size = r[2] if len(r) > 2 else ""
                ms = re.fullmatch(r"([\d.]+)\s*([KMGT]?)B?", size.upper()) if size not in ("-", "") else None
                if ms:  # page shows e.g. "659.46MB"; convert to approx bytes so output matches other drives
                    size = int(float(ms.group(1)) * 1024 ** " KMGT".index(ms.group(2) or " "))
                isdir = size in ("-", "")
                res.add(path or "/", name, "folder" if isdir else "file", None if isdir else size)
                if isdir:
                    subs.append(name)
            for name in subs:
                if lvl >= depth or w.full(): continue
                nap()
                try:
                    pg.locator(".document_table_list_name .touch-div span").get_by_text(name, exact=True).first.click(timeout=15000)
                    pg.locator(".document_title_item_active").get_by_text(name, exact=True).wait_for(timeout=20000)
                    pg.wait_for_timeout(800)
                    t2, _ = wait_list(30)
                except Exception as ex:
                    res.message += f" [skip {path}/{name}: {type(ex).__name__}]"; continue
                if t2 and t2 > 0:
                    ls(f"{path}/{name}", lvl + 1, t2)
                crumbs = pg.locator(".document_title_item_text")
                crumbs.nth(lvl).click(); pg.wait_for_timeout(1500); wait_list(20)
        ls("", 0, total)
    finally:
        b.close(); pw.stop()
    return res


HOSTS = [("quark.cn", quark), ("aliyundrive", aliyun), ("alipan", aliyun), ("baidu.com", baidu),
         ("xunlei.com", xunlei), ("uc.cn", uc), ("189.cn", tianyi), ("115", pan115), ("123", pan123),
         ("139.com", yidong), ("guangyapan", guangya)]


def list_share(url, pwd="", depth=3, cap=500):
    url = url.strip()
    host = urlparse(url).netloc.lower()
    fn = next((f for k, f in HOSTS if k in host), None)
    if not fn:
        r = Result(host); r.status = "error"; r.message = "unsupported host"; return r
    try:
        r = fn(url, pwd, depth, cap)
        if not r.title and r.entries:
            r.title = r.entries[0]["path"].strip("/").split("/")[0]
        return r
    except Exception as ex:
        r = Result(host); r.status = "error"; r.message = f"{type(ex).__name__}: {str(ex)[:300]}"; return r


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("url"); ap.add_argument("pwd", nargs="?", default="")
    ap.add_argument("--code", default="", help="extraction code (same as positional pwd / ?pwd=)")
    ap.add_argument("--depth", type=int, default=3); ap.add_argument("--cap", type=int, default=500)
    ap.add_argument("--csv"); ap.add_argument("--proxy", default=None, help="e.g. http://127.0.0.1:8791")
    a = ap.parse_args()
    PROXY = a.proxy
    t0 = time.time()
    res = list_share(a.url, a.code or a.pwd, a.depth, a.cap)
    print(f"# {res.drive} | status={res.status} | title={res.title} | entries={len(res.entries)}"
          f"{' (truncated)' if res.truncated else ''} | {time.time() - t0:.1f}s {res.message}")
    for e in res.entries:
        print(f"{e['type']:6} {human(e['size']):>10}  {e['path'].rstrip('/')}/{e['name']}")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(["路径", "名称", "类型", "大小"])
            for e in res.entries:
                w.writerow([e["path"], e["name"], e["type"], human(e["size"])])
