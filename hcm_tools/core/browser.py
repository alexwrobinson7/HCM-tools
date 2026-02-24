"""Browser session lifecycle — launch, pause for manual login, tear down."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

logger = logging.getLogger(__name__)


class BrowserSession:
    """Async context manager that owns a Playwright browser session."""

    def __init__(
        self,
        headless: bool = False,
        slow_mo: int = 50,
        downloads_path: Optional[str] = None,
        viewport: Optional[dict] = None,
    ):
        self.headless = headless
        self.slow_mo = slow_mo
        self.downloads_path = downloads_path
        self.viewport = viewport or {"width": 1280, "height": 900}

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def __aenter__(self) -> "BrowserSession":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
        )

        context_kwargs: dict = {
            "viewport": self.viewport,
            "accept_downloads": True,
        }
        if self.downloads_path:
            Path(self.downloads_path).mkdir(parents=True, exist_ok=True)

        self._context = await self._browser.new_context(**context_kwargs)
        self._page = await self._context.new_page()
        logger.debug("Browser session started.")
        return self

    async def __aexit__(self, *_) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.debug("Browser session closed.")

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("BrowserSession not started — use as async context manager.")
        return self._page

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> None:
        logger.info(f"Navigating to {url}")
        await self.page.goto(url, wait_until=wait_until)

    async def pause_for_login(self) -> None:
        """Block until the user confirms they have completed manual login / MFA."""
        print()
        print("=" * 60)
        print("  MANUAL LOGIN REQUIRED")
        print("  Complete login + MFA/SSO in the browser window.")
        print("  When you are on the authenticated home page,")
        print("  press ENTER here to resume automation.")
        print("=" * 60)
        # Run blocking input() in a thread so the event loop stays alive.
        await asyncio.get_event_loop().run_in_executor(None, input, "  Press ENTER to continue... ")
        print()
        logger.info("User confirmed login — resuming automation.")
