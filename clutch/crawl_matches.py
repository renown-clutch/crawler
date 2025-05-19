import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from playwright.async_api import BrowserContext, Page

from clutch.browser_tools import cleanup_browser, setup_browser
from clutch.db.models import Drop, Match, MatchCrawlTask, Player, Stat, db
from clutch.telegram_utils import send_alert

logger = logging.getLogger(__name__)


def extract_array_from_push(script_content: str) -> Optional[str]:
    """Extracts the full JS array passed to __next_f.push([...]) using bracket matching."""
    start_idx = script_content.find("self.__next_f.push([")
    if start_idx == -1:
        return None

    start_idx += len("self.__next_f.push(")
    bracket_count = 0
    end_idx = start_idx

    while end_idx < len(script_content):
        char = script_content[end_idx]
        if char == "[":
            bracket_count += 1
        elif char == "]":
            bracket_count -= 1
            if bracket_count == 0:
                end_idx += 1
                break
        end_idx += 1

    array_string = script_content[start_idx:end_idx]
    return array_string


def extract_match_data(html_content: str) -> Optional[dict]:
    """Extract match data from HTML content."""

    script_pattern = r"<script[^>]*>(.*?)</script>"
    scripts = re.findall(script_pattern, html_content, re.DOTALL)

    for i, script_content in enumerate(scripts, 1):
        if "matchPageData" in script_content:
            raw_array_str = extract_array_from_push(script_content)
            if not raw_array_str:
                break

            start_idx = raw_array_str.find('{\\"matchPageData\\"')
            if start_idx != -1:
                raw_array_str = raw_array_str[start_idx:]

            raw_array_str = raw_array_str.replace('\\"', '"')
            if not raw_array_str.endswith("}"):
                last_bracket_idx = raw_array_str.rfind("}")
                raw_array_str = raw_array_str[: last_bracket_idx + 1]
            data = json.loads(raw_array_str)
            return data["matchPageData"]

    return None


def create_or_update_player(user_data: Dict) -> Player:
    """Create or update a player from match data."""
    steam_id = int(user_data["steam_id"])

    player_country = user_data["country"]
    if player_country is None:
        player_country = "unknown"
    else:
        player_country = player_country["alpha2"]

    player_avatar_url = user_data["steam_avatar"]
    if not player_avatar_url.startswith("https://"):
        player_avatar_url = ""

    player, created = Player.get_or_create(
        steam_id=steam_id,
        defaults={
            "name": user_data["nickname"],
            "elo": user_data["elo_rating"],
            "country": player_country,
            "avatar_url": player_avatar_url,
        },
    )

    if created:
        logger.info(f"Created new player {player.name} (steam_id: {steam_id})")
    else:
        if player.name != user_data["nickname"]:
            player.name = user_data["nickname"]
        if player.elo != user_data["elo_rating"]:
            player.elo = user_data["elo_rating"]
        if player.country != player_country:
            player.country = player_country
        if player.avatar_url != player_avatar_url:
            player.avatar_url = player_avatar_url
        player.save()

    return player


def process_match_data(match_crawl_task: MatchCrawlTask) -> Optional[Match]:
    """Process match data and create/update database records."""
    match_data = match_crawl_task.parsed_match_json

    if match_data["status"] == "CANCELLED":
        match_started_event_at = None
        team1_current_score = 0
        team2_current_score = 0
        server_city = None
        server_country = None
    else:
        match_started_event_at = datetime.fromisoformat(
            match_data["match_started_event_at"].replace("$D", "")
        ).replace(tzinfo=ZoneInfo("UTC"))
        team1_current_score = match_data["team1_current_score"]
        team2_current_score = match_data["team2_current_score"]
        server_city = match_data["ServerLocation"]["city"]
        server_country = match_data["ServerLocation"]["iso"]

        if team1_current_score is None:
            team1_current_score = 0
        if team2_current_score is None:
            team2_current_score = 0

    match_id = match_data["id"]
    match, created = Match.get_or_create(
        id=match_id,  # Use the Renown match ID as the primary key
        defaults={
            "season": match_data["season"]["name"],
            "map": match_data["map"]["display_name"],
            "team1_score": team1_current_score,
            "team2_score": team2_current_score,
            "created_at": datetime.fromisoformat(
                match_data["created_at"].replace("$D", "")
            ).replace(tzinfo=ZoneInfo("UTC")),
            "status": match_data["status"],
            "cancel_reason": match_data["cancel_reason"],
            "finished": match_data["finished"],
            "finished_at": datetime.fromisoformat(
                match_data["finished_at"].replace("$D", "")
            ).replace(tzinfo=ZoneInfo("UTC"))
            if match_data["finished_at"]
            else None,
            "winner": match_data["winner"],
            "demo_saved": match_data["demo_saved"],
            "match_started_event_at": match_started_event_at,
            "leetify_match_id": match_data["leetify_match_id"],
            "demo_file_name": match_data["demo_file_name"],
            "server_city": server_city,
            "server_country": server_country,
        },
    )

    if match_data["team_1_captain"]:
        team1_captain = Player.get_or_none(
            name=match_data["team_1_captain"]["nickname"]
        )
        if team1_captain:
            match.team1_captain = team1_captain

    if match_data["team_2_captain"]:
        team2_captain = Player.get_or_none(
            name=match_data["team_2_captain"]["nickname"]
        )
        if team2_captain:
            match.team2_captain = team2_captain

    match.save()

    stats_created = 0
    for participant in match_data["MatchParticipant"]:
        user_data = participant["user"]
        player = create_or_update_player(user_data)

        if match_data["status"] == "CANCELLED":
            elo_change = 0
            elo_before_match = 0
            leetify_rating = 0
        else:
            elo_change = participant["user"]["EloChange"][0]["elo_change"]
            elo_before_match = participant["user"]["EloChange"][0]["user_elo_before"]
            leetify_rating = participant["leetify_rating"]
            if leetify_rating is None:
                leetify_rating = 0

        Stat.create(
            player=player,
            match=match,
            team=participant["team"],
            kicked=participant["kicked"],
            kills=participant["kills"],
            assists=participant["assists"],
            deaths=participant["deaths"],
            damage_dealt=participant["damage_dealt"],
            kills_with_headshot=participant["kills_with_headshot"],
            two_ks=participant["two_ks"],
            three_ks=participant["three_ks"],
            four_ks=participant["four_ks"],
            five_ks=participant["five_ks"],
            entry_attempts=participant["entry_attempts"],
            entry_successes=participant["entry_successes"],
            utility_damage=participant["utility_damage"],
            flashes_enemies_blinded=participant["flashes_enemies_blinded"],
            mvps=participant["mvps"],
            party_id=participant["partyId"],
            abandon=participant["abandon"],
            leetify_rating=leetify_rating,
            elo_change=elo_change,
            elo_before_match=elo_before_match,
        )
        stats_created += 1
        logger.info(f"Created stat for player {player.name} in match {match_id}")

    logger.info(f"Successfully created {stats_created} stats for match {match_id}")

    if "Drop" in match_data and match_data["Drop"]:
        drop_data = match_data["Drop"]
        skin_data = drop_data["Skin"]
        user_data = drop_data["user"]

        player = Player.get_or_none(name=user_data["nickname"])

        price_str = skin_data.get("formattedPrice", "€0")
        price_str = price_str.replace("€", "").strip()
        try:
            price_float = float(price_str)
            price_in_cents = int(price_float * 100)
        except (ValueError, TypeError):
            price_in_cents = 0

        Drop.create(
            player=player,
            match=match,
            skin_name=skin_data["name"],
            asset_id=skin_data["asset_id"],
            icon_hash=skin_data["icon_hash"],
            exterior=skin_data["exterior"],
            doppler_phase=skin_data["doppler_phase"],
            buff_price=skin_data.get("buff_price", 0),
            name_color=skin_data["name_color"],
            float_value=skin_data["float_value"],
            rarity_name=skin_data["rarity_name"],
            steam_market_price=price_in_cents,
        )

    match_crawl_task.processed = True
    match_crawl_task.save()
    return match


async def update_or_create_match_task(
    match_id: int,
    page_html: str,
    processed: bool,
    error_occured: bool,
    match_status: str,
    parsed_match_json: Optional[dict] = None,
) -> None:
    """Update existing match task or create a new one if it doesn't exist."""
    task = MatchCrawlTask.select().where(MatchCrawlTask.match_id == match_id).first()
    if task:
        logger.info(f"Updating match {match_id} - {match_status}")
        task.page_html = page_html
        task.processed = processed
        task.error_occured = error_occured
        task.match_status = match_status
        task.parsed_match_json = parsed_match_json
        task.save()
    else:
        logger.info(f"Creating new match {match_id} - {match_status}")
        task = MatchCrawlTask.create(
            match_id=match_id,
            page_html=page_html,
            processed=processed,
            error_occured=error_occured,
            match_status=match_status,
            parsed_match_json=parsed_match_json,
        )
    return task


async def crawl_single_match(match_id: int, page: Page):
    """Crawl a single match page."""
    logger.info(f"Crawling match {match_id}")

    url = f"https://renown.gg/match/{match_id}"
    await page.goto(url, timeout=60000)
    await page.wait_for_load_state("networkidle", timeout=60000)

    html_content = await page.content()

    not_found_selector = (
        'div.row:has(h1:text("404")) > div.text:text("This page could not be found")'
    )
    not_found = await page.query_selector(not_found_selector)
    if not_found:
        logger.info(f"Match {match_id} does not exist (404)")
        return "404"

    status_selector = "div.status div.status-wrapper"
    status_element = await page.query_selector(status_selector)
    match_status = "error"

    if status_element:
        status_text = await status_element.text_content()
        match_status = status_text.strip().lower()

    if match_status in ["finished", "cancelled"]:
        match_data = extract_match_data(html_content)

        with db.atomic() as transaction:
            try:
                match_crawl_task = await update_or_create_match_task(
                    match_id=match_id,
                    page_html=html_content,
                    processed=False,
                    error_occured=False,
                    match_status=match_status,
                    parsed_match_json=match_data,
                )
                process_match_data(match_crawl_task)
                logger.info(
                    f"Successfully crawled and processed match {match_id} - {match_status}"
                )
            except Exception as e:
                logger.error(f"Error processing match {match_id}: {str(e)}")

                transaction.rollback()
                MatchCrawlTask.delete().where(
                    MatchCrawlTask.match_id == match_id
                ).execute()
                return "error"

    return match_status


async def get_last_match_id() -> int:
    """Get the last crawled match ID from the database."""
    last_match = (
        MatchCrawlTask.select().order_by(MatchCrawlTask.match_id.desc()).first()
    )
    if last_match:
        return last_match.match_id
    return 7  # hardcoded, since first match on renown is 8


async def get_live_matches(context: BrowserContext) -> List[Dict]:
    """Fetch current live matches from API."""
    page = await context.new_page()
    try:
        await page.goto("https://renown.gg/api/live-matches", timeout=30000)
        content = await page.content()
        json_content = await page.evaluate("() => JSON.parse(document.body.innerText)")
        live_matches = json_content.get("liveMatches", [])
        logger.info(
            f"Found {len(live_matches)} live matches: {[match['id'] for match in live_matches]}"
        )
        return live_matches
    finally:
        await page.close()


async def track_live_matches_with_stop(
    context: BrowserContext, stop_event: asyncio.Event
) -> None:
    """Poll and track live matches with higher frequency, stopping when signaled."""
    active_match_ids = set()
    logger.info("Starting live match tracker with stop event")

    while not stop_event.is_set():
        try:
            live_matches = await get_live_matches(context)
            current_live_ids = {match["id"] for match in live_matches}

            # Process newly started matches
            new_matches = current_live_ids - active_match_ids
            if new_matches:
                logger.info(f"New live matches detected: {new_matches}")
                for match_id in new_matches:
                    logger.info(
                        f"Creating tracking entry for new live match {match_id}"
                    )
                    # Create a tracking entry but don't process yet
                    await update_or_create_match_task(
                        match_id=match_id,
                        page_html="",
                        processed=False,
                        error_occured=False,
                        match_status="PLAYING",
                    )

            # Check for finished matches
            finished_matches = active_match_ids - current_live_ids
            if finished_matches:
                logger.info(
                    f"Matches no longer live (potentially finished): {finished_matches}"
                )
                for match_id in finished_matches:
                    logger.info(f"Processing potentially finished match {match_id}")
                    # Mark for immediate processing
                    page = await context.new_page()
                    try:
                        match_status = await crawl_single_match(match_id, page)
                        logger.info(
                            f"Processed finished match {match_id}: status={match_status}"
                        )
                    finally:
                        await page.close()

            # Update active match list
            active_match_ids = current_live_ids

            # For matches still active, update scores periodically
            for match in live_matches:
                match_id = match["id"]
                score_info = f"Score: {match.get('team1_current_score', 0)}-{match.get('team2_current_score', 0)}"
                # Only update DB with live data, not fully process yet
                task = (
                    MatchCrawlTask.select()
                    .where(MatchCrawlTask.match_id == match_id)
                    .first()
                )
                if task:
                    logger.info(f"Updating live match {match_id} - {score_info}")
                    task.match_status = match["status"]
                    task.save()

            if active_match_ids:
                logger.info(f"Currently tracking live matches: {active_match_ids}")

            # Use wait_for with timeout to allow for clean shutdown
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=25
                )  # Poll every 25 seconds
            except asyncio.TimeoutError:
                # This is expected - timeout means continue the loop
                pass

        except Exception as e:
            logger.error(f"Error in live match tracker: {str(e)}")
            # Check if we should stop
            if stop_event.is_set():
                break
            await asyncio.sleep(60)  # Back off on error

    logger.info("Live match tracker shutting down")


async def backfill_missed_matches(context: BrowserContext) -> None:
    """Check for any potentially missed matches and process them."""
    logger.info("Starting match backfill check")

    # Get the highest match ID we've recorded
    highest_tracked = await get_last_match_id()
    logger.info(f"Highest tracked match ID: {highest_tracked}")

    # Get the lowest unprocessed match
    lowest_unprocessed = (
        MatchCrawlTask.select()
        .where(MatchCrawlTask.processed == False)
        .order_by(MatchCrawlTask.match_id.asc())
        .first()
    )

    lowest_id = lowest_unprocessed.match_id if lowest_unprocessed else highest_tracked
    if lowest_unprocessed:
        logger.info(f"Lowest unprocessed match: {lowest_id}")

    # Check for any gaps in our match history
    all_matches = set(
        m.match_id for m in MatchCrawlTask.select(MatchCrawlTask.match_id)
    )
    missed_matches = set(range(8, highest_tracked + 1)) - all_matches

    if missed_matches:
        logger.info(
            f"Found {len(missed_matches)} missed matches: {sorted(missed_matches)}"
        )
    else:
        logger.info("No missed matches found in history")

    page = await context.new_page()
    try:
        # Process all gaps we found
        for match_id in sorted(missed_matches):
            logger.info(f"Processing missed match {match_id}")
            status = await crawl_single_match(match_id, page)
            logger.info(f"Processed missed match {match_id}: status={status}")

        # Now check for unprocessed matches
        unprocessed = (
            MatchCrawlTask.select()
            .where(MatchCrawlTask.processed == False)
            .order_by(MatchCrawlTask.match_id.asc())
        )

        unprocessed_count = unprocessed.count()
        if unprocessed_count > 0:
            logger.info(f"Found {unprocessed_count} unprocessed matches")
            for task in unprocessed:
                logger.info(f"Processing unfinished match {task.match_id}")
                status = await crawl_single_match(task.match_id, page)
                logger.info(
                    f"Processed unfinished match {task.match_id}: status={status}"
                )
        else:
            logger.info("No unprocessed matches found")
    finally:
        await page.close()


async def crawl_matches_combined(context: Optional[BrowserContext] = None) -> None:
    """Combined approach to crawl matches with backfilling and live tracking."""
    logger.info("Starting combined match crawler")

    own_context = context is None
    if own_context:
        logger.info("Creating new browser context")
        playwright, browser, context = await setup_browser()

    try:
        # First, backfill any missed matches
        logger.info("Starting backfill process")
        await backfill_missed_matches(context)
        logger.info("Backfill process completed")

        # Then continue looking for new matches beyond our highest ID
        highest_id = await get_last_match_id()
        logger.info(f"Looking for new matches beyond ID {highest_id}")

        page = await context.new_page()
        try:
            # Look ahead for some new matches
            consecutive_not_found = 0
            match_id = highest_id
            while consecutive_not_found < 1:  # Allow for some gaps
                match_id += 1
                logger.info(f"Checking for match {match_id}")
                match_status = await crawl_single_match(match_id, page)
                logger.info(f"Match {match_id} check result: {match_status}")

                if match_status == "404":
                    consecutive_not_found += 1
                    logger.info(
                        f"Match {match_id} not found (404) - consecutive not found: {consecutive_not_found}/5"
                    )
                else:
                    consecutive_not_found = 0
                    logger.info(
                        f"Found valid match {match_id} with status: {match_status}"
                    )
        finally:
            await page.close()
            logger.info(f"Finished looking for new matches (stopped at ID {match_id})")
    finally:
        if own_context:
            logger.info("Cleaning up browser context")
            await cleanup_browser(playwright, browser)


async def combined_match_crawler_job():
    """Combined job for all match crawling activity."""
    logger.info("Starting combined match crawler job")
    playwright, browser, context = await setup_browser(include_cookies=True)

    # Register this with the scheduler's task tracking (if available)
    try:
        from clutch.scheduler import _running_tasks

        _running_tasks["combined_crawler"] = (
            None,
            lambda: cleanup_browser(playwright, browser),
        )
        logger.info("Registered with scheduler's task tracking system")
    except (ImportError, AttributeError):
        logger.info("Could not register with scheduler task tracking (standalone mode)")

    # Flag to signal when to stop the tracker
    tracker_stop_event = asyncio.Event()
    tracking_task = None

    try:
        # Run backfill at startup
        logger.info("Running initial backfill")
        await backfill_missed_matches(context)
        logger.info("Initial backfill completed")

        # Start live tracking with stop event
        logger.info("Starting live match tracking task")
        tracking_task = asyncio.create_task(
            track_live_matches_with_stop(context, tracker_stop_event)
        )
        logger.info("Live match tracking task started")

        # Run standard crawler every 2 minutes to catch any new matches
        logger.info("Starting periodic crawler checks")
        try:
            while True:
                logger.info("Running periodic match crawler")
                await crawl_matches_combined(context)
                logger.info(
                    "Periodic match crawler completed, sleeping for 120 seconds"
                )
                await asyncio.sleep(120)
        except asyncio.CancelledError:
            logger.info("Main crawler loop cancelled")
            raise
    except Exception as e:
        logger.error(f"Error in combined match crawler: {str(e)}")
        await send_alert(f"Error in match crawler: {str(e)}")
    finally:
        # Signal the tracker to stop and wait for it
        logger.info("Signaling live tracker to stop")
        tracker_stop_event.set()

        if tracking_task:
            try:
                logger.info("Waiting for live tracker to finish")
                # Wait with a timeout to avoid hanging
                await asyncio.wait_for(tracking_task, timeout=5.0)
                logger.info("Live tracker finished")
            except asyncio.TimeoutError:
                logger.warning("Live tracker did not finish in time, cancelling")
                tracking_task.cancel()
            except Exception as e:
                logger.error(f"Error waiting for tracking task: {str(e)}")

        logger.info("Cleaning up combined match crawler job")
        await cleanup_browser(playwright, browser)


async def crawl_new_matches(context: Optional[BrowserContext] = None) -> None:
    """Crawl the matches and update the database.
    Legacy function - use crawl_matches_combined instead."""
    logger.info("Starting to crawl new matches (legacy method)")
    own_context = context is None
    if own_context:
        playwright, browser, context = await setup_browser()

    try:
        # Use the new combined approach instead
        await crawl_matches_combined(context)
    finally:
        if own_context:
            await cleanup_browser(playwright, browser)


async def track_live_matches(context: BrowserContext) -> None:
    """Poll and track live matches with higher frequency."""
    # Create an event that will never be set
    never_stop = asyncio.Event()
    logger.info(
        "Starting legacy live match tracker (may not properly clean up resources)"
    )
    await track_live_matches_with_stop(context, never_stop)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(combined_match_crawler_job())
