import logging

from clutch.models import MatchCrawlTask

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def fix_match_data():
    """Fix match data JSON structure by removing matchPageData wrapper."""
    logger.info("Starting to fix match data...")

    # Get all match tasks
    tasks = list(MatchCrawlTask.select())
    total_tasks = len(tasks)
    logger.info(f"Found {total_tasks} match tasks")

    fixed_count = 0
    for i, task in enumerate(tasks, 1):
        if not task.parsed_match_json:
            raise ValueError(f"Task {task.match_id} has no parsed_match_json")

        # Check if data is wrapped in matchPageData
        if "matchPageData" in task.parsed_match_json:
            logger.info(f"Fixing task {i}/{total_tasks} (Match ID: {task.match_id})")

            # Extract the inner data
            task.parsed_match_json = task.parsed_match_json["matchPageData"]
            task.save()
            fixed_count += 1
        task.processed = False
        task.save()

    logger.info(f"Fixed {fixed_count} match tasks")
    logger.info("Finished fixing match data")


if __name__ == "__main__":
    fix_match_data()
