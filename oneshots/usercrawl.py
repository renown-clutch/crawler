#!/usr/bin/env python3
"""
Oneshot script to create UserCrawlTask entries for steam IDs from renown_users.json
that don't already exist in the Player table.
"""

import json
import logging

from clutch.db.models import Player, UserCrawlTask

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """Main function to process renown users and create crawl tasks."""

    # Load the renown users JSON file
    try:
        with open("renown_users.json", "r") as f:
            renown_users = json.load(f)
        logger.info(f"Loaded {len(renown_users)} users from renown_users.json")
    except FileNotFoundError:
        logger.error("renown_users.json file not found")
        return
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON file: {e}")
        return

    # Get all existing steam_ids from Player table for efficient lookup
    existing_player_steam_ids = set(
        player.steam_id for player in Player.select(Player.steam_id)
    )
    logger.info(f"Found {len(existing_player_steam_ids)} existing players in database")

    # Get all existing steam_ids from UserCrawlTask table to avoid duplicates
    existing_crawl_task_steam_ids = set(
        task.steam_id for task in UserCrawlTask.select(UserCrawlTask.steam_id)
    )
    logger.info(
        f"Found {len(existing_crawl_task_steam_ids)} existing crawl tasks in database"
    )

    created_count = 0
    skipped_count = 0

    for user in renown_users:
        steam_id = int(user["steam_id"])

        # Check if steam_id already exists in Player table
        if steam_id in existing_player_steam_ids:
            skipped_count += 1
            continue

        # Check if we already have a crawl task for this steam_id
        if steam_id in existing_crawl_task_steam_ids:
            skipped_count += 1
            continue

        # Create UserCrawlTask for this steam_id
        try:
            UserCrawlTask.create(
                steam_id=steam_id,
                page_html="",  # Empty HTML since this is just queued for crawling
                processed=False,
            )
            created_count += 1
            logger.info(
                f"Created UserCrawlTask for steam_id: {steam_id} (nickname: {user.get('nickname', 'Unknown')})"
            )

        except Exception as e:
            logger.error(f"Error creating UserCrawlTask for steam_id {steam_id}: {e}")

    logger.info(
        f"Process completed. Created {created_count} new UserCrawlTask entries, skipped {skipped_count} existing entries"
    )


if __name__ == "__main__":
    main()
