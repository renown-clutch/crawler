import os
import sys
from datetime import datetime
from typing import Dict, Optional
from zoneinfo import ZoneInfo

# Add the parent directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clutch.models import (
    Drop,
    Match,
    MatchCrawlTask,
    Player,
    Stat,
)


def create_or_update_player(user_data: Dict) -> Player:
    """Create or update a player from match data."""
    steam_id = int(user_data["steam_id"])
    player, created = Player.get_or_create(
        steam_id=steam_id,
        defaults={
            "name": user_data["nickname"],
            "elo": user_data["elo_rating"],
            "country": user_data["country"]["alpha2"],
            "avatar_url": user_data["steam_avatar"],
            "registered_since": datetime.now(tz=ZoneInfo("UTC")),
        },
    )

    if created:
        print(f"Created new player {player.name} (steam_id: {steam_id})")
    else:
        # Update existing player if needed
        if player.name != user_data["nickname"]:
            player.name = user_data["nickname"]
        if player.elo != user_data["elo_rating"]:
            player.elo = user_data["elo_rating"]
        if player.country != user_data["country"]["alpha2"]:
            player.country = user_data["country"]["alpha2"]
        if player.avatar_url != user_data["steam_avatar"]:
            player.avatar_url = user_data["steam_avatar"]
        player.save()

    return player


def process_match_data(match_data: Dict) -> Optional[Match]:
    """Process match data and create/update database records."""
    try:
        # Create or update match

        # if match status is CANCELLED it doesnt have match_started_event_at
        if match_data["status"] == "CANCELLED":
            match_started_event_at = None
            team1_current_score = 0
            team2_current_score = 0
            server_city = None
            server_country = None
        else:
            match_started_event_at = datetime.fromisoformat(
                match_data["match_started_event_at"].replace("$D", "")
            ).replace(tzinfo=ZoneInfo("UTC"))
            team1_current_score = match_data["team1_current_score"]
            team2_current_score = match_data["team2_current_score"]
            server_city = match_data["ServerLocation"]["city"]
            server_country = match_data["ServerLocation"]["iso"]
            if team1_current_score is None:
                team1_current_score = 0
            if team2_current_score is None:
                team2_current_score = 0

        match_id = match_data["id"]
        match, created = Match.get_or_create(
            match_id=match_id,
            defaults={
                "season": match_data["season"]["name"],
                "map": match_data["map"]["display_name"],
                "team1_score": team1_current_score,
                "team2_score": team2_current_score,
                "created_at": datetime.fromisoformat(
                    match_data["created_at"].replace("$D", "")
                ).replace(tzinfo=ZoneInfo("UTC")),
                "status": match_data["status"],
                "cancel_reason": match_data["cancel_reason"],
                "finished": match_data["finished"],
                "finished_at": datetime.fromisoformat(
                    match_data["finished_at"].replace("$D", "")
                ).replace(tzinfo=ZoneInfo("UTC"))
                if match_data["finished_at"]
                else None,
                "winner": match_data["winner"],
                "demo_saved": match_data["demo_saved"],
                "match_started_event_at": match_started_event_at,
                "leetify_match_id": match_data["leetify_match_id"],
                "demo_file_name": match_data["demo_file_name"],
                "server_city": server_city,
                "server_country": server_country,
            },
        )

        # Process team captains
        if match_data["team_1_captain"]:
            team1_captain = Player.get_or_none(
                name=match_data["team_1_captain"]["nickname"]
            )
            if team1_captain:
                match.team1_captain = team1_captain

        if match_data["team_2_captain"]:
            team2_captain = Player.get_or_none(
                name=match_data["team_2_captain"]["nickname"]
            )
            if team2_captain:
                match.team2_captain = team2_captain

        match.save()

        # Process participants and their stats
        for participant in match_data["MatchParticipant"]:
            user_data = participant["user"]
            player = create_or_update_player(user_data)

            # if match is cancelled, there is no elo change
            if match_data["status"] == "CANCELLED":
                elo_change = 0
                elo_before_match = 0
                leetify_rating = 0
            else:
                elo_change = participant["user"]["EloChange"][0]["elo_change"]
                elo_before_match = participant["user"]["EloChange"][0][
                    "user_elo_before"
                ]
                leetify_rating = participant["leetify_rating"]
                if leetify_rating is None:
                    leetify_rating = 0

            # Create or update stat
            stat, _ = Stat.get_or_create(
                player=player,
                match=match,
                defaults={
                    "team": participant["team"],
                    "kicked": participant["kicked"],
                    "kills": participant["kills"],
                    "assists": participant["assists"],
                    "deaths": participant["deaths"],
                    "damage_dealt": participant["damage_dealt"],
                    "kills_with_headshot": participant["kills_with_headshot"],
                    "two_ks": participant["two_ks"],
                    "three_ks": participant["three_ks"],
                    "four_ks": participant["four_ks"],
                    "five_ks": participant["five_ks"],
                    "entry_attempts": participant["entry_attempts"],
                    "entry_successes": participant["entry_successes"],
                    "utility_damage": participant["utility_damage"],
                    "flashes_enemies_blinded": participant["flashes_enemies_blinded"],
                    "mvps": participant["mvps"],
                    "party_id": participant["partyId"],
                    "abandon": participant["abandon"],
                    "leetify_rating": leetify_rating,
                    "elo_change": elo_change,
                    "elo_before_match": elo_before_match,
                },
            )

        # Process drops if any
        if "Drop" in match_data and match_data["Drop"]:
            drop_data = match_data["Drop"]
            skin_data = drop_data["Skin"]
            user_data = drop_data["user"]

            # player = create_or_update_player(user_data)
            player = Player.get_or_none(name=user_data["nickname"])

            Drop.create(
                player=player,
                match=match,
                skin_name=skin_data["name"],
                asset_id=skin_data["asset_id"],
                icon_hash=skin_data["icon_hash"],
                exterior=skin_data["exterior"],
                doppler_phase=skin_data["doppler_phase"],
                buff_price=skin_data["buff_price"],
                name_color=skin_data["name_color"],
                float_value=skin_data["float_value"],
                rarity_name=skin_data["rarity_name"],
                steam_market_price=skin_data["steam_market"],
            )

        return match

    except Exception as e:
        print(f"Error processing match {match_id}: {e}")
        raise e
        return None


def main():
    """Process all parsed matches."""
    # Get all matches that have been parsed but not processed
    matches = MatchCrawlTask.select().where(
        (MatchCrawlTask.processed == True)  # noqa: E712
        & (MatchCrawlTask.parsed_match_json.is_null(False))
    )

    for match_task in matches:
        print(f"Processing match {match_task.match_id}")
        match_data = match_task.parsed_match_json["matchPageData"]
        if not match_data:
            print(f"No parsed data for match {match_task.match_id}")
            continue

        if not match_data["created_at"]:
            break

        match = process_match_data(match_data)
        if match:
            print(f"Successfully processed match {match_task.match_id}")
        else:
            print(f"Failed to process match {match_task.match_id}")


if __name__ == "__main__":
    main()
