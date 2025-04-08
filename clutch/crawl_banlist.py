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


async def process_new_bans(context: BrowserContext) -> None:
    page = await context.new_page()
    try:
        current_page = 1
        has_next_page = True

        while has_next_page:
            bans = await process_ban_page(page, current_page)

            if not bans:
                logger.info("No bans found, stopping")
                break

            existing_bans = set()
            for ban in Ban.select():
                existing_bans.add((ban.player.steam_id, ban.issued_on))

            new_bans = [
                ban_data
                for ban_data in bans
                if (ban_data["steam_id"], ban_data["issued_on"]) not in existing_bans
            ]
            logger.info(f"Found {len(new_bans)} new bans out of {len(bans)} total bans")

            if not new_bans:
                logger.info(f"No new bans found on page {current_page}, stopping")
                break

            for ban_data in new_bans:
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
                )
                logger.info(f"Added new ban for {ban_data['nickname']}")

            next_button = await page.query_selector(
                'button.text-button:has-text("Next")'
            )
            has_next_page = bool(next_button and not await next_button.is_disabled())

            if has_next_page:
                current_page += 1
            else:
                logger.info(f"Reached last page {current_page}")

    finally:
        await page.close()


async def crawl_banlist(context: Optional[BrowserContext] = None) -> None:
    if context is None:
        playwright, browser, context = await setup_browser()
        try:
            await process_new_bans(context)
        finally:
            await cleanup_browser(playwright, browser)
    else:
        await process_new_bans(context)


def main():
    asyncio.run(crawl_banlist())


if __name__ == "__main__":
    main()
