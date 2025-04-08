import asyncio
import logging

from clutch.crawl_matches import process_match_data
from clutch.db.models import (
    Ban,
    Drop,
    Match,
    MatchCrawlTask,
    Player,
    Stat,
    UserCrawlTask,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def cleanup_database():
    """Delete all matches, stats, players, and drops while keeping tasks."""
    logger.info("Starting database cleanup...")

    # Delete drops
    deleted_drops = Drop.delete().execute()
    logger.info(f"Deleted {deleted_drops} drops")

    # Delete stats
    deleted_stats = Stat.delete().execute()
    logger.info(f"Deleted {deleted_stats} stats")

    # Delete matches
    deleted_matches = Match.delete().execute()
    logger.info(f"Deleted {deleted_matches} matches")

    # Delete bans (must be done before players due to foreign key constraint)
    deleted_bans = Ban.delete().execute()
    logger.info(f"Deleted {deleted_bans} bans")

    # Delete players
    deleted_players = Player.delete().execute()
    logger.info(f"Deleted {deleted_players} players")

    logger.info("Database cleanup completed")


async def reset_tasks():
    """Reset all task processing statuses."""
    logger.info("Resetting task processing statuses...")

    # Reset match tasks
    updated_match_tasks = (
        MatchCrawlTask.update(processed=False)
        .where(MatchCrawlTask.processed == True)  # noqa: E712
        .execute()
    )
    logger.info(f"Reset {updated_match_tasks} match tasks")

    # Reset user tasks
    updated_user_tasks = (
        UserCrawlTask.update(processed=False)
        .where(UserCrawlTask.processed == True)  # noqa: E712
        .execute()
    )
    logger.info(f"Reset {updated_user_tasks} user tasks")

    logger.info("Task reset completed")


async def process_all_match_tasks():
    """Process all match tasks to rebuild the database."""
    logger.info("Starting to process all match tasks...")

    # Get all unprocessed match tasks
    tasks = list(
        MatchCrawlTask.select().order_by(MatchCrawlTask.match_id.asc())  # noqa: E712
    )
    total_tasks = len(tasks)
    logger.info(f"Found {total_tasks} unprocessed match tasks")

    # Process each task
    for i, task in enumerate(tasks, 1):
        logger.info(f"Processing task {i}/{total_tasks} (Match ID: {task.match_id})")

        # Process the match data
        match = process_match_data(task)
        if not match:
            raise ValueError(
                f"Failed to process match {task.match_id} - no match object returned"
            )

        # Check if match was created in database
        db_match = Match.get_or_none(Match.id == task.match_id)
        if not db_match:
            raise ValueError(f"Match {task.match_id} was not created in database")

        # Check number of stats
        stats_count = Stat.select().where(Stat.match == task.match_id).count()
        if stats_count != 10:
            raise ValueError(
                f"Match {task.match_id} has {stats_count} stats instead of 10"
            )

        # Check if match has players (through stats)
        players_count = Stat.select().where(Stat.match == task.match_id).count()
        if players_count == 0:
            raise ValueError(f"Match {task.match_id} has no players")

        # Check if match has drops
        drops_count = Drop.select().where(Drop.match == task.match_id).count()
        if drops_count == 0:
            logger.warning(f"Match {task.match_id} has no drops (this might be normal)")

        logger.info(
            f"Successfully processed match {task.match_id} with {stats_count} stats, {players_count} players, and {drops_count} drops"
        )

    logger.info("Finished processing all match tasks")


async def main():
    """Main function to rebuild the database."""
    # Clean up the database
    await cleanup_database()

    # Reset task processing statuses
    await reset_tasks()

    # Process all match tasks
    await process_all_match_tasks()

    logger.info("Database rebuild completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
