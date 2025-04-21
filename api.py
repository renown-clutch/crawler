import logging
from datetime import datetime, timedelta, timezone

import requests
from decouple import config
from fastapi import FastAPI
from pydantic import BaseModel

from clutch.db.models import Ban, Drop, FaceitProfile, Player

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
        faceit_json = get_faceit_profile(steam_id)
        faceit_profile = FaceitProfile.create(
            player=player,
            faceit_json=faceit_json,
            faceit_url=faceit_json["faceit_url"],
            faceit_elo=faceit_json["games"]["cs2"]["faceit_elo"],
        )

    else:
        logger.info(f"Faceit profile found for {steam_id}")
        faceit_profile = faceit_profile.first()
        current_time = datetime.now(timezone.utc)
        if faceit_profile.last_updated_at < current_time - timedelta(hours=5):
            logger.info(f"Updating faceit outdated profile for {steam_id}")
            faceit_json = get_faceit_profile(steam_id)
            FaceitProfile.update(faceit_json=faceit_json).where(
                FaceitProfile.player == player
            ).execute()
    return faceit_profile.faceit_json


@app.get("/get_user_faceit_elo")
async def get_user_faceit_elo(steam_id: int):
    faceit_data = await get_user_faceit(steam_id)
    return faceit_data.faceit_elo


@app.get("/get_user_faceit_url")
async def get_user_faceit_url(steam_id: int):
    faceit_data = await get_user_faceit(steam_id)
    url = faceit_data["faceit_url"].replace("{lang}/", "")
    return url


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
