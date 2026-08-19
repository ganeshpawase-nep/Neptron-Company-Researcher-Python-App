import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto('https://duckduckgo.com/?q=Microsoft+company')
        await page.wait_for_timeout(3000)
        
        # Dump the first result
        html = await page.evaluate("""() => {
            const firstRes = document.querySelector('[data-testid="result"]');
            if (firstRes) return firstRes.outerHTML;
            return 'No [data-testid="result"] found!';
        }""")
        print('Result tag:', html[:100] if html else 'None')
        
        # What is the tag of the main results wrapper?
        tag = await page.evaluate("""() => {
            const firstRes = document.querySelector('[data-testid="result"]');
            if (firstRes) return firstRes.tagName;
            const article = document.querySelector('article');
            if (article) return 'Found article!';
            return 'No article either';
        }""")
        print('Tag:', tag)
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
