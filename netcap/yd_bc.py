import asyncio,sys
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        pg=await (await b.new_context(locale="zh-CN", viewport={"width":1400,"height":900})).new_page()
        await pg.goto(sys.argv[1], timeout=60000, wait_until="domcontentloaded")
        await pg.wait_for_timeout(9000)
        print((await pg.inner_text("body"))[:800])
        if len(sys.argv)>2:
            await pg.locator(".document_table_list_item .touch-div span", has_text=sys.argv[2]).first.click(); await pg.wait_for_timeout(5000)
            el=pg.get_by_text("全部", exact=True).first
            print(await el.evaluate("e=>e.parentElement.parentElement.outerHTML.slice(0,1500)"))
            print(await pg.evaluate("()=>[...document.querySelectorAll('.document_table_list_item')].map(r=>[...r.children].map(c=>c.innerText.trim()))"))
        await pg.screenshot(path="yd3.png")
        await b.close()
asyncio.run(main())
