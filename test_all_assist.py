import asyncio
from playwright.async_api import async_playwright
import json

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        await page.goto('https://duckduckgo.com/?q=SJ+SURFINE+PVT.LTD.+company+profile+industry%2C+sector%2C+address%2C+employees+count%2C+founded%2C+contact+email%2C+website%2C+LinkedIn%2C+Contact+number')
        await page.wait_for_timeout(5000)
        
        js_code = """() => {
            const els = document.querySelectorAll('div,section,article');
            const results = [];
            for (const e of els) {
                if (e.querySelector('input[name="q"]')) continue;
                const text = (e.innerText || '').trim();
                if (!text || text.length < 120 || text.length > 12000) continue;
                const r = e.getBoundingClientRect();
                if (r.width < 250 || r.height < 80 || r.height > 800) continue;
                const pos = text.toLowerCase().indexOf('search assist');
                if (pos < 0 || pos > 200) continue;
                
                results.push({tag: e.tagName, len: text.length, textSnippet: text.substring(0, 150).replace(/\\n/g, ' ')});
            }
            return results;
        }"""
        blocks = await page.evaluate(js_code)
        print('MATCHES WITHOUT EXCLUSIONS:')
        for b in blocks:
            print(json.dumps(b))
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
