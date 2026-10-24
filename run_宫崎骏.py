import csv, json, sys, datetime, zoneinfo
sys.path.insert(0, "/workspace/scrapes")
from pan_list import list_share, human
rows = list(csv.DictReader(open("slowread_宫崎骏合集.csv", encoding="utf-8-sig")))
out, summ = [], []
for i, r in enumerate(rows, 1):
    res = list_share(r["share_url"], r["extraction_code"], 3, 500)
    ts = datetime.datetime.now(zoneinfo.ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M:%S %Z")
    st = res.status + (" (truncated at 500)" if res.truncated else "")
    base = [i, r["title"], r["drive"], r["share_url"], st]
    if not res.entries:
        out.append(base + ["", "", "", "", res.message, ts])
    for e in res.entries:
        out.append(base + [e["path"], e["name"], "文件夹" if e["type"] == "folder" else "文件", human(e["size"]), res.message, ts])
    files = [e for e in res.entries if e["type"] == "file"]
    summ.append({"i": i, "title": r["title"], "share_title": res.title, "status": res.status, "msg": res.message,
                 "truncated": res.truncated, "entries": len(res.entries), "files": len(files),
                 "folders": len(res.entries) - len(files), "listed_bytes": sum(e["size"] or 0 for e in files),
                 "top": [(e["type"], e["name"]) for e in res.entries if e["path"] == "/"], "time": ts})
    print(json.dumps(summ[-1], ensure_ascii=False), flush=True)
    with open("宫崎骏合集_网盘文件列表.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["序号", "标题", "网盘", "链接", "状态", "路径", "名称", "类型", "大小", "备注", "抓取时间"])
        w.writerows(out)
json.dump(summ, open("/workspace/scrapes/宫崎骏合集_summary.json", "w"), ensure_ascii=False, indent=1)
