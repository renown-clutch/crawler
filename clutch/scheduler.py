import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from decouple import config
from telegram import Bot

from .browser_tools import check_renown_login, cleanup_browser, setup_browser
from .crawl_banlist import process_new_bans
from .crawl_matches import combined_match_crawler_job, crawl_new_matches
from .telegram_utils import send_alert

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_CHAT_ID = config("TELEGRAM_CHAT_ID")
TELEGRAM_BOT_TOKEN = config("TELEGRAM_BOT_TOKEN")

bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Store running tasks to ensure proper cleanup
_running_tasks = {}


async def cleanup_task(task_id):
    """Cleanup a running task if it exists."""
    if task_id in _running_tasks:
        logger.info(f"Cleaning up task: {task_id}")
        try:
            bg_task, cleanup_fn = _running_tasks[task_id]

            # First cancel any background task if it exists and is running
            if bg_task and not bg_task.done():
                logger.info(f"Cancelling background task for {task_id}")
                bg_task.cancel()
                try:
                    # Wait for it to actually cancel
                    await asyncio.wait_for(asyncio.shield(bg_task), timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception) as e:
                    logger.info(
                        f"Background task for {task_id} cancelled: {type(e).__name__}"
                    )

            # Then run the cleanup function
            if cleanup_fn:
                logger.info(f"Running cleanup function for {task_id}")
                await cleanup_fn()
        except Exception as e:
            logger.error(f"Error cleaning up task {task_id}: {str(e)}")
        finally:
            del _running_tasks[task_id]


async def crawl_matches_job():
    """Crawl new matches and update the database. (Legacy - retained for compatibility)"""
    # Clean up any existing task first
    await cleanup_task("crawl_matches")

    playwright, browser, context = await setup_browser(include_cookies=False)
    try:
        # Store the cleanup function
        _running_tasks["crawl_matches"] = (
            None,
            lambda: cleanup_browser(playwright, browser),
        )

        await crawl_new_matches(context)
        logger.info("Successfully crawled new matches")
    except Exception as e:
        logger.error(f"Error crawling new matches: {str(e)}")
        await send_alert(f"Error crawling new matches: {str(e)}")
    finally:
        await cleanup_task("crawl_matches")


async def crawl_banlist_job():
    """Crawl the ban list and update the database."""
    # Clean up any existing task first
    await cleanup_task("crawl_banlist")

    playwright, browser, context = await setup_browser(include_cookies=True)
    try:
        # Store the cleanup function
        _running_tasks["crawl_banlist"] = (
            None,
            lambda: cleanup_browser(playwright, browser),
        )

        await process_new_bans(context)
        logger.info("Successfully crawled ban list")
    except Exception as e:
        logger.error(f"Error crawling ban list: {str(e)}")
        await send_alert(f"Error crawling ban list: {str(e)}")
    finally:
        await cleanup_task("crawl_banlist")


async def check_login_status_job():
    """Check if user is still logged into renown.gg and send notification if not."""
    # Clean up any existing task first
    await cleanup_task("check_login")

    logger.info("Checking login status")
    playwright, browser, context = await setup_browser()
    try:
        # Store the cleanup function
        _running_tasks["check_login"] = (
            None,
            lambda: cleanup_browser(playwright, browser),
        )

        is_logged_in = await check_renown_login(playwright, browser, context)
        if not is_logged_in:
            logger.warning("Login session expired")
            await send_alert(
                "⚠️ Renown.gg login session expired! Attempting to pause crawler tasks..."
            )

            # Pause all jobs instead of shutting down immediately
            for job in scheduler.get_jobs():
                if job.id != "check_login_status":  # Keep the login check job running
                    job.pause()
                    logger.info(f"Paused job: {job.name}")

            logger.info("All crawler tasks paused due to login expiration")
            return

        # If we're here and jobs are paused, try to resume them
        for job in scheduler.get_jobs():
            if job.id != "check_login_status" and job.next_run_time is None:
                job.resume()
                logger.info(f"Resumed job: {job.name}")

        logger.info("Login session active")
    except Exception as e:
        logger.error(f"Error checking login status: {str(e)}")
        await send_alert(f"Error checking login status: {str(e)}")
    finally:
        await cleanup_task("check_login")


# Wrapper for the combined match crawler to handle cleanup properly
async def combined_match_crawler_wrapper():
    """Wrapper for combined match crawler job to ensure proper cleanup."""
    # Clean up any existing task first
    await cleanup_task("combined_crawler")

    # Allow only one instance to run at a time
    if "combined_crawler" in _running_tasks:
        logger.warning(
            "Combined match crawler already running, skipping this execution"
        )
        return

    try:
        # Run the actual job
        await combined_match_crawler_job()
    except Exception as e:
        logger.error(f"Error in match crawler wrapper: {str(e)}")
        await send_alert(f"Error in match crawler: {str(e)}")
    finally:
        # Make sure to clean up in case the job didn't clean up properly
        await cleanup_task("combined_crawler")


scheduler = AsyncIOScheduler()

scheduler.add_job(
    check_login_status_job,
    CronTrigger(minute=0),
    id="check_login_status",
    name="Check Login",
    replace_existing=True,
    misfire_grace_time=300,  # 5 minutes
    coalesce=True,
    next_run_time=datetime.now(),
)

# Replace the old crawl_matches job with our new combined crawler
scheduler.add_job(
    combined_match_crawler_wrapper,
    trigger="interval",
    minutes=10,  # Only restart the job if it terminates
    id="combined_match_crawler",
    name="Track and crawl matches",
    replace_existing=True,
    misfire_grace_time=300,  # 5 minutes
    coalesce=True,
    next_run_time=datetime.now() + timedelta(seconds=10),
    max_instances=1,  # Ensure only one instance runs at a time
)

# Remove or comment out the old job
# scheduler.add_job(
#     crawl_matches_job,
#     CronTrigger(minute="*/1"),
#     id="crawl_matches",
#     name="Crawl matches",
#     replace_existing=True,
#     next_run_time=datetime.now() + timedelta(seconds=10),
# )

scheduler.add_job(
    crawl_banlist_job,
    CronTrigger(minute=0),
    id="crawl_banlist",
    name="Crawl banlist",
    replace_existing=True,
    misfire_grace_time=300,  # 5 minutes
    coalesce=True,
    next_run_time=datetime.now() + timedelta(seconds=15),
)


async def start_scheduler():
    """Start the scheduler."""
    scheduler.start()
    logger.info("Scheduler started")

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        # Clean up all running tasks
        task_ids = list(_running_tasks.keys())
        for task_id in task_ids:
            await cleanup_task(task_id)

        scheduler.shutdown()
        logger.info("Scheduler stopped")
