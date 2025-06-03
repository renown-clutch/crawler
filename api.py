import asyncio
import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional

import requests
from decouple import config
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from peewee import fn
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from clutch.cache import cache_manager
from clutch.cache_invalidator import (
    run_cache_invalidation_job,
    set_clear_faceit_cache_func,
)
from clutch.chat import chat_manager
from clutch.db.models import (
    Ban,
    Drop,
    FaceitProfile,
    Match,
    Player,
    Stat,
)
from clutch.middleware import RequestLoggingMiddleware

logger = logging.getLogger(__name__)

FACEIT_API_KEY = config("FACEIT_API_KEY")

RENOWN_AUTH_TOKEN = config("RENOWN_AUTH_TOKEN")

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
app.add_middleware(RequestLoggingMiddleware)


@app.on_event("startup")
async def startup_event():
    """Start the cache invalidation job when the API starts."""
    logger.info("Starting cache invalidation job...")

    # Register the clear_faceit_cache function with the cache invalidator
    set_clear_faceit_cache_func(clear_faceit_cache)

    # Set the connection manager for chat
    chat_manager.set_connection_manager(manager)

    # Run the job in the background to not block API startup
    asyncio.create_task(run_cache_invalidation_job(interval_seconds=60))
    logger.info("Cache invalidation job started.")


@app.get("/get_user_faceit")
@cache_manager.cached(ttl_seconds=300)  # Cache for 5 minutes
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
@cache_manager.cached(ttl_seconds=300)  # Cache for 5 minutes
async def get_user_faceit_elo(steam_id: int):
    faceit_data = await get_user_faceit(steam_id)
    return faceit_data.get("games", {}).get("cs2", {}).get("faceit_elo", None)


@app.get("/get_user_faceit_url")
@cache_manager.cached(ttl_seconds=300)  # Cache for 5 minutes
async def get_user_faceit_url(steam_id: int):
    faceit_data = await get_user_faceit(steam_id)
    url = faceit_data.get("faceit_url", "")
    if url:
        url = url.replace("{lang}/", "")
    return url


@app.get("/get_user_faceit_elo_and_url")
@cache_manager.cached(ttl_seconds=300)  # Cache for 5 minutes
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
@cache_manager.cached(ttl_seconds=600)  # Cache for 10 minutes
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
@cache_manager.cached(ttl_seconds=600)  # Cache for 10 minutes
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
@cache_manager.cached(ttl_seconds=300)  # Cache for 5 minutes
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
@cache_manager.cached(ttl_seconds=300)  # Cache for 5 minutes
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


@app.get("/get_profile_data")
@cache_manager.cached(ttl_seconds=600)  # Cache for 10 minutes
async def get_profile_data(steam_id: int):
    logger.info(f"Getting profile data for user {steam_id}")
    player = Player.select().where(Player.steam_id == steam_id).first()

    if player is None:
        logger.info(f"No player found with steam_id {steam_id}")
        return {"highest_elo": None}

    # Get all stats for this player
    player_stats = Stat.select().where(Stat.player == player)

    highest_elo = None

    for stat in player_stats:
        before_elo = stat.elo_before_match

        after_elo = stat.elo_before_match + stat.elo_change

        if highest_elo is None:
            highest_elo = max(before_elo, after_elo)
        else:
            highest_elo = max(highest_elo, before_elo, after_elo)

    return {"highest_elo": highest_elo}


@app.get("/get_player_matchup")
@cache_manager.cached(ttl_seconds=300)  # Cache for 5 minutes
async def get_player_matchup(player1_steam_id: int, player2_steam_id: int):
    logger.info(
        f"Getting matchup data for players {player1_steam_id} and {player2_steam_id}"
    )

    # Get both players
    player1 = Player.select().where(Player.steam_id == player1_steam_id).first()
    player2 = Player.select().where(Player.steam_id == player2_steam_id).first()

    if not player1 or not player2:
        return {"error": "One or both players not found"}
    if player1.steam_id == player2.steam_id:
        return {"error": "Players are the same"}

    # Find matches where both players participated
    common_matches = (
        Match.select()
        .join(Stat, on=(Match.id == Stat.match))
        .where(
            ((Stat.player == player1) | (Stat.player == player2))
            & (Match.status != "CANCELLED")
        )
        .group_by(Match.id)
        .having(fn.COUNT(Stat.id) == 2)
    )

    # Initialize counters
    matches_played_together = 0
    wins_together = 0
    matches_played_against = 0
    wins_against = 0
    matches_list = []

    # Process each match
    for match in common_matches:
        # Get stats for both players in this match
        player1_stat = (
            Stat.select()
            .where((Stat.match == match) & (Stat.player == player1))
            .first()
        )
        player2_stat = (
            Stat.select()
            .where((Stat.match == match) & (Stat.player == player2))
            .first()
        )

        if not player1_stat or not player2_stat:
            continue

        # Check if they were on the same team
        same_team = player1_stat.team == player2_stat.team

        # Determine if they won
        won_match = False
        if match.winner == "team1" and player1_stat.team == "team1":
            won_match = True
        elif match.winner == "team2" and player1_stat.team == "team2":
            won_match = True

        # Update counters
        if same_team:
            matches_played_together += 1
            if won_match:
                wins_together += 1
        else:
            matches_played_against += 1
            if won_match:
                wins_against += 1

        # Add match to list
        matches_list.append(
            {
                "match_id": match.id,
                "map": match.map,
                "date": match.created_at.isoformat(),
                "team1_score": match.team1_score,
                "team2_score": match.team2_score,
                "player1_team": player1_stat.team,
                "player2_team": player2_stat.team,
                "player1_stats": {
                    "kills": player1_stat.kills,
                    "deaths": player1_stat.deaths,
                    "assists": player1_stat.assists,
                    "mvps": player1_stat.mvps,
                    "leetify_rating": player1_stat.leetify_rating,
                },
                "player2_stats": {
                    "kills": player2_stat.kills,
                    "deaths": player2_stat.deaths,
                    "assists": player2_stat.assists,
                    "mvps": player2_stat.mvps,
                    "leetify_rating": player2_stat.leetify_rating,
                },
            }
        )

    # Calculate win rates
    winrate_together = (
        (wins_together / matches_played_together * 100)
        if matches_played_together > 0
        else 0
    )
    winrate_against = (
        (wins_against / matches_played_against * 100)
        if matches_played_against > 0
        else 0
    )

    return {
        "matches_played_together": matches_played_together,
        "wins_together": wins_together,
        "winrate_together": round(winrate_together, 2),
        "matches_played_against": matches_played_against,
        "wins_against": wins_against,
        "winrate_against": round(winrate_against, 2),
        "matches": matches_list,
    }


class TeamMapStatsRequest(BaseModel):
    team1_steam_ids: list[int]
    team2_steam_ids: list[int]


@app.post("/get_teams_map_stats")
@cache_manager.cached(ttl_seconds=300)  # Cache for 5 minutes
async def get_teams_map_stats(request: TeamMapStatsRequest):
    logger.info(
        f"Getting map stats for teams: {request.team1_steam_ids} and {request.team2_steam_ids}"
    )

    # Initialize results
    map_stats = {}

    # Process each team
    team1_stats = await get_team_map_stats(request.team1_steam_ids)
    team2_stats = await get_team_map_stats(request.team2_steam_ids)

    # Combine results
    all_maps = set(team1_stats.keys()) | set(team2_stats.keys())

    for map_name in all_maps:
        map_stats[map_name] = {
            "team1": team1_stats.get(map_name, 0),
            "team2": team2_stats.get(map_name, 0),
        }

    return map_stats


async def get_team_map_stats(steam_ids: list[int]):
    """Get map stats for a team of players based on their last 30 matches."""
    map_counts = {}

    async def process_player(steam_id: int):
        player = Player.select().where(Player.steam_id == steam_id).first()
        if player is None:
            logger.info(f"No player found with steam_id {steam_id}")
            return {}

        # Get the last 30 matches for this player
        player_matches = (
            Match.select(Match)
            .join(Stat, on=(Match.id == Stat.match))
            .where((Stat.player == player) & (Match.status != "CANCELLED"))
            .order_by(Match.id.asc())
            .distinct()
            .limit(30)
        )

        # Count maps
        player_map_counts = {}
        for match in reversed(list(player_matches)):
            if match.map not in player_map_counts:
                player_map_counts[match.map] = 0
            player_map_counts[match.map] += 1

        return player_map_counts

    # Process all players concurrently
    tasks = [process_player(steam_id) for steam_id in steam_ids]
    players_map_counts = await asyncio.gather(*tasks)

    # Combine all player map counts
    for player_counts in players_map_counts:
        for map_name, count in player_counts.items():
            if map_name not in map_counts:
                map_counts[map_name] = 0
            map_counts[map_name] += count

    return map_counts


class PlayerStatsRequest(BaseModel):
    steam_ids: list[int]


@app.post("/get_players_stats")
@cache_manager.cached(ttl_seconds=600)  # Cache for 5 minutes
async def get_players_stats(request: PlayerStatsRequest):
    logger.info(f"Getting stats for players: {request.steam_ids}")
    results = {}

    async def process_player(steam_id: int):
        player = Player.select().where(Player.steam_id == steam_id).first()

        if player is None:
            return steam_id, None

        # Get total count of non-cancelled games
        total_games = (
            Match.select(Match)
            .join(Stat, on=(Match.id == Stat.match))
            .where((Stat.player == player) & (Match.status != "CANCELLED"))
            .distinct()
            .count()
        )

        # Get last 30 matches for calculating averages
        player_matches = (
            Match.select(Match)
            .join(Stat, on=(Match.id == Stat.match))
            .where((Stat.player == player) & (Match.status != "CANCELLED"))
            .order_by(Match.id.desc())
            .distinct()
            .limit(30)
        )

        if total_games == 0:
            return steam_id, {
                "total_matches": 0,
                "winrate": 0,
                "avg_kills": 0,
                "avg_kd": 0,
                "avg_kr": 0,
                "avg_adr": 0,
                "avg_hs_percent": 0,
                "current_streak": 0,
                "avg_leetify_rating": 0,
            }

        total_kills = 0
        total_deaths = 0
        total_damage = 0
        total_headshots = 0
        total_rounds = 0
        total_leetify_rating = 0
        wins = 0

        match_count = 0
        current_streak = 0
        for match in reversed(list(player_matches)):
            match_count += 1

            stat = Stat.get(Stat.match == match, Stat.player == player)

            won_match = (match.winner == "team1" and stat.team == "team1") or (
                match.winner == "team2" and stat.team == "team2"
            )

            if current_streak == 0:
                current_streak = 1 if won_match else -1
            else:
                if current_streak > 0:
                    if won_match:
                        current_streak += 1
                    else:
                        current_streak = 0
                else:
                    if not won_match:
                        current_streak -= 1
                    else:
                        current_streak = 0

            if won_match:
                wins += 1

            total_kills += stat.kills
            total_deaths += stat.deaths
            total_damage += stat.damage_dealt
            total_headshots += stat.kills_with_headshot
            total_leetify_rating += stat.leetify_rating * 100
            total_rounds += match.team1_score + match.team2_score

        avg_kills = round(total_kills / match_count)
        avg_deaths = round(total_deaths / match_count)
        winrate = round((wins / match_count) * 100)

        avg_leetify_rating = round(total_leetify_rating / match_count, 1)

        avg_kd = (
            round(avg_kills / avg_deaths, 1) if avg_deaths > 0 else round(avg_kills, 1)
        )
        avg_kr = (
            round(avg_kills / (avg_kills + avg_deaths))
            if (avg_kills + avg_deaths) > 0
            else 0
        )

        avg_hs_percent = (
            round((total_headshots / total_kills * 100)) if total_kills > 0 else 0
        )

        avg_adr = round(total_damage / total_rounds) if total_rounds > 0 else 0

        return (
            steam_id,
            {
                "total_matches": total_games,
                "winrate": winrate,
                "avg_kills": avg_kills,
                "avg_deaths": avg_deaths,
                "avg_kd": avg_kd,
                "avg_kr": avg_kr,
                "avg_adr": avg_adr,
                "avg_hs_percent": avg_hs_percent,
                "avg_leetify_rating": avg_leetify_rating,
                "current_streak": current_streak,
            },
        )

    tasks = [process_player(steam_id) for steam_id in request.steam_ids]
    results_list = await asyncio.gather(*tasks)

    results = {steam_id: data for steam_id, data in results_list}

    return results


@app.get("/get_user_last_matches")
@cache_manager.cached(ttl_seconds=300)
async def get_user_last_matches(steam_id: int, limit: int = 3):
    logger.info(f"Getting last {limit} matches for user {steam_id}")

    player = Player.select().where(Player.steam_id == steam_id).first()

    if player is None:
        logger.info(f"No player found with steam_id {steam_id}")
        return {"matches": []}

    matches = (
        Match.select(Match)
        .join(Stat, on=(Match.id == Stat.match))
        .where((Stat.player == player) & (Match.status != "CANCELLED"))
        .order_by(Match.created_at.desc())
        .limit(limit)
        .distinct()
    )

    matches_data = []
    for match in matches:
        stat = Stat.get(Stat.match == match, Stat.player == player)

        won = (match.winner == "team1" and stat.team == "team1") or (
            match.winner == "team2" and stat.team == "team2"
        )

        total_rounds = match.team1_score + match.team2_score
        adr = round(stat.damage_dealt / total_rounds) if total_rounds > 0 else 0

        if stat.team == "team1":
            team_score = match.team1_score
            opponent_score = match.team2_score
        else:
            team_score = match.team2_score
            opponent_score = match.team1_score

        matches_data.append(
            {
                "match_id": match.id,
                "map": match.map,
                "played_at": match.created_at.isoformat(),
                "team_score": team_score,
                "opponent_score": opponent_score,
                "won": won,
                "kills": stat.kills,
                "assists": stat.assists,
                "deaths": stat.deaths,
                "adr": adr,
                "elo_before": stat.elo_before_match,
                "elo_change": stat.elo_change,
            }
        )

    return {"matches": matches_data}


@app.get("/get_live_matches")
@cache_manager.cached(ttl_seconds=10)
async def get_live_matches():
    logger.info("Getting live matches from Renown")
    url = "https://renown.gg/api/live-matches"
    cookies = {"auth_token": RENOWN_AUTH_TOKEN}

    try:
        response = requests.get(url, cookies=cookies)
        response.raise_for_status()  # Raise an exception for bad status codes
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching live matches from Renown: {str(e)}")
        return {"error": "Failed to fetch live matches", "details": str(e)}


class PlayerNamesRequest(BaseModel):
    player_names: list[str]


@app.post("/get_faceit_elo_by_names")
@cache_manager.cached(ttl_seconds=300)  # Cache for 5 minutes
async def get_faceit_elo_by_names(request: PlayerNamesRequest):
    logger.info(f"Getting faceit elo for players by names: {request.player_names}")
    results = {name: None for name in request.player_names}

    # Find players by name
    players = Player.select(Player.name, Player.steam_id).where(
        Player.name.in_(request.player_names)
    )

    # Create a map of names to steam_ids
    steam_id_map = {player.name: player.steam_id for player in players}

    # Get faceit data only for players we found
    steam_ids = list(steam_id_map.values())
    if steam_ids:
        # Reuse existing endpoint to fetch Faceit data
        faceit_request = RankRequest(steam_ids=steam_ids)
        faceit_data = await get_users_faceit_elo_and_url(faceit_request)

        # Map the results back to player names
        for name, steam_id in steam_id_map.items():
            if steam_id in faceit_data and faceit_data[steam_id] is not None:
                results[name] = faceit_data[steam_id]["elo"]

    return results


class CacheInvalidationRequest(BaseModel):
    pattern: str
    admin_key: str


class CacheStatisticsRequest(BaseModel):
    admin_key: str




class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.connection_info: Dict[WebSocket, Dict[str, Any]] = {}

    async def connect(
        self, websocket: WebSocket, client_id: str, steam_id: Optional[int] = None
    ):
        await websocket.accept()
        if client_id not in self.active_connections:
            self.active_connections[client_id] = []
        self.active_connections[client_id].append(websocket)
        self.connection_info[websocket] = {
            "client_id": client_id,
            "steam_id": steam_id,
            "connected_at": datetime.now(timezone.utc),
        }
        logger.info(f"Client {client_id} connected, steam_id: {steam_id}")

    async def disconnect(self, websocket: WebSocket):
        for client_id, connections in list(self.active_connections.items()):
            if websocket in connections:
                connections.remove(websocket)
                if websocket in self.connection_info:
                    logger.info(
                        f"Client {self.connection_info[websocket]['client_id']} disconnected"
                    )
                    del self.connection_info[websocket]
                if not connections:
                    del self.active_connections[client_id]
                break

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json(message)

    async def broadcast_to_client(self, message: dict, client_id: str):
        if client_id in self.active_connections:
            disconnected = []
            for connection in self.active_connections[client_id]:
                try:
                    await connection.send_json(message)
                except RuntimeError:
                    disconnected.append(connection)

            # Clean up any disconnected clients
            for conn in disconnected:
                await self.disconnect(conn)

    async def broadcast_to_steam_id(self, message: dict, steam_id: int):
        disconnected = []
        for websocket, info in self.connection_info.items():
            if info.get("steam_id") == steam_id:
                try:
                    await websocket.send_json(message)
                except RuntimeError:
                    disconnected.append(websocket)

        # Clean up disconnected connections
        for conn in disconnected:
            await self.disconnect(conn)

    async def broadcast(self, message: dict):
        disconnected = []
        for client_id, connections in self.active_connections.items():
            for connection in connections:
                try:
                    await connection.send_json(message)
                except RuntimeError:
                    disconnected.append(connection)

        # Clean up disconnected connections
        for conn in disconnected:
            await self.disconnect(conn)

    def get_connected_clients_count(self):
        total_connections = sum(
            len(connections) for connections in self.active_connections.values()
        )
        return {
            "total_connections": total_connections,
            "unique_clients": len(self.active_connections),
            "clients": {
                client_id: len(connections)
                for client_id, connections in self.active_connections.items()
            },
        }


# Create connection manager instance
manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    client_id: str = Query(...),
    steam_id: Optional[int] = Query(None),
):
    await manager.connect(websocket, client_id, steam_id)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)

                # Process message based on its type
                if "type" in message:
                    try:
                        if message["type"] == "ping":
                            await manager.send_personal_message(
                                {"type": "pong"}, websocket
                            )

                        # Chat message handling
                        elif message["type"] == "send_chat_message":
                            if "username" in message and "message" in message:
                                message_data = await chat_manager.add_message(
                                    message["username"], message["message"]
                                )
                                await manager.send_personal_message(
                                    {"type": "chat_message_sent", "data": message_data},
                                    websocket,
                                )

                        # Get recent messages
                        elif message["type"] == "get_recent_messages":
                            limit = message.get("limit", 50)
                            messages = await chat_manager.get_recent_messages(limit)
                            await manager.send_personal_message(
                                {"type": "recent_messages", "data": messages}, websocket
                            )

                        # User Faceit data endpoints
                        elif message["type"] == "get_user_faceit":
                            if "steam_id" in message:
                                faceit_data = await get_user_faceit(message["steam_id"])
                                await manager.send_personal_message(
                                    {
                                        "type": "faceit_data",
                                        "request_type": "get_user_faceit",
                                        "steam_id": message["steam_id"],
                                        "data": faceit_data,
                                    },
                                    websocket,
                                )

                        elif message["type"] == "get_user_faceit_elo":
                            if "steam_id" in message:
                                elo = await get_user_faceit_elo(message["steam_id"])
                                await manager.send_personal_message(
                                    {
                                        "type": "faceit_data",
                                        "request_type": "get_user_faceit_elo",
                                        "steam_id": message["steam_id"],
                                        "data": elo,
                                    },
                                    websocket,
                                )

                        elif message["type"] == "get_user_faceit_url":
                            if "steam_id" in message:
                                url = await get_user_faceit_url(message["steam_id"])
                                await manager.send_personal_message(
                                    {
                                        "type": "faceit_data",
                                        "request_type": "get_user_faceit_url",
                                        "steam_id": message["steam_id"],
                                        "data": url,
                                    },
                                    websocket,
                                )

                        elif message["type"] == "get_user_faceit_elo_and_url":
                            if "steam_id" in message:
                                data = await get_user_faceit_elo_and_url(
                                    message["steam_id"]
                                )
                                await manager.send_personal_message(
                                    {
                                        "type": "faceit_data",
                                        "request_type": "get_user_faceit_elo_and_url",
                                        "steam_id": message["steam_id"],
                                        "data": data,
                                    },
                                    websocket,
                                )

                        # User bans endpoint
                        elif message["type"] == "get_user_bans":
                            if "steam_id" in message:
                                bans = await get_user_bans(message["steam_id"])
                                await manager.send_personal_message(
                                    {
                                        "type": "user_bans",
                                        "steam_id": message["steam_id"],
                                        "data": bans,
                                    },
                                    websocket,
                                )

                        # User drops endpoint
                        elif message["type"] == "get_user_drops":
                            if "steam_id" in message:
                                drops = await get_user_drops(message["steam_id"])
                                await manager.send_personal_message(
                                    {
                                        "type": "user_drops",
                                        "steam_id": message["steam_id"],
                                        "data": drops,
                                    },
                                    websocket,
                                )

                        # Multi-user ranks endpoint
                        elif message["type"] == "get_users_ranks":
                            if "steam_ids" in message:
                                ranks_request = RankRequest(
                                    steam_ids=message["steam_ids"]
                                )
                                ranks = await get_users_ranks(ranks_request)
                                await manager.send_personal_message(
                                    {"type": "users_ranks", "data": ranks}, websocket
                                )

                        # Multi-user faceit data endpoint
                        elif message["type"] == "get_users_faceit_elo_and_url":
                            if "steam_ids" in message:
                                faceit_request = RankRequest(
                                    steam_ids=message["steam_ids"]
                                )
                                faceit_data = await get_users_faceit_elo_and_url(
                                    faceit_request
                                )
                                await manager.send_personal_message(
                                    {"type": "users_faceit_data", "data": faceit_data},
                                    websocket,
                                )

                        # User profile data
                        elif message["type"] == "get_profile_data":
                            if "steam_id" in message:
                                profile_data = await get_profile_data(
                                    message["steam_id"]
                                )
                                await manager.send_personal_message(
                                    {
                                        "type": "profile_data",
                                        "steam_id": message["steam_id"],
                                        "data": profile_data,
                                    },
                                    websocket,
                                )

                        # Player matchup data
                        elif message["type"] == "get_player_matchup":
                            if (
                                "player1_steam_id" in message
                                and "player2_steam_id" in message
                            ):
                                matchup = await get_player_matchup(
                                    message["player1_steam_id"],
                                    message["player2_steam_id"],
                                )
                                await manager.send_personal_message(
                                    {"type": "player_matchup", "data": matchup},
                                    websocket,
                                )

                        # Teams map stats
                        elif message["type"] == "get_teams_map_stats":
                            if (
                                "team1_steam_ids" in message
                                and "team2_steam_ids" in message
                            ):
                                map_stats_request = TeamMapStatsRequest(
                                    team1_steam_ids=message["team1_steam_ids"],
                                    team2_steam_ids=message["team2_steam_ids"],
                                )
                                map_stats = await get_teams_map_stats(map_stats_request)
                                await manager.send_personal_message(
                                    {"type": "teams_map_stats", "data": map_stats},
                                    websocket,
                                )

                        # Players stats
                        elif message["type"] == "get_players_stats":
                            if "steam_ids" in message:
                                stats_request = PlayerStatsRequest(
                                    steam_ids=message["steam_ids"]
                                )
                                stats = await get_players_stats(stats_request)
                                await manager.send_personal_message(
                                    {"type": "players_stats", "data": stats}, websocket
                                )

                        # User last matches
                        elif message["type"] == "get_user_last_matches":
                            if "steam_id" in message:
                                limit = message.get("limit", 3)
                                matches = await get_user_last_matches(
                                    message["steam_id"], limit
                                )
                                await manager.send_personal_message(
                                    {
                                        "type": "user_last_matches",
                                        "steam_id": message["steam_id"],
                                        "data": matches,
                                    },
                                    websocket,
                                )

                        # Live matches
                        elif message["type"] == "get_live_matches":
                            matches = await get_live_matches()
                            await manager.send_personal_message(
                                {"type": "live_matches", "data": matches}, websocket
                            )

                        # Faceit elo by names
                        elif message["type"] == "get_faceit_elo_by_names":
                            if "player_names" in message:
                                names_request = PlayerNamesRequest(
                                    player_names=message["player_names"]
                                )
                                elo_data = await get_faceit_elo_by_names(names_request)
                                await manager.send_personal_message(
                                    {"type": "faceit_elo_by_names", "data": elo_data},
                                    websocket,
                                )

                        else:
                            logger.info(
                                f"Received unknown message type: {message['type']}"
                            )
                            await manager.send_personal_message(
                                {
                                    "type": "error",
                                    "message": f"Unknown message type: {message['type']}",
                                },
                                websocket,
                            )

                    except Exception as e:
                        logger.error(
                            f"Error processing {message['type']} request: {str(e)}"
                        )
                        await manager.send_personal_message(
                            {
                                "type": "error",
                                "request_type": message.get("type"),
                                "message": f"Error processing request: {str(e)}",
                            },
                            websocket,
                        )

                else:
                    logger.info(f"Received message without type: {message}")
                    await manager.send_personal_message(
                        {
                            "type": "error",
                            "message": "Message must include a 'type' field",
                        },
                        websocket,
                    )

            except json.JSONDecodeError:
                logger.error(f"Received invalid JSON: {data}")
                await manager.send_personal_message(
                    {"type": "error", "message": "Invalid JSON"}, websocket
                )
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
        await manager.disconnect(websocket)


@app.get("/get_renown_stats")
@cache_manager.cached(ttl_seconds=60)  # Cache for 1 minute
async def get_renown_stats():
    """Get comprehensive Renown statistics including user counts and live matches."""
    try:
        # Get total player count
        total_count = Player.select().count()

        # Get today's date
        today = date.today()

        # Count distinct players who played in matches that finished today
        players_played_today = (
            Player.select()
            .join(Stat, on=(Player.id == Stat.player))
            .join(Match, on=(Stat.match == Match.id))
            .where(
                (Match.finished == True)
                & (Match.status != "CANCELLED")
                & (fn.date(Match.match_started_event_at) == today)
            )
            .distinct()
            .count()
        )

        # Count distinct players who have played in at least 1 non-cancelled match
        players_with_matches = (
            Player.select()
            .join(Stat, on=(Player.id == Stat.player))
            .join(Match, on=(Stat.match == Match.id))
            .where(Match.status != "CANCELLED")
            .distinct()
            .count()
        )

        # Count matches created today (non-cancelled)
        matches_today = (
            Match.select()
            .where((Match.status != "CANCELLED") & (fn.date(Match.created_at) == today))
            .count()
        )

        # Get live matches count
        live_matches_count = 0
        try:
            url = "https://renown.gg/api/live-matches"
            cookies = {"auth_token": RENOWN_AUTH_TOKEN}
            response = requests.get(url, cookies=cookies)
            response.raise_for_status()
            live_matches_data = response.json()

            # Extract the liveMatches array from the response
            if (
                isinstance(live_matches_data, dict)
                and "liveMatches" in live_matches_data
            ):
                live_matches_list = live_matches_data["liveMatches"]
                live_matches_count = (
                    len(live_matches_list) if isinstance(live_matches_list, list) else 0
                )
            else:
                live_matches_count = 0
        except Exception as e:
            logger.error(f"Error fetching live matches count: {str(e)}")
            live_matches_count = 0

        return {
            "status": "success",
            "renown_total_user_count": total_count,
            "active_users_today": players_played_today,
            "renown_active_user_count": players_with_matches,
            "matches_today": matches_today,
            "live_matches_count": live_matches_count,
        }
    except Exception as e:
        logger.error(f"Error getting renown stats: {str(e)}")
        return {"status": "error", "message": str(e)}


class ChatMessageRequest(BaseModel):
    username: str
    message: str

