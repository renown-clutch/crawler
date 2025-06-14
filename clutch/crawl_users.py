import asyncio
import logging
import re
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from playwright.async_api import BrowserContext, Page

from clutch.browser_tools import cleanup_browser, setup_browser
from clutch.db.models import FriendsCrawlTask, Player, UserCrawlTask, db
from clutch.telegram_utils import send_alert

logger = logging.getLogger(__name__)


def extract_friend_steam_ids_from_html(html_content: str) -> List[int]:
    """Extract friend steam IDs from the HTML content of a friends page."""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_content, "html.parser")

        # Find the profile-friend-list div specifically
        friends_list_div = soup.find("div", class_="profile-friend-list")

        if not friends_list_div:
            logger.debug("No profile-friend-list section found")
            return []

        # Find all profile links within the friends list div only
        profile_links = friends_list_div.find_all("a", class_="profile-link")

        steam_ids = []
        for link in profile_links:
            href = link.get("href", "")
            # Extract steam ID from href="/profile/76561198000000000"
            match = re.search(r"/profile/(\d+)", href)
            if match:
                try:
                    steam_id = int(match.group(1))
                    if steam_id not in steam_ids:
                        steam_ids.append(steam_id)
                except ValueError:
                    logger.warning(
                        f"Could not convert steam_id to int: {match.group(1)}"
                    )
                    continue

        logger.debug(
            f"Extracted {len(steam_ids)} unique friend steam IDs from friends list section"
        )
        return steam_ids

    except Exception as e:
        logger.error(f"Error extracting friend steam IDs from HTML: {str(e)}")
        return []


def extract_user_data_from_friends_page_html(
    html_content: str, steam_id: int
) -> Optional[dict]:
    """Extract user data from HTML content of a friends page (profile card section)."""
    try:
        # Extract name from the profile banner on friends page
        name_pattern = r'<div class="profile-banner">.*?<div class="name">([^<]+)</div>'
        name_match = re.search(name_pattern, html_content, re.DOTALL)
        name = name_match.group(1) if name_match else None

        # Extract ELO from the rank badge within profile banner
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
            f"Error extracting user data from friends page HTML for steam_id {steam_id}: {str(e)}"
        )
        return None


def create_or_update_player_from_crawl(user_data: dict) -> Player:
    """Create or update a player from crawled user data."""
    steam_id = user_data["steam_id"]
    original_name = user_data["name"]

    try:
        player, created = Player.get_or_create(
            steam_id=steam_id,
            defaults={
                "name": original_name,
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
            if player.name != original_name:
                player.name = original_name
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

    except Exception as e:
        # Handle duplicate name constraint
        if "duplicate key value violates unique constraint" in str(
            e
        ) and "player_name" in str(e):
            logger.warning(
                f"Player name '{original_name}' already exists, creating with unique name"
            )

            # Create a unique name by appending the steam_id
            unique_name = f"{original_name}_{steam_id}"

            try:
                player, created = Player.get_or_create(
                    steam_id=steam_id,
                    defaults={
                        "name": unique_name,
                        "elo": user_data["elo"],
                        "country": user_data.get("country", "unknown"),
                        "avatar_url": user_data.get("avatar_url", ""),
                        "registered_since": user_data.get("registered_since"),
                    },
                )

                if created:
                    logger.info(
                        f"Created new player {player.name} (steam_id: {steam_id}) with unique name from crawl"
                    )

                return player

            except Exception as retry_error:
                logger.error(
                    f"Failed to create player even with unique name for steam_id {steam_id}: {str(retry_error)}"
                )
                raise
        else:
            # Re-raise other exceptions
            logger.error(
                f"Error creating/updating player for steam_id {steam_id}: {str(e)}"
            )
            raise


async def save_friends_crawl_result(
    steam_id: int,
    friends_list: List[int],
    html_content: str,
    status: str,
    new_friends_discovered: int = 0,
) -> None:
    """Save the friends crawl result to the FriendsCrawlTask table."""
    try:
        with db.atomic():
            # Create or update the FriendsCrawlTask record
            crawl_task, created = FriendsCrawlTask.get_or_create(
                steam_id=steam_id,
                defaults={
                    "last_crawled_at": datetime.now(ZoneInfo("UTC")),
                    "friends_list": friends_list,
                    "crawl_successful": status in ["success", "no_friends", "private"],
                    "crawl_status": status,
                    "page_html": html_content,
                    "friends_count": len(friends_list),
                    "new_friends_discovered": new_friends_discovered,
                    "error_message": None
                    if status in ["success", "no_friends", "private"]
                    else f"Status: {status}",
                },
            )

            if not created:
                # Update existing record
                crawl_task.last_crawled_at = datetime.now(ZoneInfo("UTC"))
                crawl_task.friends_list = friends_list
                crawl_task.crawl_successful = status in [
                    "success",
                    "no_friends",
                    "private",
                ]
                crawl_task.crawl_status = status
                crawl_task.page_html = html_content
                crawl_task.friends_count = len(friends_list)
                crawl_task.new_friends_discovered = new_friends_discovered
                crawl_task.error_message = (
                    None
                    if status in ["success", "no_friends", "private"]
                    else f"Status: {status}"
                )
                crawl_task.save()

                logger.debug(f"Updated friends crawl task for steam_id {steam_id}")
            else:
                logger.debug(f"Created new friends crawl task for steam_id {steam_id}")

    except Exception as e:
        logger.error(
            f"Error saving friends crawl result for steam_id {steam_id}: {str(e)}"
        )


async def crawl_single_user_with_friends(steam_id: int, page: Page) -> str:
    """Crawl a single user's friends page to get both profile and friends data."""
    logger.info(f"Crawling user profile and friends for steam_id {steam_id}")

    try:
        # Go directly to the friends page instead of the profile page
        url = f"https://renown.gg/profile/{steam_id}/friends"
        await page.goto(url, timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=60000)

        html_content = await page.content()

        # Check if profile exists (404 check)
        not_found_selector = 'div.row:has(h1:text("404")) > div.text:text("This page could not be found")'
        not_found = await page.query_selector(not_found_selector)
        if not_found:
            logger.info(f"User friends page {steam_id} does not exist (404)")

            # Mark the task as processed with error for 404s so we don't retry
            with db.atomic() as transaction:
                try:
                    task = (
                        UserCrawlTask.select()
                        .where(UserCrawlTask.steam_id == steam_id)
                        .first()
                    )
                    if task:
                        task.page_html = html_content
                        task.processed = True
                        task.error_occured = True
                        task.save()
                        logger.info(
                            f"Marked task for steam_id {steam_id} as processed with error (404)"
                        )
                except Exception as e:
                    logger.error(
                        f"Error updating task for 404 user {steam_id}: {str(e)}"
                    )
                    transaction.rollback()

            return "404"

        # Check if profile is private
        private_selector = (
            'div.text:text("This user has chosen to make their profile private")'
        )
        private_profile = await page.query_selector(private_selector)
        if private_profile:
            logger.info(f"User {steam_id} has a private profile")

            # For private profiles, we can still try to extract basic user data from what's available
            user_data = extract_user_data_from_friends_page_html(html_content, steam_id)
            if user_data:
                with db.atomic() as transaction:
                    try:
                        # Update user crawl task
                        task = (
                            UserCrawlTask.select()
                            .where(UserCrawlTask.steam_id == steam_id)
                            .first()
                        )
                        if task:
                            task.page_html = html_content
                            task.processed = True
                            task.error_occured = False
                            task.save()

                        # Create or update the player
                        create_or_update_player_from_crawl(user_data)

                        # Save friends crawl result as private (empty friends list)
                        await save_friends_crawl_result(
                            steam_id, [], html_content, "private"
                        )

                        logger.info(
                            f"Successfully processed private profile {steam_id}"
                        )
                        return "private"

                    except Exception as e:
                        logger.error(
                            f"Error processing private profile {steam_id}: {str(e)}"
                        )
                        transaction.rollback()
                        return "error"
            else:
                # Mark as processed with error if we can't get user data from private profile
                with db.atomic() as transaction:
                    try:
                        task = (
                            UserCrawlTask.select()
                            .where(UserCrawlTask.steam_id == steam_id)
                            .first()
                        )
                        if task:
                            task.page_html = html_content
                            task.processed = True
                            task.error_occured = True
                            task.save()
                    except Exception as e:
                        logger.error(
                            f"Error updating private profile task {steam_id}: {str(e)}"
                        )
                        transaction.rollback()
                return "private_no_data"

        # Extract user data from the friends page HTML
        user_data = extract_user_data_from_friends_page_html(html_content, steam_id)

        # Extract friends list
        friends_list = extract_friend_steam_ids_from_html(html_content)

        # Check if friends list is empty (but profile is not private)
        friends_list_selector = "div.profile-friend-list"
        friends_list_div = await page.query_selector(friends_list_selector)
        friends_status = "success"
        if not friends_list_div:
            logger.info(f"User {steam_id} has no friends or empty friends list")
            friends_status = "no_friends"
        elif not friends_list:
            logger.info(f"User {steam_id} friends list found but no friends extracted")
            friends_status = "no_friends"

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
                        task.error_occured = False
                        task.save()
                    else:
                        task = UserCrawlTask.create(
                            steam_id=steam_id,
                            page_html=html_content,
                            processed=True,
                            error_occured=False,
                        )

                    # Create or update the player
                    player = create_or_update_player_from_crawl(user_data)

                    # Process friends list
                    new_friends_discovered = 0
                    if friends_list:
                        # Check which friend steam IDs are new (not in Player table)
                        existing_steam_ids = set(
                            player.steam_id
                            for player in Player.select().where(
                                Player.steam_id.in_(friends_list)
                            )
                        )

                        new_steam_ids = set(friends_list) - existing_steam_ids
                        new_friends_discovered = len(new_steam_ids)

                        if new_steam_ids:
                            logger.info(
                                f"Found {len(new_steam_ids)} new steam IDs to add to crawl tasks"
                            )

                            # Create UserCrawlTask entries for new steam IDs
                            tasks_created = 0
                            for new_steam_id in new_steam_ids:
                                try:
                                    UserCrawlTask.get_or_create(
                                        steam_id=new_steam_id,
                                        defaults={
                                            "page_html": "",
                                            "processed": False,
                                            "error_occured": False,
                                        },
                                    )
                                    tasks_created += 1
                                except Exception as e:
                                    logger.warning(
                                        f"Could not create task for steam_id {new_steam_id}: {str(e)}"
                                    )

                            logger.info(f"Created {tasks_created} new user crawl tasks")

                        # Update the Player's friends_with field for backwards compatibility
                        player.friends_with = friends_list
                        player.save()

                    # Save friends crawl result
                    await save_friends_crawl_result(
                        steam_id,
                        friends_list,
                        html_content,
                        friends_status,
                        new_friends_discovered,
                    )

                    logger.info(
                        f"Successfully crawled user {steam_id}: {len(friends_list)} friends, {new_friends_discovered} new"
                    )
                    return "success"

                except Exception as e:
                    logger.error(f"Error processing user {steam_id}: {str(e)}")
                    transaction.rollback()

                    # Mark task as processed with error if there's a database issue
                    try:
                        task = (
                            UserCrawlTask.select()
                            .where(UserCrawlTask.steam_id == steam_id)
                            .first()
                        )
                        if task:
                            task.processed = True
                            task.error_occured = True
                            task.save()
                            logger.info(
                                f"Marked task for steam_id {steam_id} as processed with error due to: {str(e)}"
                            )
                    except Exception as save_error:
                        logger.error(
                            f"Could not mark task as error for {steam_id}: {str(save_error)}"
                        )

                    return "error"
        else:
            logger.warning(f"Could not extract user data for {steam_id}")

            # Mark task as processed with error if we can't extract data
            with db.atomic() as transaction:
                try:
                    task = (
                        UserCrawlTask.select()
                        .where(UserCrawlTask.steam_id == steam_id)
                        .first()
                    )
                    if task:
                        task.page_html = html_content
                        task.processed = True
                        task.error_occured = True
                        task.save()
                        logger.info(
                            f"Marked task for steam_id {steam_id} as processed with error (no data extracted)"
                        )
                except Exception as e:
                    logger.error(
                        f"Error updating task for no-data user {steam_id}: {str(e)}"
                    )
                    transaction.rollback()

            return "no_data"

    except Exception as e:
        logger.error(f"Error crawling user {steam_id}: {str(e)}")

        # Mark task as processed with error for any other exceptions
        try:
            with db.atomic():
                task = (
                    UserCrawlTask.select()
                    .where(UserCrawlTask.steam_id == steam_id)
                    .first()
                )
                if task:
                    task.processed = True
                    task.error_occured = True
                    task.save()
                    logger.info(
                        f"Marked task for steam_id {steam_id} as processed with error due to exception: {str(e)}"
                    )
        except Exception as save_error:
            logger.error(
                f"Could not mark task as error for {steam_id}: {str(save_error)}"
            )

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


async def crawl_users_concurrent_batch(
    tasks: List[UserCrawlTask],
    contexts: List[BrowserContext],
    overall_progress: dict = None,
) -> dict:
    """Crawl users for a batch of tasks using multiple browser contexts concurrently."""
    results = {
        "success": 0,
        "404": 0,
        "private": 0,
        "private_no_data": 0,
        "no_data": 0,
        "error": 0,
        "context_closed": 0,
    }

    if not tasks:
        return results

    # Create pages for each context
    pages = []
    try:
        for context in contexts:
            page = await context.new_page()
            pages.append(page)

        # Create semaphore to limit concurrent operations
        semaphore = asyncio.Semaphore(len(contexts))

        async def process_single_task(task: UserCrawlTask, page_idx: int) -> dict:
            async with semaphore:
                if overall_progress:
                    overall_progress["current"] += 1
                    current = overall_progress["current"]
                    total = overall_progress["total"]
                    percentage = (current / total * 100) if total > 0 else 0

                    logger.info(
                        f"[Worker {page_idx + 1}] Processing user {current}/{total} ({percentage:.1f}%): steam_id {task.steam_id}"
                    )

                page = pages[page_idx % len(pages)]
                status = await crawl_single_user_with_friends(task.steam_id, page)

                logger.info(
                    f"[Worker {page_idx + 1}] User {task.steam_id} crawl result: {status}"
                )

                # Small delay between requests to be respectful
                await asyncio.sleep(0.5)

                return {"status": status}

        # Create tasks for all users with worker assignment
        async_tasks = []
        for i, task in enumerate(tasks):
            worker_idx = i % len(contexts)
            async_task = process_single_task(task, worker_idx)
            async_tasks.append(async_task)

        # Execute all tasks concurrently
        task_results = await asyncio.gather(*async_tasks, return_exceptions=True)

        # Process results
        for result in task_results:
            if isinstance(result, Exception):
                logger.error(f"Error in concurrent processing: {result}")
                results["error"] += 1
            else:
                status = result["status"]
                results[status] = results.get(status, 0) + 1

    finally:
        # Clean up pages
        for page in pages:
            try:
                await page.close()
            except Exception as e:
                logger.warning(f"Error closing page: {e}")

    return results


async def process_user_crawl_tasks_concurrent(
    context: Optional[BrowserContext] = None,
    batch_size: int = 100,
    max_batches: Optional[int] = None,
    num_workers: int = 4,
) -> None:
    """Process user crawl tasks using multiple concurrent browser contexts."""
    logger.info(f"Starting concurrent user crawl process with {num_workers} workers")

    own_context = context is None
    if own_context:
        playwright, browser, context = await setup_browser(include_cookies=True)

    try:
        # Create multiple contexts for concurrent processing
        contexts = [context]  # Include the main context

        # Create additional contexts if we need more workers
        for i in range(num_workers - 1):
            additional_context = await context.browser.new_context()
            contexts.append(additional_context)

        logger.info(
            f"Created {len(contexts)} browser contexts for concurrent processing"
        )

        try:
            # Get all unprocessed tasks upfront
            all_tasks_to_process = list(
                UserCrawlTask.select()
                .where(UserCrawlTask.processed == False)
                .order_by(UserCrawlTask.created_at.asc())
            )

            if not all_tasks_to_process:
                logger.info("No unprocessed user crawl tasks found")
                return

            total_tasks = len(all_tasks_to_process)
            logger.info(
                f"🎯 CONCURRENT USER CRAWL PLAN: Found {total_tasks} unprocessed user crawl tasks"
            )

            if max_batches:
                max_tasks_to_process = min(total_tasks, max_batches * batch_size)
                logger.info(
                    f"📊 Will process up to {max_tasks_to_process} tasks (limited by max_batches={max_batches})"
                )
            else:
                logger.info(f"📊 Will process all {total_tasks} tasks")

            logger.info(f"⚙️  Batch size: {batch_size} tasks per batch")
            logger.info(f"🔧 Workers: {num_workers} concurrent browser contexts")
            logger.info("🚀 Starting concurrent crawl...")
            logger.info("-" * 60)

            # Set up overall progress tracking
            overall_progress = {"current": 0, "total": total_tasks}

            total_processed = 0
            batch_count = 0
            task_index = 0

            while task_index < total_tasks and (
                max_batches is None or batch_count < max_batches
            ):
                # Get the next batch of tasks
                end_index = min(task_index + batch_size, total_tasks)
                current_batch = all_tasks_to_process[task_index:end_index]

                if not current_batch:
                    break

                # Process a batch concurrently
                batch_count += 1
                remaining_tasks = total_tasks - task_index
                logger.info(
                    f"Starting concurrent batch {batch_count}: processing {len(current_batch)} tasks ({remaining_tasks} remaining)"
                )

                batch_results = await crawl_users_concurrent_batch(
                    current_batch, contexts, overall_progress
                )

                batch_total = sum(batch_results.values())
                total_processed += batch_total
                task_index = end_index

                logger.info(f"Batch {batch_count} complete. Results: {batch_results}")

                # Calculate overall progress
                percentage_complete = (
                    (overall_progress["current"] / total_tasks * 100)
                    if total_tasks > 0
                    else 0
                )
                logger.info(
                    f"Overall progress: {overall_progress['current']}/{total_tasks} ({percentage_complete:.1f}%) - {total_tasks - overall_progress['current']} remaining"
                )

                # Check if we had context issues
                if batch_results.get("context_closed", 0) > 0:
                    logger.warning(
                        "Browser context issues detected, may need to restart"
                    )
                    break

                # Small delay between batches
                await asyncio.sleep(1)

            logger.info("-" * 60)
            logger.info("🏁 CONCURRENT USER CRAWL COMPLETE!")
            logger.info(
                f"✅ Total processed: {total_processed} tasks across {batch_count} batches using {num_workers} workers"
            )

            if overall_progress["current"] < total_tasks:
                remaining = total_tasks - overall_progress["current"]
                logger.info(f"⏭️  {remaining} tasks remaining (stopped early)")
            else:
                logger.info("🎉 All tasks processed successfully!")

        finally:
            # Close additional contexts (keep the main one if we didn't create it)
            for i, ctx in enumerate(contexts):
                if (
                    i > 0 or own_context
                ):  # Close additional contexts, or main if we own it
                    try:
                        await ctx.close()
                    except Exception as e:
                        logger.warning(f"Error closing context {i}: {e}")

    except Exception as e:
        logger.error(f"Error in concurrent user crawl process: {str(e)}")
        await send_alert(f"Error in concurrent user crawl process: {str(e)}")
    finally:
        if own_context:
            await cleanup_browser(playwright, browser)


async def process_user_crawl_tasks(
    context: Optional[BrowserContext] = None,
    batch_size: int = 100,
    concurrent: bool = False,
    num_workers: int = 4,
    max_batches: Optional[int] = None,
) -> None:
    """Process pending user crawl tasks."""
    if concurrent:
        await process_user_crawl_tasks_concurrent(
            context=context,
            batch_size=batch_size,
            max_batches=max_batches,
            num_workers=num_workers,
        )
    else:
        # Original sequential implementation
        logger.info("Starting to process user crawl tasks (with friends)")

        own_context = context is None
        if own_context:
            playwright, browser, context = await setup_browser(include_cookies=True)

        try:
            # Get unprocessed user crawl tasks
            unprocessed_tasks = (
                UserCrawlTask.select()
                .where(UserCrawlTask.processed == False)
                .order_by(UserCrawlTask.created_at.asc())
                .limit(batch_size)
            )

            task_count = unprocessed_tasks.count()
            if task_count == 0:
                logger.info("No unprocessed user crawl tasks found")
                return

            logger.info(f"Found {task_count} unprocessed user crawl tasks to process")

            page = await context.new_page()
            try:
                results = {
                    "success": 0,
                    "404": 0,
                    "private": 0,
                    "private_no_data": 0,
                    "no_data": 0,
                    "error": 0,
                    "context_closed": 0,
                }

                for i, task in enumerate(unprocessed_tasks, 1):
                    logger.info(
                        f"Processing user crawl task {i}/{task_count} for steam_id {task.steam_id}"
                    )
                    status = await crawl_single_user_with_friends(task.steam_id, page)
                    results[status] = results.get(status, 0) + 1
                    logger.info(f"Processed user {task.steam_id}: status={status}")

                    # Small delay between requests to be respectful
                    await asyncio.sleep(0.5)

                logger.info(
                    f"Finished processing {task_count} user crawl tasks. Results: {results}"
                )

            finally:
                await page.close()

        except Exception as e:
            logger.error(f"Error processing user crawl tasks: {str(e)}")
            await send_alert(f"Error processing user crawl tasks: {str(e)}")
        finally:
            if own_context:
                await cleanup_browser(playwright, browser)


async def user_crawl_job(batch_size: int = 100):
    """Job function for the scheduler to process user crawl tasks."""
    logger.info(f"Starting user crawl job (batch_size={batch_size})")
    try:
        await process_user_crawl_tasks(batch_size=batch_size)
        logger.info("User crawl job completed successfully")
    except Exception as e:
        logger.error(f"Error in user crawl job: {str(e)}")
        await send_alert(f"Error in user crawl job: {str(e)}")


async def user_crawl_job_concurrent(
    batch_size: int = 100,
    max_batches: Optional[int] = None,
    num_workers: int = 4,
    show_stats: bool = True,
):
    """Standalone concurrent job function to crawl user profiles and friends."""
    mode = "concurrent"
    logger.info(
        f"Starting user crawl job in {mode} mode (batch_size={batch_size}, max_batches={max_batches}, num_workers={num_workers})"
    )

    if show_stats:
        # Get basic stats
        total_unprocessed = (
            UserCrawlTask.select().where(UserCrawlTask.processed == False).count()
        )
        total_tasks = UserCrawlTask.select().count()
        logger.info(
            f"Pre-crawl stats: {total_unprocessed} unprocessed / {total_tasks} total user crawl tasks"
        )

    start_time = asyncio.get_event_loop().time()

    try:
        await process_user_crawl_tasks(
            batch_size=batch_size,
            concurrent=True,
            num_workers=num_workers,
            max_batches=max_batches,
        )

        end_time = asyncio.get_event_loop().time()
        duration = end_time - start_time
        logger.info(
            f"User crawl job completed successfully in {duration:.2f} seconds ({mode} mode)"
        )

        if show_stats:
            total_unprocessed_after = (
                UserCrawlTask.select().where(UserCrawlTask.processed == False).count()
            )
            processed_this_run = total_unprocessed - total_unprocessed_after
            logger.info(
                f"Post-crawl stats: {total_unprocessed_after} unprocessed remaining, {processed_this_run} processed this run"
            )

    except Exception as e:
        logger.error(f"Error in concurrent user crawl job: {str(e)}")
        await send_alert(f"Error in concurrent user crawl job: {str(e)}")


if __name__ == "__main__":
    import argparse

    # Set up argument parsing
    parser = argparse.ArgumentParser(
        description="Crawl user profiles and friends lists from Renown"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of users to process per batch (default: 100)",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        help="Maximum number of batches to process (default: unlimited)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level",
    )
    parser.add_argument(
        "--concurrent",
        action="store_true",
        help="Use concurrent processing with multiple browser contexts (default: False)",
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        default=4,
        help="Number of concurrent browser workers to use (default: 4)",
    )
    parser.add_argument(
        "--stats", action="store_true", help="Show statistics before and after crawling"
    )

    args = parser.parse_args()

    # Set up logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    mode = "concurrent" if args.concurrent else "sequential"
    workers_info = f" with {args.num_workers} workers" if args.concurrent else ""
    logger.info(
        f"Starting user crawler in {mode} mode{workers_info} with batch_size={args.batch_size}"
    )

    if args.concurrent:
        # Run concurrent version
        asyncio.run(
            user_crawl_job_concurrent(
                batch_size=args.batch_size,
                max_batches=args.max_batches,
                num_workers=args.num_workers,
                show_stats=args.stats,
            )
        )
    else:
        # Run original version for scheduler compatibility
        asyncio.run(user_crawl_job(batch_size=args.batch_size))
