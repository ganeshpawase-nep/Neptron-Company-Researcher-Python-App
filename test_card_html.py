import asyncio
from playwright.async_api import async_playwright
import json

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        await page.goto('https://duckduckgo.com/?q=SJ+SURFINE+PVT.LTD.+company+profile+industry%2C+sector%2C+address%2C+employees+count%2C+founded%2C+contact+email%2C+website%2C+LinkedIn%2C+Contact+number')
        await page.wait_for_timeout(2000)
        
        # Click the Search Assist button
        await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('a, button, [role="button"], [role="link"]'));
            const btn = btns.find(b => /Search\\s+Assist/i.test(b.innerText));
            if (btn) btn.click();
        }""")
        
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
                
                const low = text.toLowerCase();
                if (low.includes('optional feature') ||
                    low.includes('anonymously generates') ||
                    (low.includes('scans the web') && low.includes('search settings'))) continue;
                    
                const head = low.slice(0, 300);
                const navTerms = ['images', 'videos', 'news', 'maps'];
                const navHits = navTerms.filter(t => head.includes(t)).length;
                if (navHits >= 3) continue;
                
                results.push(e.outerHTML.substring(0, 800));
            }
            return results;
        }"""
        blocks = await page.evaluate(js_code)
        print('MATCHES AFTER CLICKING:')
        for b in blocks:
            print("---")
            print(b)
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
