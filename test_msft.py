import asyncio
from playwright.async_api import async_playwright
import urllib.parse

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        query = 'Microsoft company profile industry, sector, address, employees count, founded'
        url = f'https://duckduckgo.com/?q={urllib.parse.quote(query)}'
        await page.goto(url)
        await page.wait_for_timeout(3000)
        
        # Click the Search Assist button
        clicked = await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('a, button, [role="button"], [role="link"]'));
            const btn = btns.find(b => /Search\\s+Assist/i.test(b.innerText || b.textContent));
            if (btn) {
                btn.click();
                return true;
            }
            return false;
        }""")
        
        if not clicked:
            print('Search Assist button not found on generic query either!')
            
        await page.wait_for_timeout(5000)
        
        # Now dump the entire HTML of the body to see what's there
        html = await page.content()
        with open('page_html.html', 'w', encoding='utf-8') as f:
            f.write(html)
        print('Wrote page_html.html')
        
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
