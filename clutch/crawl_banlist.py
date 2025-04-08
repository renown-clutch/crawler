import asyncio
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from playwright.async_api import BrowserContext, Page

from clutch.browser_tools import cleanup_browser, setup_browser
from clutch.db.models import Ban, Player


async def parse_ban_row(row_element, page: Page) -> dict:
    """Parse a single ban row from the ban list table."""

    player_cell = await row_element.query_selector(".cell.player")
    nickname_element = await player_cell.query_selector(".nickname a")
    nickname = await nickname_element.text_content()
    profile_url = await nickname_element.get_attribute("href")
    steam_id = int(profile_url.split("/")[-1])

    category_cell = await row_element.query_selector(".cell.category")
    category = await (await category_cell.query_selector(".name")).text_content()
    severity = await (await category_cell.query_selector(".duration")).text_content()

    timezone = await page.evaluate(
        "() => Intl.DateTimeFormat().resolvedOptions().timeZone"
    )

    ban_issued_cell = await row_element.query_selector(".cell.ban-issued")
    ban_issued_text = await (
        await ban_issued_cell.query_selector(".ban-date")
    ).text_content()
    ban_issued = (
        datetime.strptime(ban_issued_text, "%b %d, %Y %H:%M")
        .replace(tzinfo=ZoneInfo(timezone))
        .astimezone(ZoneInfo("UTC"))
    )

    expires_cell = await row_element.query_selector(".cell.expires")
    expires_element = await expires_cell.query_selector(".ban-date")
    permanent = False
    expires_on = None

    if expires_element:
        expires_text = await expires_element.text_content()
        expires_on = (
            datetime.strptime(expires_text, "%b %d, %Y %H:%M")
            .replace(tzinfo=ZoneInfo(timezone))
            .astimezone(ZoneInfo("UTC"))
        )
    else:
        permanent_element = await expires_cell.query_selector(".permanent")
        if permanent_element:
            permanent = True

    return {
        "steam_id": steam_id,
        "nickname": nickname,
        "category": category,
        "severity": severity,
        "issued_on": ban_issued,
        "expires_on": expires_on,
        "permanent": permanent,
    }


async def process_ban_page(page: Page, page_number: int) -> list:
    """Process a single page of the ban list."""
    url = f"https://renown.gg/banlist?page={page_number}"
    print(f"Processing ban list page {page_number}")

    await page.goto(url, timeout=60000)
    await page.wait_for_load_state("networkidle", timeout=60000)

    rows = await page.query_selector_all(".rows .row")
    bans = []

    for row in rows:
        ban_data = await parse_ban_row(row, page)
        bans.append(ban_data)

    return bans


async def process_new_bans(context: BrowserContext) -> None:
    """Process all new bans from the ban list."""
    page = await context.new_page()
    try:
        current_page = 1
        while True:
            bans = await process_ban_page(page, current_page)
            if not bans:
                break

            existing_bans = set()
            for ban in Ban.select():
                existing_bans.add((ban.player.steam_id, ban.issued_on))

            new_bans = []
            for ban_data in bans:
                ban_key = (ban_data["steam_id"], ban_data["issued_on"])
                if ban_key not in existing_bans:
                    new_bans.append(ban_data)

            if not new_bans:
                print(f"No new bans found on page {current_page}, stopping")
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
                print(f"Added new ban for {ban_data['nickname']}")

            next_button = await page.query_selector(
                'button.text-button:has-text("Next")'
            )
            if not next_button or await next_button.is_disabled():
                break

            current_page += 1

    finally:
        await page.close()


async def crawl_banlist(context: Optional[BrowserContext] = None) -> None:
    """Crawl the ban list and update the database."""
    if context is None:
        playwright, browser, context = await setup_browser()
        try:
            await process_new_bans(context)
        finally:
            await cleanup_browser(playwright, browser)
    else:
        await process_new_bans(context)


def main():
    """Main function to run the ban list crawler."""
    asyncio.run(crawl_banlist())


if __name__ == "__main__":
    main()
