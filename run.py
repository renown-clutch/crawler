import asyncio
from typing import Optional

from playwright.async_api import Page

from clutch.core import cleanup_browser, login_to_renown, setup_browser
from clutch.models import MatchCrawlTask
from clutch.tasks import crawl_match


async def check_live_matches(page: Page) -> Optional[int]:
    """Check for live matches and return the highest match ID if found."""
    try:
        await page.goto("https://renown.gg/play", timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=60000)

        # Check if live matches section exists
        live_matches = await page.query_selector(".live-match-list")

        if not live_matches:
            print("No live matches section found")
            return None

        # Get all match links
        match_links = await live_matches.query_selector_all("a.match")
        if not match_links:
            print("No live matches found")
            return None

        # Extract match IDs and find the highest one
        highest_id = 0
        for link in match_links:
            href = await link.get_attribute("href")
            if href:
                match_id = int(href.split("/")[-1])
                highest_id = max(highest_id, match_id)

        if highest_id > 0:
            print(f"Found live matches, highest ID: {highest_id}")
            return highest_id

        return None
    except Exception as e:
        print(f"Error checking live matches: {e}")
        return None


async def check_auth_and_live_matches():
    """Check authentication and live matches status."""
    print("Setting up browser...")
    playwright, browser, context = await setup_browser()

    try:
        print("Creating new page...")
        page = await context.new_page()

        print("Checking login status...")
        auth_result = await login_to_renown(page)
        if not auth_result:
            print("Login check failed or user not logged in")
            return None

        print("Login check successful, checking live matches...")
        highest_match_id = await check_live_matches(page)
        return highest_match_id
    finally:
        await cleanup_browser(playwright, browser)


def main():
    # First check auth and get highest live match ID
    print("Starting authentication and live match check...")
    highest_match_id = asyncio.run(check_auth_and_live_matches())

    # Hardcoded ignored match IDs
    IGNORED_MATCH_IDS = set(range(1, 8))  # IDs 1-7

    # First, process any matches that had errors
    print("Checking for previously failed matches...")
    failed_matches = (
        MatchCrawlTask.select()
        .where(
            (MatchCrawlTask.match_id > 7)
            & (
                (MatchCrawlTask.error_occured == True)  # noqa: E712
                | (MatchCrawlTask.match_status == "not_found")
                | (MatchCrawlTask.match_status == "playing")
                | (MatchCrawlTask.match_status == "vote")
            )
        )
        .order_by(MatchCrawlTask.match_id)
    )

    if failed_matches:
        print(f"Found {len(failed_matches)} failed matches to retry")
        for match in failed_matches:
            if match.match_id in IGNORED_MATCH_IDS:
                continue
            print(f"Retrying failed match ID {match.match_id}")
            result = crawl_match.delay(match.match_id)
            print(result.get())  # Wait for the result

    if highest_match_id is None:
        print("No live matches found, proceeding with sequential crawling...")
        # Get the highest match_id that has been processed
        last_processed = (
            MatchCrawlTask.select().order_by(MatchCrawlTask.match_id.desc()).first()
        )
        current_id = (
            last_processed.match_id + 1 if last_processed else 8
        )  # Start from 8 since 1-7 are ignored
    else:
        print(f"Found live matches, highest ID: {highest_match_id}")
        # Get the highest match_id that has been processed
        last_processed = (
            MatchCrawlTask.select().order_by(MatchCrawlTask.match_id.desc()).first()
        )
        current_id = last_processed.match_id + 1 if last_processed else 8

        # If there's a gap between last processed and highest live match,
        # find all matches that need to be processed
        if last_processed and last_processed.match_id < highest_match_id:
            print(
                f"Found gap between last processed ({last_processed.match_id}) and highest live match ({highest_match_id})"
            )

            # Get all match IDs in the gap that are either missing or have error status
            missing_matches = []
            for match_id in range(last_processed.match_id + 1, highest_match_id + 1):
                if match_id in IGNORED_MATCH_IDS:
                    continue

                existing_task = (
                    MatchCrawlTask.select()
                    .where(MatchCrawlTask.match_id == match_id)
                    .first()
                )
                if not existing_task or (
                    existing_task.error_occured
                    and existing_task.match_status == "error"
                ):
                    missing_matches.append(match_id)

            if missing_matches:
                print(f"Found {len(missing_matches)} matches to process in the gap")
                for match_id in missing_matches:
                    print(f"Processing match ID {match_id}")
                    result = crawl_match.delay(match_id)
                    print(result.get())  # Wait for the result

    # Process new matches sequentially
    consecutive_404s = 0
    while consecutive_404s < 1:
        if current_id in IGNORED_MATCH_IDS:
            current_id += 1
            continue

        if highest_match_id and current_id > highest_match_id:
            print("Reached highest live match ID, stopping...")
            break

        print(f"Crawling match ID {current_id}")
        result = crawl_match.delay(current_id)
        task_result = result.get()

        if "does not exist (404)" in task_result:
            consecutive_404s += 1
        else:
            consecutive_404s = 0

        current_id += 1

    print(f"Stopped at match ID {current_id - 1} after finding 10 consecutive 404s")


if __name__ == "__main__":
    main()
