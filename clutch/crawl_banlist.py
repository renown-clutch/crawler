import asyncio
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page

from clutch.browser_tools import cleanup_browser, setup_browser
from clutch.db.models import Ban, Player

logger = logging.getLogger(__name__)


def parse_ban_row(row_element) -> dict:
    player_cell = row_element.select_one(".cell.player")
    nickname_link = player_cell.select_one(".nickname a")
    nickname = nickname_link.text.strip()
    steam_id = int(nickname_link["href"].split("/")[-1])

    category_cell = row_element.select_one(".cell.category")
    category = category_cell.select_one(".name").text.strip()
    severity = category_cell.select_one(".duration").text.strip()

    ban_issued_cell = row_element.select_one(".cell.ban-issued")
    ban_issued_text = ban_issued_cell.select_one(".ban-date").text.strip()

    expires_cell = row_element.select_one(".cell.expires")
    expires_element = expires_cell.select_one(".ban-date")
    permanent = False

    if expires_element:
        expires_text = expires_element.text.strip()
    else:
        permanent = bool(expires_cell.select_one(".permanent"))

    return {
        "steam_id": steam_id,
        "nickname": nickname,
        "category": category,
        "severity": severity,
        "ban_issued_text": ban_issued_text,
        "expires_text": expires_text if not permanent else None,
        "permanent": permanent,
    }


async def process_ban_page(page: Page, page_number: int) -> list:
    url = f"https://renown.gg/banlist?page={page_number}"
    logger.info(f"Processing page {page_number}")

    await page.goto(url, timeout=60000)
    await page.wait_for_load_state("networkidle", timeout=60000)

    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")

    timezone = await page.evaluate(
        "() => Intl.DateTimeFormat().resolvedOptions().timeZone"
    )

    rows = soup.select(".rows .row")
    logger.info(f"Found {len(rows)} bans on page {page_number}")

    bans = []
    for row in rows:
        ban_data = parse_ban_row(row)

        ban_issued = (
            datetime.strptime(ban_data["ban_issued_text"], "%b %d, %Y %H:%M")
            .replace(tzinfo=ZoneInfo(timezone))
            .astimezone(ZoneInfo("UTC"))
        )

        expires_on = None
        if ban_data["expires_text"]:
            expires_on = (
                datetime.strptime(ban_data["expires_text"], "%b %d, %Y %H:%M")
                .replace(tzinfo=ZoneInfo(timezone))
                .astimezone(ZoneInfo("UTC"))
            )

        bans.append(
            {
                "steam_id": ban_data["steam_id"],
                "nickname": ban_data["nickname"],
                "category": ban_data["category"],
                "severity": ban_data["severity"],
                "issued_on": ban_issued,
                "expires_on": expires_on,
                "permanent": ban_data["permanent"],
            }
        )

    return bans


async def full_sync_bans(context: BrowserContext) -> None:
    """
    Full synchronization of bans with the website.
    This approach handles the fact that website admins delete and recreate bans rather than editing them.

    Process:
    1. Mark all existing bans as potentially deleted
    2. Crawl all pages from the website
    3. For each ban found on website:
       - If exact match exists in DB: mark as still active
       - If similar ban exists (same player, similar time): mark old as deleted, create new
       - If completely new: create new ban
    4. After crawling, mark any remaining bans as deleted from website
    5. Log all changes for monitoring
    """
    page = await context.new_page()
    now_time = datetime.now(ZoneInfo("UTC"))

    try:
        # Step 1: Mark all existing active bans as potentially deleted
        bans_marked_for_check = (
            Ban.update(website_active=False).where(Ban.website_active == True).execute()
        )
        logger.info(f"Marked {bans_marked_for_check} existing bans for verification")

        current_page = 1
        has_next_page = True
        total_processed = 0
        total_reactivated = 0
        total_new = 0
        total_deleted_and_recreated = 0

        # Build lookup of existing bans for efficient comparison
        existing_bans_by_exact_match = {}  # (steam_id, issued_on) -> ban
        existing_bans_by_player = {}  # steam_id -> [list of bans]

        for ban in Ban.select().join(Player):
            # Exact match lookup
            key = (ban.player.steam_id, ban.issued_on)
            existing_bans_by_exact_match[key] = ban

            # Player-based lookup for finding similar bans
            if ban.player.steam_id not in existing_bans_by_player:
                existing_bans_by_player[ban.player.steam_id] = []
            existing_bans_by_player[ban.player.steam_id].append(ban)

        logger.info(
            f"Starting full ban sync. Found {len(existing_bans_by_exact_match)} existing bans in database"
        )

        # Step 2: Crawl all pages from website
        while has_next_page:
            bans = await process_ban_page(page, current_page)

            if not bans:
                logger.info("No bans found on page, stopping sync")
                break

            for ban_data in bans:
                total_processed += 1
                exact_key = (ban_data["steam_id"], ban_data["issued_on"])

                if exact_key in existing_bans_by_exact_match:
                    # Exact match found - this ban still exists on website
                    existing_ban = existing_bans_by_exact_match[exact_key]

                    # Check if any details have changed
                    changes = []
                    if existing_ban.category != ban_data["category"]:
                        changes.append(
                            f"category: '{existing_ban.category}' -> '{ban_data['category']}'"
                        )
                        existing_ban.category = ban_data["category"]

                    if existing_ban.severity != ban_data["severity"]:
                        changes.append(
                            f"severity: '{existing_ban.severity}' -> '{ban_data['severity']}'"
                        )
                        existing_ban.severity = ban_data["severity"]

                    if existing_ban.expires_on != ban_data["expires_on"]:
                        old_expires = (
                            existing_ban.expires_on.isoformat()
                            if existing_ban.expires_on
                            else None
                        )
                        new_expires = (
                            ban_data["expires_on"].isoformat()
                            if ban_data["expires_on"]
                            else None
                        )
                        changes.append(
                            f"expires_on: '{old_expires}' -> '{new_expires}'"
                        )
                        existing_ban.expires_on = ban_data["expires_on"]

                    if existing_ban.permanent != ban_data["permanent"]:
                        changes.append(
                            f"permanent: {existing_ban.permanent} -> {ban_data['permanent']}"
                        )
                        existing_ban.permanent = ban_data["permanent"]

                    # Mark as active and update last seen time
                    existing_ban.website_active = True
                    existing_ban.last_seen_on_website = now_time

                    # Update player name if needed
                    if existing_ban.player.name != ban_data["nickname"]:
                        existing_ban.player.name = ban_data["nickname"]
                        existing_ban.player.save()
                        changes.append(
                            f"player_name: '{existing_ban.player.name}' -> '{ban_data['nickname']}'"
                        )

                    existing_ban.save()
                    total_reactivated += 1

                    if changes:
                        logger.warning(
                            f"UPDATED existing ban for {ban_data['nickname']} (steam_id: {ban_data['steam_id']}): {', '.join(changes)}"
                        )
                    else:
                        logger.debug(
                            f"Confirmed existing ban for {ban_data['nickname']} (steam_id: {ban_data['steam_id']})"
                        )

                else:
                    # No exact match - check if this might be a recreated ban
                    steam_id = ban_data["steam_id"]
                    is_likely_recreated = False

                    if steam_id in existing_bans_by_player:
                        # Look for bans for this player that are similar
                        for existing_ban in existing_bans_by_player[steam_id]:
                            if (
                                existing_ban.website_active
                            ):  # Skip already processed bans
                                continue

                            # Check if this could be the same ban recreated
                            # (same category, similar timeframe)
                            time_diff = abs(
                                (
                                    existing_ban.issued_on - ban_data["issued_on"]
                                ).total_seconds()
                            )

                            if (
                                existing_ban.category == ban_data["category"]
                                and existing_ban.severity == ban_data["severity"]
                                and time_diff < 86400
                            ):  # Within 24 hours
                                # This looks like a recreated ban
                                existing_ban.website_active = False
                                existing_ban.save()

                                logger.warning(
                                    f"DETECTED RECREATED BAN: Old ban (issued: {existing_ban.issued_on}) -> New ban (issued: {ban_data['issued_on']}) for {ban_data['nickname']}"
                                )
                                is_likely_recreated = True
                                total_deleted_and_recreated += 1
                                break

                    # Create new ban (whether completely new or recreated)
                    player, _ = Player.get_or_create(
                        steam_id=ban_data["steam_id"],
                        defaults={"name": ban_data["nickname"]},
                    )

                    Ban.create(
                        player=player,
                        category=ban_data["category"],
                        severity=ban_data["severity"],
                        issued_on=ban_data["issued_on"],
                        expires_on=ban_data["expires_on"],
                        permanent=ban_data["permanent"],
                        website_active=True,
                        last_seen_on_website=now_time,
                        created_at=now_time,
                    )
                    total_new += 1

                    if is_likely_recreated:
                        logger.warning(
                            f"Created RECREATED ban for {ban_data['nickname']} (steam_id: {ban_data['steam_id']}, issued: {ban_data['issued_on']})"
                        )
                    else:
                        logger.info(
                            f"Created NEW ban for {ban_data['nickname']} (steam_id: {ban_data['steam_id']}, issued: {ban_data['issued_on']})"
                        )

            next_button = await page.query_selector(
                'button.text-button:has-text("Next")'
            )
            has_next_page = bool(next_button and not await next_button.is_disabled())

            if has_next_page:
                current_page += 1
            else:
                logger.info(f"Reached last page {current_page} in sync")

        # Step 3: Mark any remaining inactive bans as deleted from website
        deleted_bans = Ban.select().where(Ban.website_active == False)
        total_deleted = deleted_bans.count()

        if total_deleted > 0:
            logger.warning(
                f"Found {total_deleted} bans that were DELETED from website:"
            )
            for deleted_ban in deleted_bans:
                logger.warning(
                    f"DELETED: {deleted_ban.player.name} (steam_id: {deleted_ban.player.steam_id}, issued: {deleted_ban.issued_on}, category: {deleted_ban.category})"
                )

        # Summary logging
        logger.info("Full ban sync completed:")
        logger.info(f"  - Processed {total_processed} bans from website")
        logger.info(f"  - Reactivated {total_reactivated} existing bans")
        logger.info(f"  - Created {total_new} new bans")
        logger.info(f"  - Detected {total_deleted_and_recreated} recreated bans")
        logger.info(f"  - Found {total_deleted} bans deleted from website")

        if total_deleted_and_recreated > 0 or total_deleted > 0:
            logger.warning(
                f"ATTENTION: Website admins made {total_deleted_and_recreated + total_deleted} ban modifications!"
            )

    finally:
        await page.close()


async def crawl_banlist(context: BrowserContext) -> None:
    """
    Legacy function for backward compatibility.
    Now redirects to full sync for more accurate data.
    """
    await full_sync_bans(context)


async def sync_banlist(context: Optional[BrowserContext] = None) -> None:
    """
    Entry point for the comprehensive ban synchronization job.
    This should be used for both regular updates and the daily audit job.
    """
    if context is None:
        playwright, browser, context = await setup_browser(include_cookies=True)
        try:
            await full_sync_bans(context)
        finally:
            await cleanup_browser(playwright, browser)
    else:
        await full_sync_bans(context)


def main():
    asyncio.run(sync_banlist())


if __name__ == "__main__":
    main()
