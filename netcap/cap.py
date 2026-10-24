import sys, json, asyncio, os
from playwright.async_api import async_playwright
url=sys.argv[1]; out=sys.argv[2]; proxy=os.environ.get("CAP_PROXY")
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True, proxy={"server":proxy} if proxy else None)
        ctx=await b.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36", locale="zh-CN")
        pg=await ctx.new_page(); recs=[]
        async def onresp(r):
            rq=r.request
            if rq.resource_type in ("xhr","fetch"):
                try: body=(await r.text())[:3000]
                except Exception as e: body=f"<{e}>"
                recs.append({"m":rq.method,"u":rq.url,"st":r.status,"hdr":{k:v for k,v in rq.headers.items() if k.lower() not in("user-agent","accept-language","sec-ch-ua","sec-ch-ua-platform","sec-ch-ua-mobile")},"post":rq.post_data,"body":body})
        pg.on("requestfailed",lambda rq: recs.append({"m":rq.method,"u":rq.url,"st":"FAIL "+str(rq.failure),"hdr":{},"post":None,"body":""}))
        pg.on("response",lambda r: asyncio.ensure_future(onresp(r)))
        try: await pg.goto(url, wait_until="networkidle", timeout=45000)
        except Exception as e: print("goto",e)
        await pg.wait_for_timeout(int(os.environ.get("CAP_WAIT","4000")))
        txt=await pg.inner_text("body")
        json.dump({"recs":recs,"text":txt[:3000],"final":pg.url},open(out,"w"),ensure_ascii=False,indent=1)
        await pg.screenshot(path=out+".png")
        await b.close()
asyncio.run(main())
