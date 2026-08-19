import asyncio
import time
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        await page.goto('https://duckduckgo.com/?q=SJ+SURFINE+PVT.LTD.+company+profile+industry%2C+sector%2C+address%2C+employees+count%2C+founded%2C+contact+email%2C+website%2C+LinkedIn%2C+Contact+number')
        
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
                
                results.push(e.outerHTML.substring(0, 500));
            }
            return results;
        }"""
        
        start_time = time.time()
        found = False
        while time.time() - start_time < 15:
            blocks = await page.evaluate(js_code)
            if blocks:
                print(f"[{time.time() - start_time:.2f}s] MATCHED:", blocks)
                found = True
                break
            await asyncio.sleep(0.5)
            
        if not found:
            print("Never matched.")
            
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
