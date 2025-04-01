import os
import asyncio
from playwright.async_api import async_playwright, Browser, Page, BrowserContext
from telegram import Bot
import json
from typing import Optional, Dict, Any, Tuple

BROWSERLESS_WS_ENDPOINT = os.getenv("BROWSERLESS_WS_ENDPOINT")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

bot = Bot(token=TELEGRAM_BOT_TOKEN)

def is_headless() -> bool:
    val = os.getenv("HEADLESS", "false").lower()
    return val not in ["false", "no", "0"]

def should_use_remote() -> bool:
    return os.getenv("USE_REMOTE", "false").lower() == "true"

async def setup_browser() -> Tuple[async_playwright, Browser, BrowserContext]:
    """Initialize and return playwright, browser, and context instances."""
    playwright = await async_playwright().start()
    
    if should_use_remote():
        browser = await playwright.chromium.connect_over_cdp(BROWSERLESS_WS_ENDPOINT)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
    else:
        browser = await playwright.chromium.launch(headless=is_headless(), slow_mo=100)
        context = await browser.new_context()
    
    return playwright, browser, context

async def cleanup_browser(playwright: async_playwright, browser: Browser) -> None:
    """Clean up browser and playwright resources."""
    await browser.close()
    await playwright.stop()

async def set_auth_token(context: BrowserContext, auth_token: Optional[Dict[str, Any]] = None) -> None:
    """Set the authentication token cookie if provided."""
    if auth_token:
        print("Setting auth_token cookie...")
        await context.add_cookies([auth_token])
        print("Auth token cookie set successfully")

async def check_login_status(page: Page) -> bool:
    """Check if the user is already logged in."""
    try:
        logged_in_element = await page.wait_for_selector(".OpenID_loggedInText", timeout=5000)
        return bool(logged_in_element)
    except:
        return False

async def handle_successful_login(page: Page) -> Optional[Dict[str, Any]]:
    """Handle the post-login process and return the auth token."""
    print("User successfully logged in!")
    green_button = await page.wait_for_selector(".btn_green_white_innerfade", timeout=60000)
    
    if green_button:
        print("Clicking green button...")
        await green_button.click()
        await page.wait_for_load_state("networkidle", timeout=60000)
        print("Page loaded after clicking green button")
        
        cookies = await page.context.cookies()
        auth_token = next((cookie for cookie in cookies if cookie["name"] == "auth_token"), None)
        
        if auth_token:
            print("\nAuth Token Cookie Data:")
            print(json.dumps(auth_token, indent=2))
            return auth_token
        else:
            print("Auth token cookie not found!")
    return None

async def check_renown_login(page: Page) -> bool:
    """Check if user is logged into renown.gg."""
    try:
        login_message = await page.wait_for_selector("div.text", timeout=5000)
        if login_message:
            message_text = await login_message.inner_text()
            return message_text != "This page is only available to logged-in users"
        return True
    except Exception as e:
        print(f"Error checking login status: {e}")
        return False

async def handle_qr_code_login(page: Page) -> Optional[Dict[str, Any]]:
    """Handle the QR code login process."""
    previous_qr_content = None
    
    while True:
        if await check_login_status(page):
            return await handle_successful_login(page)

        try:
            qr_container = await page.wait_for_selector("*[style*='grid-template-columns: repeat(41, 1fr)']", timeout=60000)
            await page.wait_for_function(
                '!document.querySelector("*[style*=\'grid-template-columns: repeat(41, 1fr)\']")?.style.filter?.includes("blur")',
                timeout=60000
            )
            await page.wait_for_timeout(200)

            current_qr_content = await qr_container.evaluate('el => el.innerHTML')
            
            if current_qr_content != previous_qr_content:
                await qr_container.screenshot(path="qr.png")
                print("New QR code detected and saved as 'qr.png'")
                await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=open("qr.png", "rb"))
                os.remove("qr.png")
                previous_qr_content = current_qr_content
            
            await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"Error during QR code check: {e}")
            if await check_login_status(page):
                return await handle_successful_login(page)
            print("QR code element not found and not logged in.")
            break
    
    return None

async def login_to_renown(page: Page) -> Optional[Dict[str, Any]]:
    """Main login flow for renown.gg."""
    await page.goto("https://renown.gg/play", timeout=60000)
    await page.wait_for_load_state("networkidle", timeout=60000)
    
    if not await check_renown_login(page):
        print("User is not logged in. Proceeding with login process...")
        await page.goto("https://api.renown.gg/login", timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=60000)
        return await handle_qr_code_login(page)
    
    print("User appears to be logged in!")
    return None

async def main():
    auth_token = {
        "name": "auth_token",
        "value": "test",
        "domain": ".renown.gg",
        "path": "/",
        "expires": 1746137687.091656,
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax"
    }
    # auth_token = None

    playwright, browser, context = await setup_browser()
    
    try:
        if auth_token:
            await set_auth_token(context, auth_token)
        page = await context.new_page()
        await login_to_renown(page)
    finally:
        if not should_use_remote() and not is_headless():
            input("Press Enter to close the browser...")
        await cleanup_browser(playwright, browser)

if __name__ == "__main__":
    asyncio.run(main())
