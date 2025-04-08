import asyncio
from typing import Any, Dict, List

from playwright.async_api import Error

from clutch.browser_tools import cleanup_browser, login_to_renown, setup_browser
from clutch.crawl_user import crawl_user
from clutch.models import MatchCrawlTask, UserCrawlTask


async def crawl_user_profiles(players: List[Dict[str, Any]]) -> None:
    """Crawl user profiles for all players."""
    print("\nStarting to crawl user profiles...")

    empty_tasks = UserCrawlTask.select().where(UserCrawlTask.page_html == "")
    empty_count = empty_tasks.count()
    if empty_count > 0:
        print(f"Deleting {empty_count} tasks with empty page HTML...")
        for task in empty_tasks:
            task.delete_instance()
        print("Deleted empty tasks")

    existing_tasks = [
        int(task.steam_id)
        for task in UserCrawlTask.select().where(
            UserCrawlTask.page_html != "",
        )
    ]
    print(f"Found {len(existing_tasks)} existing user crawl tasks")

    playwright, browser, context = await setup_browser()
    try:
        print("Creating login page...")
        page = await context.new_page()
        try:
            print("Logging in to Renown...")
            auth_result = await login_to_renown(page)
            if not auth_result:
                print("Failed to login to Renown")
                return
        finally:
            await page.close()

        print("Creating reusable page for crawling...")
        crawl_page = await context.new_page()
        try:
            total_players = len(players)
            for i, player in enumerate(players, 1):
                steam_id = int(player["steam_id"])

                if steam_id in existing_tasks:
                    print(
                        f"Skipping already crawled user {i}/{total_players} (Steam ID: {steam_id})"
                    )
                    continue

                print(f"Crawling user {i}/{total_players} (Steam ID: {steam_id})")
                try:
                    result = await crawl_user(steam_id, context, crawl_page)
                    print(f"Result: {result}")
                except Error as e:
                    if "Target page, context or browser has been closed" in str(e):
                        print("Browser context closed, recreating...")
                        await cleanup_browser(playwright, browser)
                        playwright, browser, context = await setup_browser()
                        page = await context.new_page()
                        try:
                            print("Logging in to Renown...")
                            auth_result = await login_to_renown(page)
                            if not auth_result:
                                print("Failed to login to Renown")
                                return
                        finally:
                            await page.close()
                        crawl_page = await context.new_page()
                        result = await crawl_user(steam_id, context, crawl_page)
                        print(f"Result: {result}")
                    else:
                        raise
        finally:
            if crawl_page:
                await crawl_page.close()
    finally:
        await cleanup_browser(playwright, browser)


def extract_players_from_matchcrawl() -> List[Dict[str, Any]]:
    """
    Extract player profiles from all matchcrawl tasks.
    Returns a list of player objects containing player name, steam ID, and other relevant information.
    """
    players = []
    seen_steam_ids = set()

    tasks = MatchCrawlTask.select().where(
        MatchCrawlTask.error_occured == False,  # noqa: E712
        MatchCrawlTask.parsed_match_json.is_null(False),
    )

    total_tasks = len(tasks)
    print(f"\nStarting to process {total_tasks} matches...")

    for i, task in enumerate(tasks, 1):
        try:
            match_data = task.parsed_match_json["matchPageData"]
            participants = match_data["MatchParticipant"]

            for participant in participants:
                user = participant["user"]
                steam_id = user["steam_id"]

                if steam_id in seen_steam_ids:
                    continue

                seen_steam_ids.add(steam_id)

                player = {
                    "name": user["nickname"],
                    "steam_id": steam_id,
                    "steam_avatar": user["steam_avatar"],
                    "elo_rating": user["elo_rating"],
                    "country": user["country"]["alpha2"]
                    if user.get("country")
                    else None,
                }
                players.append(player)

        except Exception as e:
            print(f"Error processing match {task.match_id}: {str(e)}")
            continue

    print(
        f"\nFinished processing all matches. Total unique players found: {len(players)}"
    )
    return players


async def main():
    """Main function to run the extraction and crawl user profiles."""
    try:
        players = extract_players_from_matchcrawl()

        print(f"Found {len(players)} unique players:")
        await crawl_user_profiles(players)

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
