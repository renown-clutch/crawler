import asyncio
import logging
from datetime import datetime, timezone

from clutch.cache import cache_manager
from clutch.db.models import Ban, Drop, FaceitProfile, Match, Stat

logger = logging.getLogger(__name__)

# Store last known counts to detect changes
last_counts = {
    "matches": 0,
    "bans": 0,
    "stats": 0,
    "drops": 0,
    "faceit_profiles": 0,
}

# Store timestamps of last checks
last_check_times = {
    "matches": datetime.now(timezone.utc),
    "bans": datetime.now(timezone.utc),
    "stats": datetime.now(timezone.utc),
    "drops": datetime.now(timezone.utc),
    "faceit_profiles": datetime.now(timezone.utc),
}

# Will be set by api.py during startup
clear_faceit_cache_func = None


def set_clear_faceit_cache_func(func):
    """Set the function to clear faceit cache."""
    global clear_faceit_cache_func
    clear_faceit_cache_func = func
    logger.info("Set clear_faceit_cache function")


async def check_and_invalidate_caches():
    """
    Check for changes in database tables and invalidate related caches if necessary.
    """
    try:
        await check_matches_changes()
        await check_bans_changes()
        await check_stats_changes()
        await check_drops_changes()
        await check_faceit_profiles_changes()
    except Exception as e:
        logger.error(f"Error in cache invalidation job: {str(e)}")


async def check_matches_changes():
    """Check if matches have changed and invalidate related caches."""
    current_count = Match.select().where(Match.status != "CANCELLED").count()
    last_timestamp = (
        Match.select(Match.created_at).order_by(Match.created_at.desc()).first()
    )

    if current_count != last_counts["matches"] or (
        last_timestamp and last_timestamp.created_at > last_check_times["matches"]
    ):
        logger.info(
            f"Matches changed (old: {last_counts['matches']}, new: {current_count}), invalidating related caches"
        )

        # Invalidate get_teams_map_stats cache
        cache_manager.invalidate_pattern("/get_teams_map_stats")

        # Invalidate get_user_last_matches cache
        cache_manager.invalidate_pattern("/get_user_last_matches")

        # Invalidate player matchup cache as well since it depends on matches
        cache_manager.invalidate_pattern("/get_player_matchup")

        # Update last count
        last_counts["matches"] = current_count
        last_check_times["matches"] = datetime.now(timezone.utc)


async def check_bans_changes():
    """Check if bans have changed and invalidate related caches."""
    current_count = Ban.select().count()
    last_timestamp = Ban.select(Ban.issued_on).order_by(Ban.issued_on.desc()).first()

    if current_count != last_counts["bans"] or (
        last_timestamp and last_timestamp.issued_on > last_check_times["bans"]
    ):
        logger.info(
            f"Bans changed (old: {last_counts['bans']}, new: {current_count}), invalidating related caches"
        )

        # Invalidate get_user_bans cache
        cache_manager.invalidate_pattern("/get_user_bans")

        # Update last count
        last_counts["bans"] = current_count
        last_check_times["bans"] = datetime.now(timezone.utc)


async def check_stats_changes():
    """Check if stats have changed and invalidate related caches."""
    current_count = Stat.select().count()

    if current_count != last_counts["stats"]:
        logger.info(
            f"Stats changed (old: {last_counts['stats']}, new: {current_count}), invalidating related caches"
        )

        # Invalidate get_players_stats cache
        cache_manager.invalidate_pattern("/get_players_stats")

        # Invalidate get_users_ranks cache
        cache_manager.invalidate_pattern("/get_users_ranks")

        # Invalidate get_profile_data cache
        cache_manager.invalidate_pattern("/get_profile_data")

        # Invalidate get_player_matchup cache
        cache_manager.invalidate_pattern("/get_player_matchup")

        # Update last count
        last_counts["stats"] = current_count
        last_check_times["stats"] = datetime.now(timezone.utc)


async def check_drops_changes():
    """Check if drops have changed and invalidate related caches."""
    current_count = Drop.select().count()

    if current_count != last_counts["drops"]:
        logger.info(
            f"Drops changed (old: {last_counts['drops']}, new: {current_count}), invalidating related caches"
        )

        # Invalidate get_user_drops cache
        cache_manager.invalidate_pattern("/get_user_drops")

        # Update last count
        last_counts["drops"] = current_count
        last_check_times["drops"] = datetime.now(timezone.utc)


async def check_faceit_profiles_changes():
    """Check if faceit profiles have changed and invalidate related caches."""
    current_count = FaceitProfile.select().count()
    last_updated = (
        FaceitProfile.select(FaceitProfile.last_updated_at)
        .order_by(FaceitProfile.last_updated_at.desc())
        .first()
    )

    if current_count != last_counts["faceit_profiles"] or (
        last_updated
        and last_updated.last_updated_at > last_check_times["faceit_profiles"]
    ):
        logger.info(
            f"Faceit profiles changed (old: {last_counts['faceit_profiles']}, new: {current_count}), invalidating related caches"
        )

        # Invalidate faceit related caches
        cache_manager.invalidate_pattern("/get_user_faceit")
        cache_manager.invalidate_pattern("/get_user_faceit_elo")
        cache_manager.invalidate_pattern("/get_user_faceit_url")
        cache_manager.invalidate_pattern("/get_user_faceit_elo_and_url")
        cache_manager.invalidate_pattern("/get_users_faceit_elo_and_url")

        # Clear lru_cache for faceit profiles if function is available
        if clear_faceit_cache_func:
            clear_faceit_cache_func()
        else:
            logger.warning(
                "clear_faceit_cache function not set, skipping LRU cache clear"
            )

        # Update last count
        last_counts["faceit_profiles"] = current_count
        last_check_times["faceit_profiles"] = datetime.now(timezone.utc)


async def initialize_counts():
    """Initialize the last counts from the database."""
    last_counts["matches"] = Match.select().where(Match.status != "CANCELLED").count()
    last_counts["bans"] = Ban.select().count()
    last_counts["stats"] = Stat.select().count()
    last_counts["drops"] = Drop.select().count()
    last_counts["faceit_profiles"] = FaceitProfile.select().count()

    logger.info(f"Initialized cache invalidator with counts: {last_counts}")


async def run_cache_invalidation_job(interval_seconds=20):
    """
    Run the cache invalidation job at specified intervals.

    Args:
        interval_seconds: How often to check for changes (in seconds)
    """
    await initialize_counts()

    while True:
        await check_and_invalidate_caches()
        await asyncio.sleep(interval_seconds)
