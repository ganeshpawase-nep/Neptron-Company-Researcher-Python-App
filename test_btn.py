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
            const btn = Array.from(document.querySelectorAll('a, button, [role="button"], [role="link"]')).find(b => /Search\\s+Assist/i.test(b.innerText));
            if (!btn) return 'NO BUTTON';
            let p = btn;
            for(let i=0; i<4; i++) {
                if(p.parentElement) p = p.parentElement;
            }
            return p.outerHTML;
        }"""
        html = await page.evaluate(js_code)
        
        with open('button_html.txt', 'w', encoding='utf-8') as f:
            f.write(html)
            
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
