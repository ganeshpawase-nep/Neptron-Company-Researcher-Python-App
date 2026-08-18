
from pathlib import Path
from playwright.async_api import async_playwright


class BrowserManager:
    def __init__(self, settings):
        self.settings = settings
        self.playwright = None
        self.context = None
        self.page = None

    async def start(self):
        self.playwright = await async_playwright().start()
        profile = Path(self.settings.profile_dir).resolve()
        profile.mkdir(parents=True, exist_ok=True)

        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel=self.settings.browser_channel,
            headless=False,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            ignore_https_errors=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-default-browser-check",
            ],
        )

        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        self.page.set_default_timeout(self.settings.browser_timeout_ms)

    async def new_page(self):
        if not self.context:
            raise RuntimeError("Browser context is not running.")
        page = await self.context.new_page()
        page.set_default_timeout(self.settings.browser_timeout_ms)
        return page

    async def close_company_tabs(self):
        if not self.context:
            return

        pages = list(self.context.pages)
        main = self.page

        if not main or main.is_closed():
            main = pages[0] if pages else await self.context.new_page()
            main.set_default_timeout(self.settings.browser_timeout_ms)
            self.page = main

        for p in pages:
            if p is main:
                continue
            try:
                if not p.is_closed():
                    await p.close(reason="company research completed")
            except Exception:
                pass

        try:
            await main.bring_to_front()
        except Exception:
            pass

    async def close(self):
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None

        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
