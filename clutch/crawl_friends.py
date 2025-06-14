import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page

from clutch.browser_tools import cleanup_browser, setup_browser
from clutch.db.models import FriendsCrawlTask, Player, UserCrawlTask, db
from clutch.telegram_utils import send_alert

logger = logging.getLogger(__name__)


def extract_friend_steam_ids_from_html(html_content: str) -> List[int]:
    """Extract friend steam IDs from the HTML content of a friends page."""
    try:
        soup = BeautifulSoup(html_content, "html.parser")

        # Find the profile-friend-list div specifically
        friends_list_div = soup.find("div", class_="profile-friend-list")

        if not friends_list_div:
            logger.info("No profile-friend-list section found")
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

        logger.info(
            f"Extracted {len(steam_ids)} unique friend steam IDs from friends list section"
        )
        return steam_ids

    except Exception as e:
        logger.error(f"Error extracting friend steam IDs from HTML: {str(e)}")
        return []


async def crawl_single_user_friends(steam_id: int, page: Page) -> dict:
    """Crawl a single user's friends page and extract friend list."""
    logger.info(f"Crawling friends list for steam_id {steam_id}")

    result = {
        "status": "error",
        "friends_list": [],
        "friends_count": 0,
        "new_friends_discovered": 0,
        "error_message": None,
        "html_content": "",
    }

    try:
        url = f"https://renown.gg/profile/{steam_id}/friends"
        await page.goto(url, timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=60000)

        html_content = await page.content()
        result["html_content"] = html_content

        # Check if friends page exists (404 check)
        not_found_selector = 'div.row:has(h1:text("404")) > div.text:text("This page could not be found")'
        not_found = await page.query_selector(not_found_selector)
        if not_found:
            logger.info(f"User friends page {steam_id} does not exist (404)")
            result["status"] = "404"
            result["error_message"] = "Friends page not found (404)"
            return result

        # Check if profile is private
        private_selector = (
            'div.text:text("This user has chosen to make their profile private")'
        )
        private_profile = await page.query_selector(private_selector)
        if private_profile:
            logger.info(f"User {steam_id} has a private profile")
            result["status"] = "private"
            result["error_message"] = "Profile is private"
            return result

        # Check if friends list is empty
        friends_list_selector = "div.profile-friend-list"
        friends_list = await page.query_selector(friends_list_selector)
        if not friends_list:
            logger.info(f"User {steam_id} has no friends or empty friends list")
            result["status"] = "no_friends"
            return result

        # Extract friend steam IDs
        friend_steam_ids = extract_friend_steam_ids_from_html(html_content)
        result["friends_list"] = friend_steam_ids
        result["friends_count"] = len(friend_steam_ids)

        if friend_steam_ids:
            # Check which friend steam IDs are new (not in Player table)
            existing_steam_ids = set(
                player.steam_id
                for player in Player.select().where(
                    Player.steam_id.in_(friend_steam_ids)
                )
            )

            new_steam_ids = set(friend_steam_ids) - existing_steam_ids
            result["new_friends_discovered"] = len(new_steam_ids)

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
            else:
                logger.info("No new steam IDs found - all friends already in database")

            # Also update the Player's friends_with field for backwards compatibility
            try:
                player = Player.get(Player.steam_id == steam_id)
                player.friends_with = friend_steam_ids
                player.save()
                logger.info(f"Updated player {steam_id} friends_with field")
            except Player.DoesNotExist:
                logger.warning(f"Player with steam_id {steam_id} not found in database")

            result["status"] = "success"
            return result
        else:
            logger.warning(f"Could not extract friend data for {steam_id}")
            result["status"] = "no_data"
            result["error_message"] = "Could not extract friend data from HTML"
            return result

    except Exception as e:
        logger.error(f"Error crawling friends for user {steam_id}: {str(e)}")
        result["error_message"] = str(e)

        if "Target page, context or browser has been closed" in str(e):
            result["status"] = "context_closed"
        else:
            result["status"] = "error"

        return result


def get_players_needing_friends_crawl(days_threshold: int = 30) -> List[Player]:
    """Get players who need their friends list crawled or re-crawled."""

    # Get all players
    all_players = Player.select()

    # Get players who have been crawled recently
    threshold_date = datetime.now(ZoneInfo("UTC")) - timedelta(days=days_threshold)

    recently_crawled_steam_ids = set(
        task.steam_id
        for task in FriendsCrawlTask.select().where(
            FriendsCrawlTask.last_crawled_at > threshold_date
        )
    )

    # Filter out recently crawled players
    players_needing_crawl = []
    for player in all_players:
        if player.steam_id not in recently_crawled_steam_ids:
            players_needing_crawl.append(player)

    # Sort by creation date (crawl older players first)
    players_needing_crawl.sort(key=lambda p: p.created_at)

    logger.info(
        f"Found {len(players_needing_crawl)} players needing friends crawl (threshold: {days_threshold} days)"
    )

    return players_needing_crawl


async def save_friends_crawl_result(steam_id: int, result: dict) -> None:
    """Save the friends crawl result to the FriendsCrawlTask table."""
    try:
        with db.atomic():
            # Create or update the FriendsCrawlTask record
            crawl_task, created = FriendsCrawlTask.get_or_create(
                steam_id=steam_id,
                defaults={
                    "last_crawled_at": datetime.now(ZoneInfo("UTC")),
                    "friends_list": result["friends_list"],
                    "crawl_successful": result["status"]
                    in ["success", "no_friends", "private"],
                    "crawl_status": result["status"],
                    "page_html": result.get("html_content", ""),
                    "friends_count": result["friends_count"],
                    "new_friends_discovered": result["new_friends_discovered"],
                    "error_message": result.get("error_message"),
                },
            )

            if not created:
                # Update existing record
                crawl_task.last_crawled_at = datetime.now(ZoneInfo("UTC"))
                crawl_task.friends_list = result["friends_list"]
                crawl_task.crawl_successful = result["status"] in [
                    "success",
                    "no_friends",
                    "private",
                ]
                crawl_task.crawl_status = result["status"]
                crawl_task.page_html = result.get("html_content", "")
                crawl_task.friends_count = result["friends_count"]
                crawl_task.new_friends_discovered = result["new_friends_discovered"]
                crawl_task.error_message = result.get("error_message")
                crawl_task.save()

                logger.info(f"Updated friends crawl task for steam_id {steam_id}")
            else:
                logger.info(f"Created new friends crawl task for steam_id {steam_id}")

    except Exception as e:
        logger.error(
            f"Error saving friends crawl result for steam_id {steam_id}: {str(e)}"
        )


async def crawl_friends_batch(
    players: List[Player],
    page: Page,
    batch_size: int = 50,
    overall_progress: dict = None,
) -> dict:
    """Crawl friends for a batch of players."""
    results = {
        "success": 0,
        "404": 0,
        "private": 0,
        "no_friends": 0,
        "no_data": 0,
        "error": 0,
        "context_closed": 0,
    }

    for i, player in enumerate(players[:batch_size]):
        if overall_progress:
            overall_progress["current"] += 1
            current = overall_progress["current"]
            total = overall_progress["total"]
            percentage = (current / total * 100) if total > 0 else 0

            logger.info(
                f"Processing user {current}/{total} ({percentage:.1f}%): {player.name} ({player.steam_id})"
            )
        else:
            logger.info(
                f"Processing player {i + 1}/{min(len(players), batch_size)}: {player.name} ({player.steam_id})"
            )

        result = await crawl_single_user_friends(player.steam_id, page)
        status = result["status"]
        results[status] = results.get(status, 0) + 1

        # Save the result to the database
        await save_friends_crawl_result(player.steam_id, result)

        logger.info(
            f"Player {player.steam_id} friends crawl result: {status} ({result['friends_count']} friends, {result['new_friends_discovered']} new)"
        )

        # Small delay between requests to be respectful
        await asyncio.sleep(1)

    return results


async def crawl_friends_concurrent_batch(
    players: List[Player],
    contexts: List[BrowserContext],
    overall_progress: dict = None,
) -> dict:
    """Crawl friends for a batch of players using multiple browser contexts concurrently."""
    results = {
        "success": 0,
        "404": 0,
        "private": 0,
        "no_friends": 0,
        "no_data": 0,
        "error": 0,
        "context_closed": 0,
    }

    if not players:
        return results

    # Create pages for each context
    pages = []
    try:
        for context in contexts:
            page = await context.new_page()
            pages.append(page)

        # Create semaphore to limit concurrent operations
        semaphore = asyncio.Semaphore(len(contexts))

        async def process_single_player(player: Player, page_idx: int) -> dict:
            async with semaphore:
                if overall_progress:
                    overall_progress["current"] += 1
                    current = overall_progress["current"]
                    total = overall_progress["total"]
                    percentage = (current / total * 100) if total > 0 else 0

                    logger.info(
                        f"[Worker {page_idx + 1}] Processing user {current}/{total} ({percentage:.1f}%): {player.name} ({player.steam_id})"
                    )

                page = pages[page_idx % len(pages)]
                result = await crawl_single_user_friends(player.steam_id, page)

                # Save the result to the database
                await save_friends_crawl_result(player.steam_id, result)

                logger.info(
                    f"[Worker {page_idx + 1}] Player {player.steam_id} friends crawl result: {result['status']} ({result['friends_count']} friends, {result['new_friends_discovered']} new)"
                )

                # Small delay between requests to be respectful
                await asyncio.sleep(0.5)

                return result

        # Create tasks for all players with worker assignment
        tasks = []
        for i, player in enumerate(players):
            worker_idx = i % len(contexts)
            task = process_single_player(player, worker_idx)
            tasks.append(task)

        # Execute all tasks concurrently
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

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


async def process_friends_crawl_concurrent(
    context: Optional[BrowserContext] = None,
    batch_size: int = 50,
    max_batches: Optional[int] = None,
    days_threshold: int = 30,
    num_workers: int = 4,
) -> None:
    """Process friends crawling for players using multiple concurrent browser contexts."""
    logger.info(f"Starting concurrent friends crawl process with {num_workers} workers")

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
            # Get all players who need friends crawling upfront
            all_players_to_process = get_players_needing_friends_crawl(days_threshold)

            if not all_players_to_process:
                logger.info("No players need friends crawling")
                return

            total_players = len(all_players_to_process)
            logger.info(
                f"🎯 CONCURRENT FRIENDS CRAWL PLAN: Found {total_players} players needing friends crawling"
            )

            if max_batches:
                max_players_to_process = min(total_players, max_batches * batch_size)
                logger.info(
                    f"📊 Will process up to {max_players_to_process} players (limited by max_batches={max_batches})"
                )
            else:
                logger.info(f"📊 Will process all {total_players} players")

            logger.info(f"⚙️  Batch size: {batch_size} players per batch")
            logger.info(f"🔧 Workers: {num_workers} concurrent browser contexts")
            logger.info(
                f"🕒 Threshold: Re-crawling players not crawled in {days_threshold} days"
            )
            logger.info("🚀 Starting concurrent crawl...")
            logger.info("-" * 60)

            # Set up overall progress tracking
            overall_progress = {"current": 0, "total": total_players}

            total_processed = 0
            batch_count = 0
            player_index = 0

            while player_index < total_players and (
                max_batches is None or batch_count < max_batches
            ):
                # Get the next batch of players
                end_index = min(player_index + batch_size, total_players)
                current_batch = all_players_to_process[player_index:end_index]

                if not current_batch:
                    break

                # Process a batch concurrently
                batch_count += 1
                remaining_players = total_players - player_index
                logger.info(
                    f"Starting concurrent batch {batch_count}: processing {len(current_batch)} players ({remaining_players} remaining)"
                )

                batch_results = await crawl_friends_concurrent_batch(
                    current_batch, contexts, overall_progress
                )

                batch_total = sum(batch_results.values())
                total_processed += batch_total
                player_index = end_index

                logger.info(f"Batch {batch_count} complete. Results: {batch_results}")

                # Calculate overall progress
                percentage_complete = (
                    (overall_progress["current"] / total_players * 100)
                    if total_players > 0
                    else 0
                )
                logger.info(
                    f"Overall progress: {overall_progress['current']}/{total_players} ({percentage_complete:.1f}%) - {total_players - overall_progress['current']} remaining"
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
            logger.info("🏁 CONCURRENT CRAWL COMPLETE!")
            logger.info(
                f"✅ Total processed: {total_processed} players across {batch_count} batches using {num_workers} workers"
            )

            if overall_progress["current"] < total_players:
                remaining = total_players - overall_progress["current"]
                logger.info(f"⏭️  {remaining} players remaining (stopped early)")
            else:
                logger.info("🎉 All players processed successfully!")

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
        logger.error(f"Error in concurrent friends crawl process: {str(e)}")
        await send_alert(f"Error in concurrent friends crawl process: {str(e)}")
    finally:
        if own_context:
            await cleanup_browser(playwright, browser)


async def process_friends_crawl(
    context: Optional[BrowserContext] = None,
    batch_size: int = 50,
    max_batches: Optional[int] = None,
    days_threshold: int = 30,
    concurrent: bool = False,
    num_workers: int = 4,
) -> None:
    """Process friends crawling for players who haven't been processed recently."""
    if concurrent:
        await process_friends_crawl_concurrent(
            context=context,
            batch_size=batch_size,
            max_batches=max_batches,
            days_threshold=days_threshold,
            num_workers=num_workers,
        )
    else:
        # Original sequential implementation
        logger.info("Starting friends crawl process")

        own_context = context is None
        if own_context:
            playwright, browser, context = await setup_browser(include_cookies=True)

        try:
            page = await context.new_page()
            try:
                # Get all players who need friends crawling upfront
                all_players_to_process = get_players_needing_friends_crawl(
                    days_threshold
                )

                if not all_players_to_process:
                    logger.info("No players need friends crawling")
                    return

                total_players = len(all_players_to_process)
                logger.info(
                    f"🎯 FRIENDS CRAWL PLAN: Found {total_players} players needing friends crawling"
                )

                if max_batches:
                    max_players_to_process = min(
                        total_players, max_batches * batch_size
                    )
                    logger.info(
                        f"📊 Will process up to {max_players_to_process} players (limited by max_batches={max_batches})"
                    )
                else:
                    logger.info(f"📊 Will process all {total_players} players")

                logger.info(f"⚙️  Batch size: {batch_size} players per batch")
                logger.info(
                    f"🕒 Threshold: Re-crawling players not crawled in {days_threshold} days"
                )
                logger.info("🚀 Starting crawl...")
                logger.info("-" * 60)

                # Set up overall progress tracking
                overall_progress = {"current": 0, "total": total_players}

                total_processed = 0
                batch_count = 0
                player_index = 0

                while player_index < total_players and (
                    max_batches is None or batch_count < max_batches
                ):
                    # Get the next batch of players
                    end_index = min(player_index + batch_size, total_players)
                    current_batch = all_players_to_process[player_index:end_index]

                    if not current_batch:
                        break

                    # Process a batch
                    batch_count += 1
                    remaining_players = total_players - player_index
                    logger.info(
                        f"Starting batch {batch_count}: processing {len(current_batch)} players ({remaining_players} remaining)"
                    )

                    batch_results = await crawl_friends_batch(
                        current_batch, page, batch_size, overall_progress
                    )

                    batch_total = sum(batch_results.values())
                    total_processed += batch_total
                    player_index = end_index

                    logger.info(
                        f"Batch {batch_count} complete. Results: {batch_results}"
                    )

                    # Calculate overall progress
                    percentage_complete = (
                        (overall_progress["current"] / total_players * 100)
                        if total_players > 0
                        else 0
                    )
                    logger.info(
                        f"Overall progress: {overall_progress['current']}/{total_players} ({percentage_complete:.1f}%) - {total_players - overall_progress['current']} remaining"
                    )

                    # Check if we had context issues
                    if batch_results.get("context_closed", 0) > 0:
                        logger.warning(
                            "Browser context issues detected, may need to restart"
                        )
                        break

                    # Small delay between batches
                    await asyncio.sleep(2)

                logger.info("-" * 60)
                logger.info("🏁 CRAWL COMPLETE!")
                logger.info(
                    f"✅ Total processed: {total_processed} players across {batch_count} batches"
                )

                if overall_progress["current"] < total_players:
                    remaining = total_players - overall_progress["current"]
                    logger.info(f"⏭️  {remaining} players remaining (stopped early)")
                else:
                    logger.info("🎉 All players processed successfully!")

            finally:
                await page.close()

        except Exception as e:
            logger.error(f"Error in friends crawl process: {str(e)}")
            await send_alert(f"Error in friends crawl process: {str(e)}")
        finally:
            if own_context:
                await cleanup_browser(playwright, browser)


def get_friends_crawl_stats() -> dict:
    """Get statistics about friends crawling progress."""

    total_players = Player.select().count()

    crawled_players = FriendsCrawlTask.select().count()
    successful_crawls = (
        FriendsCrawlTask.select()
        .where(FriendsCrawlTask.crawl_successful == True)
        .count()
    )

    # Get status distribution
    status_counts = {}
    for task in FriendsCrawlTask.select():
        status = task.crawl_status
        status_counts[status] = status_counts.get(status, 0) + 1

    # Get recent crawl activity (last 24 hours)
    recent_threshold = datetime.now(ZoneInfo("UTC")) - timedelta(hours=24)
    recent_crawls = (
        FriendsCrawlTask.select()
        .where(FriendsCrawlTask.last_crawled_at > recent_threshold)
        .count()
    )

    # Get total friends discovered
    from peewee import fn

    total_friends_query = (
        FriendsCrawlTask.select(fn.SUM(FriendsCrawlTask.friends_count))
        .where(FriendsCrawlTask.crawl_successful == True)
        .scalar()
    )
    total_friends = total_friends_query if total_friends_query is not None else 0

    return {
        "total_players": total_players,
        "crawled_players": crawled_players,
        "successful_crawls": successful_crawls,
        "coverage_percentage": (crawled_players / total_players * 100)
        if total_players > 0
        else 0,
        "success_rate": (successful_crawls / crawled_players * 100)
        if crawled_players > 0
        else 0,
        "status_distribution": status_counts,
        "recent_crawls_24h": recent_crawls,
        "total_friends_found": total_friends,
    }


async def friends_crawl_job(
    batch_size: int = 100,
    max_batches: Optional[int] = None,
    days_threshold: int = 30,
    show_stats: bool = True,
    concurrent: bool = False,
    num_workers: int = 4,
):
    """Standalone job function to crawl friends lists."""
    mode = "concurrent" if concurrent else "sequential"
    logger.info(
        f"Starting friends crawl job in {mode} mode (batch_size={batch_size}, max_batches={max_batches}, days_threshold={days_threshold}, num_workers={num_workers if concurrent else 1})"
    )

    if show_stats:
        stats = get_friends_crawl_stats()
        logger.info(f"Pre-crawl stats: {stats}")

    start_time = time.time()

    try:
        await process_friends_crawl(
            batch_size=batch_size,
            max_batches=max_batches,
            days_threshold=days_threshold,
            concurrent=concurrent,
            num_workers=num_workers,
        )

        end_time = time.time()
        duration = end_time - start_time
        logger.info(
            f"Friends crawl job completed successfully in {duration:.2f} seconds ({mode} mode)"
        )

        if show_stats:
            stats = get_friends_crawl_stats()
            logger.info(f"Post-crawl stats: {stats}")

    except Exception as e:
        logger.error(f"Error in friends crawl job: {str(e)}")
        await send_alert(f"Error in friends crawl job: {str(e)}")


if __name__ == "__main__":
    import argparse

    # Set up argument parsing
    parser = argparse.ArgumentParser(
        description="Crawl friends lists from Renown player profiles"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of players to process per batch (default: 100)",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        help="Maximum number of batches to process (default: unlimited)",
    )
    parser.add_argument(
        "--days-threshold",
        type=int,
        default=30,
        help="Re-crawl players who haven't been crawled in this many days (default: 30)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level",
    )
    parser.add_argument(
        "--stats", action="store_true", help="Show statistics before and after crawling"
    )
    parser.add_argument(
        "--stats-only", action="store_true", help="Only show statistics, don't crawl"
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

    args = parser.parse_args()

    # Set up logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if args.stats_only:
        stats = get_friends_crawl_stats()
        print("Friends Crawl Statistics:")
        print(f"  Total players: {stats['total_players']:,}")
        print(f"  Crawled players: {stats['crawled_players']:,}")
        print(f"  Coverage: {stats['coverage_percentage']:.1f}%")
        print(f"  Success rate: {stats['success_rate']:.1f}%")
        print(f"  Recent crawls (24h): {stats['recent_crawls_24h']:,}")
        print(f"  Total friends found: {stats['total_friends_found']:,}")
        print(f"  Status distribution: {stats['status_distribution']}")
    else:
        mode = "concurrent" if args.concurrent else "sequential"
        workers_info = f" with {args.num_workers} workers" if args.concurrent else ""
        logger.info(
            f"Starting friends crawler in {mode} mode{workers_info} with batch_size={args.batch_size}, max_batches={args.max_batches}, days_threshold={args.days_threshold}"
        )

        # Run the job
        asyncio.run(
            friends_crawl_job(
                batch_size=args.batch_size,
                max_batches=args.max_batches,
                days_threshold=args.days_threshold,
                show_stats=args.stats,
                concurrent=args.concurrent,
                num_workers=args.num_workers,
            )
        )
