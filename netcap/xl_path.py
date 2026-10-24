import asyncio
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True, proxy=None)
        pg=await (await b.new_context(locale="zh-CN")).new_page(); caps=[]
        pg.on("response", lambda r: caps.append(r.url) if "drive/v1/share" in r.url else None)
        await pg.goto("https://pan.xunlei.com/s/VOhFUMhC1NsFO6D8meuqkcgLA1?pwd=7kbf&path=%2F%E4%B8%89%E4%BD%93", timeout=60000)
        await pg.wait_for_timeout(10000)
        for u in caps: print(u[:200])
        print((await pg.inner_text("body"))[:600])
        await b.close()
asyncio.run(main())
