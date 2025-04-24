import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import requests
from decouple import config
from fastapi import FastAPI
from pydantic import BaseModel

from clutch.db.models import Ban, Drop, FaceitProfile, Player, Stat

logger = logging.getLogger(__name__)

FACEIT_API_KEY = config("FACEIT_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_faceit_profile(steam_id: int):
    logger.info(f"Getting faceit profile for {steam_id}")
    url = f"https://open.faceit.com/data/v4/players?game=cs2&game_player_id={steam_id}"
    headers = {"Authorization": f"Bearer {FACEIT_API_KEY}"}
    response = requests.get(url, headers=headers)
    return response.json()


# Cache for batch requests with steam_ids as key
batch_cache = {}
BATCH_CACHE_TTL = 300  # 5 minutes in seconds


# Generate a consistent hash for a list of steam IDs
def hash_steam_ids(steam_ids):
    # Sort to ensure consistent hash regardless of order
    sorted_ids = sorted(steam_ids)
    hash_input = json.dumps(sorted_ids)
    return hashlib.md5(hash_input.encode()).hexdigest()


# Cache faceit profile requests for 5 minutes (300 seconds)
@lru_cache(maxsize=128)
def get_faceit_profile_cached(steam_id: int):
    logger.info(f"Cache miss for faceit profile {steam_id}")
    return get_faceit_profile(steam_id)


# Function to clear specific cache entries
def clear_faceit_cache(steam_id: int = None):
    if steam_id:
        get_faceit_profile_cached.cache_clear()
        logger.info(f"Cleared faceit cache for {steam_id}")
    else:
        get_faceit_profile_cached.cache_clear()
        logger.info("Cleared entire faceit cache")


app = FastAPI()


@app.get("/get_user_faceit")
async def get_user_faceit(steam_id: int):
    player = Player.select().where(Player.steam_id == steam_id).first()
    if player is None:
        logger.info("User doesnt exist in db, falling back to no caching")
        data = get_faceit_profile(steam_id)
        return data
    faceit_profile = FaceitProfile.select().where(FaceitProfile.player == player)

    if faceit_profile.count() == 0:
        logger.info(f"No faceit profile found for {steam_id}, creating profile")
        faceit_json = get_faceit_profile_cached(steam_id)
        faceit_elo = faceit_json.get("games", {}).get("cs2", {}).get("faceit_elo", None)
        faceit_profile = FaceitProfile.create(
            player=player,
            faceit_json=faceit_json,
            faceit_url=faceit_json.get("faceit_url", ""),
            faceit_elo=faceit_elo,
        )

    else:
        logger.info(f"Faceit profile found for {steam_id}")
        faceit_profile = faceit_profile.first()
        current_time = datetime.now(timezone.utc)
        if faceit_profile.last_updated_at < current_time - timedelta(hours=5):
            logger.info(f"Updating faceit outdated profile for {steam_id}")
            faceit_json = get_faceit_profile_cached(steam_id)
            # Clear cache entry after updating to ensure fresh data next time
            clear_faceit_cache(steam_id)
            FaceitProfile.update(faceit_json=faceit_json).where(
                FaceitProfile.player == player
            ).execute()
    return faceit_profile.faceit_json


@app.get("/get_user_faceit_elo")
async def get_user_faceit_elo(steam_id: int):
    faceit_data = await get_user_faceit(steam_id)
    return faceit_data.get("games", {}).get("cs2", {}).get("faceit_elo", None)


@app.get("/get_user_faceit_url")
async def get_user_faceit_url(steam_id: int):
    faceit_data = await get_user_faceit(steam_id)
    url = faceit_data.get("faceit_url", "")
    if url:
        url = url.replace("{lang}/", "")
    return url


@app.get("/get_user_faceit_elo_and_url")
async def get_user_faceit_elo_and_url(steam_id: int):
    faceit_data = await get_user_faceit(steam_id)
    url = faceit_data.get("faceit_url", "")
    if url:
        url = url.replace("{lang}/", "")
    return {
        "elo": faceit_data.get("games", {}).get("cs2", {}).get("faceit_elo", None),
        "url": url,
    }


@app.get("/get_user_bans")
async def get_user_bans(steam_id: int):
    logger.info(f"Getting bans for user {steam_id}")
    player = Player.select().where(Player.steam_id == steam_id).first()

    if player is None:
        logger.info(f"No player found with steam_id {steam_id}")
        return {"bans": []}

    bans = Ban.select().where(Ban.player == player).order_by(Ban.issued_on.desc())

    result = {
        "bans": [
            {
                "category": ban.category,
                "severity": ban.severity,
                "issued_on": ban.issued_on.isoformat(),
                "expires_on": ban.expires_on.isoformat() if ban.expires_on else None,
                "permanent": ban.permanent,
                "duration_days": None
                if ban.permanent
                else (ban.expires_on - ban.issued_on).days
                if ban.expires_on
                else None,
            }
            for ban in bans
        ]
    }

    return result


@app.get("/get_user_drops")
async def get_user_drops(steam_id: int):
    logger.info(f"Getting drops for user {steam_id}")
    player = Player.select().where(Player.steam_id == steam_id).first()
    if player is None:
        logger.info(f"No player found with steam_id {steam_id}")
        return {"drops": []}
    drops = Drop.select().where(Drop.player == player).order_by(Drop.id.desc())

    result = {
        "drops": [
            {
                "skin_name": drop.skin_name,
                "asset_id": drop.asset_id,
                "icon_hash": drop.icon_hash,
                "exterior": drop.exterior,
                "doppler_phase": drop.doppler_phase,
                "buff_price": drop.buff_price,
                "name_color": drop.name_color,
                "float_value": drop.float_value,
                "rarity_name": drop.rarity_name,
                "steam_market_price": drop.steam_market_price,
                "match_id": drop.match.id if drop.match else None,
            }
            for drop in drops
        ]
    }

    return result


class RankRequest(BaseModel):
    steam_ids: list[int]


@app.post("/get_users_ranks")
async def get_users_ranks(request: RankRequest):
    steam_ids = request.steam_ids
    logger.info(f"Getting ranks for users: {steam_ids}")
    results = {steam_id: None for steam_id in steam_ids}

    if not steam_ids:
        return results

    players = Player.select(Player.steam_id, Player.elo).where(
        Player.steam_id.in_(steam_ids)
    )

    for player in players:
        results[player.steam_id] = player.elo

    return results


@app.post("/get_users_faceit_elo_and_url")
async def get_users_faceit_elo_and_url(request: RankRequest):
    steam_ids = request.steam_ids
    logger.info(f"Getting faceit elo and url for users: {steam_ids}")

    # Check if we have a cached result for this exact set of steam_ids
    cache_key = hash_steam_ids(steam_ids)
    if cache_key in batch_cache:
        cache_entry = batch_cache[cache_key]
        current_time = datetime.now(timezone.utc).timestamp()
        if current_time < cache_entry["expires"]:
            logger.info(f"Cache hit for batch request with {len(steam_ids)} steam_ids")
            return cache_entry["data"]

    # Process requests concurrently
    async def fetch_single_user(steam_id):
        try:
            # Get player from db
            player = Player.select().where(Player.steam_id == steam_id).first()

            if player is None:
                # User not in DB, fetch directly from faceit
                faceit_data = get_faceit_profile_cached(steam_id)
            else:
                # Check if we have a profile in DB
                faceit_profile = (
                    FaceitProfile.select().where(FaceitProfile.player == player).first()
                )

                if faceit_profile is None:
                    # No profile, fetch from faceit
                    faceit_data = get_faceit_profile_cached(steam_id)
                else:
                    # Check if profile is outdated
                    current_time = datetime.now(timezone.utc)
                    if faceit_profile.last_updated_at < current_time - timedelta(
                        hours=5
                    ):
                        # Outdated, update from faceit
                        faceit_data = get_faceit_profile_cached(steam_id)
                    else:
                        # Use existing profile data
                        faceit_data = faceit_profile.faceit_json

            url = faceit_data.get("faceit_url", "")
            if url:
                url = url.replace("{lang}/", "")

            return steam_id, {
                "elo": faceit_data.get("games", {})
                .get("cs2", {})
                .get("faceit_elo", None),
                "url": url,
            }
        except Exception as e:
            logger.error(f"Error getting faceit data for {steam_id}: {str(e)}")
            return steam_id, None

    # Use asyncio.gather to process all requests concurrently
    tasks = [fetch_single_user(steam_id) for steam_id in steam_ids]
    results_list = await asyncio.gather(*tasks)

    # Convert the results to a dictionary
    results = {steam_id: data for steam_id, data in results_list}

    # Cache the results
    batch_cache[cache_key] = {
        "data": results,
        "expires": datetime.now(timezone.utc).timestamp() + BATCH_CACHE_TTL,
    }

    return results


# Add an endpoint to manually clear the cache if needed
@app.post("/clear_faceit_cache")
async def api_clear_faceit_cache(steam_id: int = None, clear_batch: bool = False):
    if steam_id:
        get_faceit_profile_cached.cache_clear()
        logger.info(f"Cleared faceit cache for {steam_id}")
    elif clear_batch:
        batch_cache.clear()
        logger.info("Cleared entire batch cache")
    else:
        get_faceit_profile_cached.cache_clear()
        batch_cache.clear()
        logger.info("Cleared all faceit caches")

    return {"status": "success", "message": "Cache cleared successfully"}


@app.get("/get_profile_data")
async def get_profile_data(steam_id: int):
    logger.info(f"Getting profile data for user {steam_id}")
    player = Player.select().where(Player.steam_id == steam_id).first()

    if player is None:
        logger.info(f"No player found with steam_id {steam_id}")
        return {"highest_elo": None}

    # Get the highest elo_before_match value for this player
    highest_elo_stat = (
        Stat.select()
        .where(Stat.player == player)
        .order_by(Stat.elo_before_match.desc())
        .first()
    )
    highest_elo = highest_elo_stat.elo_before_match if highest_elo_stat else None

    return {"highest_elo": highest_elo}
