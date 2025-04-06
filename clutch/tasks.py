import asyncio
from datetime import timedelta

from .celery import app
from .core import cleanup_browser, setup_browser
from .crawl_banlist import process_new_bans
from .models import MatchCrawlTask


async def update_or_create_match_task(
    match_id: int,
    page_html: str,
    processed: bool,
    error_occured: bool,
    match_status: str,
) -> None:
    """Update existing match task or create a new one if it doesn't exist."""
    existing_task = (
        MatchCrawlTask.select().where(MatchCrawlTask.match_id == match_id).first()
    )

    if existing_task:
        existing_task.page_html = page_html
        existing_task.processed = processed
        existing_task.error_occured = error_occured
        existing_task.match_status = match_status
        existing_task.save()
    else:
        MatchCrawlTask.create(
            match_id=match_id,
            page_html=page_html,
            processed=processed,
            error_occured=error_occured,
            match_status=match_status,
        )


async def crawl_single_match(match_id: int, context):
    """Crawl a single match page."""

    print("Creating new page")
    page = await context.new_page()
    try:
        url = f"https://renown.gg/match/{match_id}"
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
            return f"Match {match_id} does not exist (404)"

        # Extract match status
        status_selector = "div.status div.status-wrapper"
        status_element = await page.query_selector(status_selector)
        match_status = "unknown"
        if status_element:
            status_text = await status_element.text_content()
            match_status = status_text.strip().lower()

        await update_or_create_match_task(
            match_id=match_id,
            page_html=html_content,
            processed=False,
            error_occured=False,
            match_status=match_status,
        )

        return f"Successfully crawled match {match_id}"
    except Exception as e:
        await update_or_create_match_task(
            match_id=match_id,
            page_html="",
            processed=True,
            error_occured=True,
            match_status="error",
        )
        return f"Error crawling match {match_id}: {str(e)}"
    finally:
        await page.close()


@app.task
def crawl_match(match_id: int):
    """Crawl a single match and return the result."""

    async def crawl():
        playwright, browser, context = await setup_browser()
        try:
            return await crawl_single_match(match_id, context)
        finally:
            await cleanup_browser(playwright, browser)

    # Create a new event loop for this task
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(crawl())
    finally:
        loop.close()


@app.task
def crawl_banlist():
    """Crawl the ban list and update the database."""

    async def crawl():
        playwright, browser, context = await setup_browser()
        try:
            await process_new_bans(context)
            return "Successfully crawled ban list"
        finally:
            await cleanup_browser(playwright, browser)

    # Create a new event loop for this task
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(crawl())
    finally:
        loop.close()


# Configure periodic tasks
app.conf.beat_schedule = {
    "crawl-banlist-every-5-minutes": {
        "task": "clutch.tasks.crawl_banlist",
        "schedule": timedelta(minutes=30),
    },
}
