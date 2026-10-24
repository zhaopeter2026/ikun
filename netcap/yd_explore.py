import asyncio,sys
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        pg=await (await b.new_context(locale="zh-CN", viewport={"width":1400,"height":900})).new_page()
        await pg.goto(sys.argv[1], timeout=60000, wait_until="domcontentloaded")
        await pg.wait_for_timeout(9000)
        el=pg.get_by_text(sys.argv[2], exact=True).first
        print(await el.evaluate("e=>{let x=e;let out=[];for(let i=0;i<8&&x;i++){out.push(x.tagName+'.'+x.className);x=x.parentElement};return out}"))
        row=await el.evaluate("e=>{let x=e;for(let i=0;i<8;i++){x=x.parentElement; if(/row|item|list-item|tr/i.test(x.className)) return x.outerHTML.slice(0,2500)};return ''}")
        print(row)
        open("yd_dom.html","w").write(await pg.content())
        await el.click(); await pg.wait_for_timeout(6000); print(pg.url); print((await pg.inner_text("body"))[:1500])
        await pg.screenshot(path="yd2.png")
        await b.close()
asyncio.run(main())
