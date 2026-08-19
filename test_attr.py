import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto('https://duckduckgo.com/?q=Microsoft+company')
        await page.wait_for_timeout(3000)
        
        btn = await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('a, button, [role="button"], [role="link"]'));
            const btn = btns.find(b => /Search\\s+Assist/i.test(b.innerText || b.textContent));
            if (!btn) return null;
            return {
                text: btn.innerText,
                ariaExpanded: btn.getAttribute('aria-expanded'),
                ariaPressed: btn.getAttribute('aria-pressed'),
                className: btn.className,
                tagName: btn.tagName
            };
        }""")
        print(btn)
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
