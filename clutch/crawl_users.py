import asyncio
import logging
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from playwright.async_api import BrowserContext, Page

from clutch.browser_tools import cleanup_browser, setup_browser
from clutch.db.models import Player, UserCrawlTask, db
from clutch.telegram_utils import send_alert

logger = logging.getLogger(__name__)


def extract_user_data_from_html(html_content: str, steam_id: int) -> Optional[dict]:
    """Extract user data from HTML content of a profile page."""
    try:
        # Extract name from the profile banner - be more specific
        name_pattern = r'<div class="profile-banner">.*?<div class="name">([^<]+)</div>'
        name_match = re.search(name_pattern, html_content, re.DOTALL)
        name = name_match.group(1) if name_match else None

        # Extract ELO from the rank badge within profile banner - be more specific
        elo_pattern = r'<div class="profile-banner">.*?<div class="rank-badge[^"]*"[^>]*>.*?<span>([0-9,]+)</span>'
        elo_match = re.search(elo_pattern, html_content, re.DOTALL)
        elo = None
        if elo_match:
            elo_str = elo_match.group(1).replace(",", "")
            try:
                elo = int(elo_str)
            except ValueError:
                elo = None

        # Extract country from flag image within profile banner
        country_pattern = r'<div class="profile-banner">.*?<div class="flag">.*?src="https://cdn\.renown\.gg/images/flags/1x1/([A-Z]{2})\.svg"'
        country_match = re.search(country_pattern, html_content, re.DOTALL)
        country = country_match.group(1) if country_match else None

        # Extract avatar URL from profile banner
        avatar_pattern = (
            r'<div class="profile-banner">.*?<img alt="Avatar"[^>]*src="([^"]+)"'
        )
        avatar_match = re.search(avatar_pattern, html_content, re.DOTALL)
        avatar_url = avatar_match.group(1) if avatar_match else None

        # Extract registration date from profile banner
        registered_pattern = r'<div class="profile-banner">.*?<div class="text">Registered since ([^<]+)</div>'
        registered_match = re.search(registered_pattern, html_content, re.DOTALL)
        registered_since = None
        if registered_match:
            try:
                # Parse date like "Mar 04, 2025"
                date_str = registered_match.group(1)
                registered_since = datetime.strptime(date_str, "%b %d, %Y").replace(
                    tzinfo=ZoneInfo("UTC")
                )
            except ValueError:
                logger.warning(
                    f"Could not parse registration date: {registered_match.group(1)}"
                )

        if not name or elo is None:
            logger.warning(f"Could not extract essential data for steam_id {steam_id}")
            return None

        return {
            "steam_id": steam_id,
            "name": name,
            "elo": elo,
            "country": country,
            "avatar_url": avatar_url,
            "registered_since": registered_since,
        }

    except Exception as e:
        logger.error(
            f"Error extracting user data from HTML for steam_id {steam_id}: {str(e)}"
        )
        return None


def create_or_update_player_from_crawl(user_data: dict) -> Player:
    """Create or update a player from crawled user data."""
    steam_id = user_data["steam_id"]

    player, created = Player.get_or_create(
        steam_id=steam_id,
        defaults={
            "name": user_data["name"],
            "elo": user_data["elo"],
            "country": user_data.get("country", "unknown"),
            "avatar_url": user_data.get("avatar_url", ""),
            "registered_since": user_data.get("registered_since"),
        },
    )

    if created:
        logger.info(
            f"Created new player {player.name} (steam_id: {steam_id}) from crawl"
        )
    else:
        # Update existing player data
        updated = False
        if player.name != user_data["name"]:
            player.name = user_data["name"]
            updated = True
        if player.elo != user_data["elo"]:
            player.elo = user_data["elo"]
            updated = True
        if player.country != user_data.get("country", "unknown"):
            player.country = user_data.get("country", "unknown")
            updated = True
        if player.avatar_url != user_data.get("avatar_url", ""):
            player.avatar_url = user_data.get("avatar_url", "")
            updated = True
        if (
            user_data.get("registered_since")
            and player.registered_since != user_data["registered_since"]
        ):
            player.registered_since = user_data["registered_since"]
            updated = True

        if updated:
            player.save()
            logger.info(
                f"Updated player {player.name} (steam_id: {steam_id}) from crawl"
            )

    return player


async def crawl_single_user(steam_id: int, page: Page) -> str:
    """Crawl a single user profile page."""
    logger.info(f"Crawling user profile for steam_id {steam_id}")

    try:
        url = f"https://renown.gg/profile/{steam_id}"
        await page.goto(url, timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=60000)

        html_content = await page.content()

        # Check if profile exists (404 check)
        not_found_selector = 'div.row:has(h1:text("404")) > div.text:text("This page could not be found")'
        not_found = await page.query_selector(not_found_selector)
        if not_found:
            logger.info(f"User profile {steam_id} does not exist (404)")
            return "404"

        # Extract user data from HTML
        user_data = extract_user_data_from_html(html_content, steam_id)

        if user_data:
            with db.atomic() as transaction:
                try:
                    # Update or create the user crawl task
                    task = (
                        UserCrawlTask.select()
                        .where(UserCrawlTask.steam_id == steam_id)
                        .first()
                    )
                    if task:
                        task.page_html = html_content
                        task.processed = True
                        task.save()
                    else:
                        task = UserCrawlTask.create(
                            steam_id=steam_id,
                            page_html=html_content,
                            processed=True,
                        )

                    # Create or update the player
                    create_or_update_player_from_crawl(user_data)
                    logger.info(f"Successfully crawled and processed user {steam_id}")
                    return "success"

                except Exception as e:
                    logger.error(f"Error processing user {steam_id}: {str(e)}")
                    transaction.rollback()
                    return "error"
        else:
            logger.warning(f"Could not extract user data for {steam_id}")
            return "no_data"

    except Exception as e:
        logger.error(f"Error crawling user {steam_id}: {str(e)}")
        if "Target page, context or browser has been closed" in str(e):
            return "context_closed"
        return "error"


async def create_user_crawl_task(steam_id: int) -> UserCrawlTask:
    """Create a new user crawl task if it doesn't exist."""
    task, created = UserCrawlTask.get_or_create(
        steam_id=steam_id,
        defaults={
            "page_html": "",
            "processed": False,
        },
    )

    if created:
        logger.info(f"Created new user crawl task for steam_id {steam_id}")
    else:
        logger.info(f"User crawl task already exists for steam_id {steam_id}")

    return task


async def process_user_crawl_tasks(context: Optional[BrowserContext] = None) -> None:
    """Process pending user crawl tasks."""
    logger.info("Starting to process user crawl tasks")

    own_context = context is None
    if own_context:
        playwright, browser, context = await setup_browser(include_cookies=True)

    try:
        # Get unprocessed user crawl tasks
        unprocessed_tasks = (
            UserCrawlTask.select()
            .where(UserCrawlTask.processed == False)
            .order_by(UserCrawlTask.created_at.asc())
        )

        task_count = unprocessed_tasks.count()
        if task_count == 0:
            logger.info("No unprocessed user crawl tasks found")
            return

        logger.info(f"Found {task_count} unprocessed user crawl tasks")

        page = await context.new_page()
        try:
            for task in unprocessed_tasks:
                logger.info(f"Processing user crawl task for steam_id {task.steam_id}")
                status = await crawl_single_user(task.steam_id, page)
                logger.info(f"Processed user {task.steam_id}: status={status}")

                # Add a small delay between requests to be respectful
                await asyncio.sleep(2)

        finally:
            await page.close()

        logger.info(f"Finished processing {task_count} user crawl tasks")

    except Exception as e:
        logger.error(f"Error processing user crawl tasks: {str(e)}")
        await send_alert(f"Error processing user crawl tasks: {str(e)}")
    finally:
        if own_context:
            await cleanup_browser(playwright, browser)


async def user_crawl_job():
    """Job function for the scheduler to process user crawl tasks."""
    logger.info("Starting user crawl job")
    try:
        await process_user_crawl_tasks()
        logger.info("User crawl job completed successfully")
    except Exception as e:
        logger.error(f"Error in user crawl job: {str(e)}")
        await send_alert(f"Error in user crawl job: {str(e)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(user_crawl_job())
