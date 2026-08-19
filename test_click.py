"""
End-to-end test: Replicate the exact flow from search_engine.py
Wait 5s, find <A href="...assist=true">, click at exact pixel coords, wait for More button.
"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        
        query = "YASH CORPORATION company profile industry, sector, address"
        await page.goto(f"https://duckduckgo.com/?q={query}")
        print("Page loaded, waiting 5s...")
        await page.wait_for_timeout(5000)
        
        # Find the exact <A> tag with assist=true
        coords = await page.evaluate("""() => {
            const links = document.querySelectorAll('a');
            for (const a of links) {
                const href = a.getAttribute('href') || '';
                if (href.includes('assist=true')) {
                    const r = a.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        return {
                            x: Math.round(r.x + r.width / 2),
                            y: Math.round(r.y + r.height / 2),
                            method: 'href-assist-true',
                            href: href.slice(0, 100)
                        };
                    }
                }
            }
            return null;
        }""")
        
        if coords:
            print(f"Found Search Assist link at ({coords['x']}, {coords['y']}) via {coords['method']}")
            print(f"  href: {coords['href']}")
            
            # Raw physical mouse click
            await page.mouse.move(coords['x'], coords['y'])
            await asyncio.sleep(0.2)
            await page.mouse.down()
            await asyncio.sleep(0.1)
            await page.mouse.up()
            print("Clicked! Waiting 3s for card to render...")
            await page.wait_for_timeout(3000)
            
            # Check page state
            state = await page.evaluate("""() => {
                const body = document.body.innerText || '';
                return {
                    hasMoreButton: /\\bMore\\b/.test(body),
                    hasSearchAssistText: body.includes('Search Assist'),
                    bodyLen: body.length
                };
            }""")
            print(f"After click: {state}")
            print("SUCCESS!" if state['hasMoreButton'] else "FAILED - More button not found")
        else:
            print("FAILED - No <A> tag with assist=true found!")
        
        await page.wait_for_timeout(3000)
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
