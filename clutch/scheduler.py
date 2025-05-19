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


async def crawl_matches_job():
    """Crawl new matches and update the database. (Legacy - retained for compatibility)"""
    playwright, browser, context = await setup_browser(include_cookies=False)
    try:
        await crawl_new_matches(context)
        logger.info("Successfully crawled new matches")
    except Exception as e:
        logger.error(f"Error crawling new matches: {str(e)}")
        await send_alert(f"Error crawling new matches: {str(e)}")
    finally:
        await cleanup_browser(playwright, browser)


async def crawl_banlist_job():
    """Crawl the ban list and update the database."""
    playwright, browser, context = await setup_browser(include_cookies=True)
    try:
        await process_new_bans(context)
        logger.info("Successfully crawled ban list")
    except Exception as e:
        logger.error(f"Error crawling ban list: {str(e)}")
        await send_alert(f"Error crawling ban list: {str(e)}")
    finally:
        await cleanup_browser(playwright, browser)


async def check_login_status_job():
    """Check if user is still logged into renown.gg and send notification if not."""
    logger.info("Checking login status")
    playwright, browser, context = await setup_browser()
    try:
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
        try:
            await cleanup_browser(playwright, browser)
        except Exception as e:
            logger.error(f"Error during browser cleanup: {str(e)}")
            # Don't send alert for cleanup errors as they're less critical


scheduler = AsyncIOScheduler()

scheduler.add_job(
    check_login_status_job,
    CronTrigger(minute=0),
    id="check_login_status",
    name="Check Login",
    replace_existing=True,
    next_run_time=datetime.now(),
)

# Replace the old crawl_matches job with our new combined crawler
scheduler.add_job(
    combined_match_crawler_job,
    trigger="interval",
    minutes=10,  # Only restart the job if it terminates
    id="combined_match_crawler",
    name="Track and crawl matches",
    replace_existing=True,
    next_run_time=datetime.now() + timedelta(seconds=10),
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
        scheduler.shutdown()
        logger.info("Scheduler stopped")
