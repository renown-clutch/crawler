import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from playwright.async_api import BrowserContext, Page

from clutch.browser_tools import cleanup_browser, setup_browser
from clutch.db.models import Drop, Match, MatchCrawlTask, Player, Stat, db

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

            raw_array_str = raw_array_str[:-5]

            start_idx = raw_array_str.find('{\\"matchPageData\\"')
            if start_idx != -1:
                raw_array_str = raw_array_str[start_idx:]

            raw_array_str = raw_array_str.replace('\\"', '"')

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

        Drop.create(
            player=player,
            match=match,
            skin_name=skin_data["name"],
            asset_id=skin_data["asset_id"],
            icon_hash=skin_data["icon_hash"],
            exterior=skin_data["exterior"],
            doppler_phase=skin_data["doppler_phase"],
            buff_price=skin_data["buff_price"],
            name_color=skin_data["name_color"],
            float_value=skin_data["float_value"],
            rarity_name=skin_data["rarity_name"],
            steam_market_price=skin_data["steam_market"],
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


async def crawl_new_matches(context: Optional[BrowserContext] = None) -> None:
    """Crawl the matches and update the database."""
    logger.info("Starting to crawl new matches")
    page = await context.new_page()

    match_id = await get_last_match_id()
    match_status = "cancelled"

    while match_status in ["finished", "cancelled"]:
        match_id += 1
        match_status = await crawl_single_match(match_id, page)
    logger.info("No more matches to crawl")

    await page.close()


async def main():
    """Main function to run the matches crawler."""
    playwright, browser, context = await setup_browser()
    try:
        await crawl_new_matches(context)
    finally:
        await cleanup_browser(playwright, browser)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
