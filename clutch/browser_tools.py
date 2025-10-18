from typing import Tuple
from decouple import config

from playwright.async_api import Browser, BrowserContext, async_playwright

BROWSERLESS_WS_ENDPOINT = config("BROWSERLESS_WS_ENDPOINT")
RENOWN_AUTH_TOKEN = config("RENOWN_AUTH_TOKEN")
if not RENOWN_AUTH_TOKEN:
    raise ValueError("RENOWN_AUTH_TOKEN is not set")

ENV = config("ENV", default="dev")


def should_use_remote() -> bool:
    if ENV == "dev":
        return False
    return True


async def setup_browser(
    include_cookies: bool = True,
) -> Tuple[async_playwright, Browser, BrowserContext]:
    """Initialize and return playwright, browser, and context instances."""
    playwright = await async_playwright().start()

    if should_use_remote():
        browser = await playwright.chromium.connect_over_cdp(BROWSERLESS_WS_ENDPOINT)
        context = (
            browser.contexts[0] if browser.contexts else await browser.new_context()
        )
    else:
        # Launch browser with args to use /tmp for cache and user data
        # Run headless in production, with GUI in dev
        is_headless = ENV != "dev"
        browser = await playwright.chromium.launch(
            headless=is_headless,
            slow_mo=100 if should_use_remote() else None,
            args=[
                "--disk-cache-dir=/tmp/chromium-cache",
                "--user-data-dir=/tmp/chromium-user-data",
                "--disable-dev-shm-usage",  # Use /tmp instead of /dev/shm
            ],
        )
        context = await browser.new_context()

    if include_cookies:
        auth_token = {
            "name": "auth_token",
            "value": RENOWN_AUTH_TOKEN,
            "domain": ".renown.gg",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }
        await context.add_cookies([auth_token])

    return playwright, browser, context


async def cleanup_browser(playwright: async_playwright, browser: Browser) -> None:
    """Clean up browser and playwright resources."""
    await browser.close()
    await playwright.stop()


async def check_renown_login(
    playwright: async_playwright, browser: Browser, context: BrowserContext
) -> bool:
    """Check if user is logged into renown.gg."""
    try:
        page = await context.new_page()
        await page.goto("https://renown.gg/play", timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=60000)

        try:
            login_message = await page.wait_for_selector("div.text", timeout=5000)
            if login_message:
                message_text = await login_message.inner_text()
                return message_text != "This page is only available to logged-in users"
            return True
        except Exception as e:
            print(f"Error checking login status: {e}")
            return False
    finally:
        await page.close()
