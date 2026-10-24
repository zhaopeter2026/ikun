import asyncio, json
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True, proxy=None)
        ctx=await b.new_context(locale="zh-CN", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
        pg=await ctx.new_page(); caps=[]
        async def onr(r):
            if "api-pan.xunlei.com/drive/v1/share" in r.url:
                try: caps.append((r.url, await r.text()))
                except: pass
        pg.on("response", lambda r: asyncio.ensure_future(onr(r)))
        await pg.goto("https://pan.xunlei.com/s/VOhFUMhC1NsFO6D8meuqkcgLA1?pwd=7kbf", timeout=60000)
        await pg.wait_for_timeout(9000)
        html=await pg.content()
        open("xl_dom.html","w").write(html)
        # find element with text 三体
        el=pg.get_by_text("三体", exact=True).first
        print(await el.evaluate("e=>e.outerHTML.slice(0,300)"))
        print(await el.evaluate("e=>{let x=e;let out=[];for(let i=0;i<6&&x;i++){out.push(x.tagName+'.'+x.className);x=x.parentElement};return out}"))
        await el.click(); await pg.wait_for_timeout(6000)
        print(pg.url)
        for u,t in caps: print(u[:250]); print(t[:600]); print()
        await pg.screenshot(path="xl2.png")
        await b.close()
asyncio.run(main())
