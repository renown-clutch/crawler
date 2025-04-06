import asyncio
from typing import Optional

from playwright.async_api import BrowserContext, Error, Page

from clutch.core import cleanup_browser, setup_browser
from clutch.models import UserCrawlTask


async def update_or_create_user_task(
    steam_id: int, page_html: str, processed: bool, error_occured: bool
) -> None:
    """Update existing user task or create a new one if it doesn't exist."""
    existing_task = (
        UserCrawlTask.select().where(UserCrawlTask.steam_id == steam_id).first()
    )

    if existing_task:
        existing_task.page_html = page_html
        existing_task.processed = processed
        existing_task.error_occured = error_occured
        existing_task.save()
    else:
        UserCrawlTask.create(
            steam_id=steam_id,
            page_html=page_html,
            processed=processed,
            error_occured=error_occured,
        )


async def crawl_single_user_with_page(steam_id: int, page: Page) -> str:
    """Crawl a single user profile page using an existing page."""
    try:
        url = f"https://renown.gg/profile/{steam_id}"
        print(f"Navigating to {url}")
        await page.goto(url, timeout=60000)
        print("Waiting for networkidle")
        await page.wait_for_load_state("networkidle", timeout=60000)
        print("Networkidle loaded")

        print("Getting page content")
        html_content = await page.content()

        print("Processing page content")
        not_found_selector = 'div.row:has(h1:text("404")) > div.text:text("This page could not be found")'
        not_found = await page.query_selector(not_found_selector)
        if not_found:
            await update_or_create_user_task(
                steam_id=steam_id,
                page_html=html_content,
                processed=False,
                error_occured=True,
            )
            return f"User {steam_id} does not exist (404)"

        await update_or_create_user_task(
            steam_id=steam_id,
            page_html=html_content,
            processed=False,
            error_occured=False,
        )

        return f"Successfully crawled user {steam_id}"
    except Error as e:
        if "Target page, context or browser has been closed" in str(e):
            raise  # Re-raise to trigger context recreation
        return f"Error crawling user {steam_id}: {str(e)}"
    except Exception as e:
        return f"Error crawling user {steam_id}: {str(e)}"


async def crawl_single_user(steam_id: int, context: BrowserContext) -> str:
    """Crawl a single user profile page."""
    page = None
    try:
        print("Creating new page")
        page = await context.new_page()
        return await crawl_single_user_with_page(steam_id, page)
    finally:
        if page:
            try:
                print("Closing page")
                await page.close()
            except Exception as e:
                print(f"Error closing page: {e}")


async def crawl_user(
    steam_id: int, context: Optional[BrowserContext] = None, page: Optional[Page] = None
) -> str:
    """Crawl a single user profile and return the result."""
    if page is not None:
        return await crawl_single_user_with_page(steam_id, page)
    elif context is None:
        playwright, browser, context = await setup_browser()
        try:
            return await crawl_single_user(steam_id, context)
        finally:
            await cleanup_browser(playwright, browser)
    else:
        return await crawl_single_user(steam_id, context)


def main():
    """Main function to run the user crawler."""
    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m clutch.crawl_user <steam_id>")
        sys.exit(1)

    try:
        steam_id = int(sys.argv[1])
    except ValueError:
        print("Error: Steam ID must be a valid integer")
        sys.exit(1)

    result = asyncio.run(crawl_user(steam_id))
    print(result)


if __name__ == "__main__":
    main()
