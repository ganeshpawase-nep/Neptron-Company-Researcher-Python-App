import asyncio
from playwright.async_api import async_playwright
from search.search_engine import SearchEngine
from config.settings import Settings

async def main():
    settings = Settings()
    # Ensure it doesn't close immediately
    settings.headless = False
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        engine = SearchEngine(settings, context)
        # Use the exact query from the user's log
        query = "YASH CORPORATION company profile industry, sector, address, employees count, founded, contact email, website, LinkedIn, Contact number"
        
        print(f"Running query: {query}")
        result, _ = await engine.search_query_with_assist(query)
        
        print("====== RESULT ======")
        print(result)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
