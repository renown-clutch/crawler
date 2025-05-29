import logging
from datetime import datetime, timezone
from typing import Any, Dict

import socketio

# Import all the API functions we need to reuse
from api import (
    PlayerNamesRequest,
    PlayerStatsRequest,
    RankRequest,
    TeamMapStatsRequest,
)
from api import (
    get_faceit_elo_by_names as api_get_faceit_elo_by_names,
)
from api import (
    get_live_matches as api_get_live_matches,
)
from api import (
    get_player_matchup as api_get_player_matchup,
)
from api import (
    get_players_stats as api_get_players_stats,
)
from api import (
    get_profile_data as api_get_profile_data,
)
from api import (
    get_teams_map_stats as api_get_teams_map_stats,
)
from api import (
    get_user_bans as api_get_user_bans,
)
from api import (
    get_user_drops as api_get_user_drops,
)
from api import (
    get_user_faceit as api_get_user_faceit,
)
from api import (
    get_user_faceit_elo as api_get_user_faceit_elo,
)
from api import (
    get_user_faceit_elo_and_url as api_get_user_faceit_elo_and_url,
)
from api import (
    get_user_faceit_url as api_get_user_faceit_url,
)
from api import (
    get_user_last_matches as api_get_user_last_matches,
)
from api import (
    get_user_renown_stats as api_get_user_renown_stats,
)
from api import (
    get_users_faceit_elo_and_url as api_get_users_faceit_elo_and_url,
)
from api import (
    get_users_ranks as api_get_users_ranks,
)
from clutch.chat import chat_manager

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

# Create Socket.IO server with CORS enabled and async_mode set to 'asgi'
sio = socketio.AsyncServer(
    cors_allowed_origins="*", logger=False, engineio_logger=False, async_mode="asgi"
)

# Store client information
client_info: Dict[str, Dict[str, Any]] = {}


@sio.event
async def connect(sid, environ, auth):
    """Handle client connection"""
    # Extract client_id and steam_id from auth data
    client_id = auth.get("client_id") if auth else None
    steam_id = auth.get("steam_id") if auth else None

    if not client_id:
        logger.warning(f"Client {sid} connected without client_id")
        await sio.disconnect(sid)
        return False

    # Store client information
    client_info[sid] = {
        "client_id": client_id,
        "steam_id": steam_id,
        "connected_at": datetime.now(timezone.utc),
    }

    logger.info(f"Client {client_id} connected with sid {sid}, steam_id: {steam_id}")

    # Join room based on client_id for targeted messaging
    await sio.enter_room(sid, f"client_{client_id}")

    # If steam_id is provided, also join steam_id room
    if steam_id:
        await sio.enter_room(sid, f"steam_{steam_id}")

    return True


@sio.event
async def disconnect(sid):
    """Handle client disconnection"""
    if sid in client_info:
        client_data = client_info[sid]
        logger.info(f"Client {client_data['client_id']} disconnected (sid: {sid})")
        del client_info[sid]


@sio.event
async def ping(sid, data=None):
    """Handle ping requests"""
    await sio.emit("pong", room=sid)


@sio.event
async def send_chat_message(sid, data):
    """Handle chat message sending"""
    try:
        username = data.get("username")
        message = data.get("message")

        if not username or not message:
            await sio.emit(
                "error", {"message": "Username and message are required"}, room=sid
            )
            return

        # Add message to chat manager - this will trigger the SocketIOConnectionManager
        # to broadcast the message to all clients (including the sender)
        await chat_manager.add_message(username, message)

    except Exception as e:
        logger.error(f"Error sending chat message: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error sending message: {str(e)}"}, room=sid
        )


@sio.event
async def get_recent_messages(sid, data=None):
    """Handle getting recent chat messages"""
    try:
        if data is None:
            data = {}
        limit = data.get("limit", 50)
        messages = await chat_manager.get_recent_messages(limit)
        await sio.emit("recent_messages", {"data": messages}, room=sid)

    except Exception as e:
        logger.error(f"Error getting recent messages: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting messages: {str(e)}"}, room=sid
        )


@sio.event
async def get_user_faceit(sid, data):
    """Handle getting user faceit data"""
    try:
        if data is None:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        steam_id = data.get("steam_id")
        if not steam_id:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        faceit_data = await api_get_user_faceit(steam_id)
        await sio.emit(
            "faceit_data",
            {
                "request_type": "get_user_faceit",
                "steam_id": steam_id,
                "data": faceit_data,
            },
            room=sid,
        )

    except Exception as e:
        logger.error(f"Error getting user faceit: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting faceit data: {str(e)}"}, room=sid
        )


@sio.event
async def get_user_faceit_elo(sid, data):
    """Handle getting user faceit elo"""
    try:
        if data is None:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        steam_id = data.get("steam_id")
        if not steam_id:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        elo = await api_get_user_faceit_elo(steam_id)
        await sio.emit(
            "faceit_data",
            {"request_type": "get_user_faceit_elo", "steam_id": steam_id, "data": elo},
            room=sid,
        )

    except Exception as e:
        logger.error(f"Error getting user faceit elo: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting faceit elo: {str(e)}"}, room=sid
        )


@sio.event
async def get_user_faceit_url(sid, data):
    """Handle getting user faceit url"""
    try:
        if data is None:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        steam_id = data.get("steam_id")
        if not steam_id:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        url = await api_get_user_faceit_url(steam_id)
        await sio.emit(
            "faceit_data",
            {"request_type": "get_user_faceit_url", "steam_id": steam_id, "data": url},
            room=sid,
        )

    except Exception as e:
        logger.error(f"Error getting user faceit url: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting faceit url: {str(e)}"}, room=sid
        )


@sio.event
async def get_user_faceit_elo_and_url(sid, data):
    """Handle getting user faceit elo and url"""
    try:
        if data is None:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        steam_id = data.get("steam_id")
        if not steam_id:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        result = await api_get_user_faceit_elo_and_url(steam_id)
        await sio.emit(
            "faceit_data",
            {
                "request_type": "get_user_faceit_elo_and_url",
                "steam_id": steam_id,
                "data": result,
            },
            room=sid,
        )

    except Exception as e:
        logger.error(f"Error getting user faceit elo and url: {str(e)}")
        await sio.emit(
            "error",
            {"message": f"Error getting faceit elo and url: {str(e)}"},
            room=sid,
        )


@sio.event
async def get_user_bans(sid, data):
    """Handle getting user bans"""
    try:
        if data is None:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        steam_id = data.get("steam_id")
        if not steam_id:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        bans = await api_get_user_bans(steam_id)
        logger.info(f"User {steam_id} has {len(bans)} bans")
        await sio.emit("user_bans", {"steam_id": steam_id, "data": bans}, room=sid)

    except Exception as e:
        logger.error(f"Error getting user bans: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting user bans: {str(e)}"}, room=sid
        )


@sio.event
async def get_user_drops(sid, data):
    """Handle getting user drops"""
    try:
        if data is None:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        steam_id = data.get("steam_id")
        if not steam_id:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        drops = await api_get_user_drops(steam_id)
        await sio.emit("user_drops", {"steam_id": steam_id, "data": drops}, room=sid)

    except Exception as e:
        logger.error(f"Error getting user drops: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting user drops: {str(e)}"}, room=sid
        )


@sio.event
async def get_users_ranks(sid, data):
    """Handle getting multiple users ranks"""
    try:
        if data is None:
            await sio.emit("error", {"message": "steam_ids is required"}, room=sid)
            return

        steam_ids = data.get("steam_ids")
        if not steam_ids:
            await sio.emit("error", {"message": "steam_ids is required"}, room=sid)
            return

        ranks_request = RankRequest(steam_ids=steam_ids)
        ranks = await api_get_users_ranks(ranks_request)
        await sio.emit("users_ranks", {"data": ranks}, room=sid)

    except Exception as e:
        logger.error(f"Error getting users ranks: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting users ranks: {str(e)}"}, room=sid
        )


@sio.event
async def get_users_faceit_elo_and_url(sid, data):
    """Handle getting multiple users faceit elo and url"""
    try:
        if data is None:
            await sio.emit("error", {"message": "steam_ids is required"}, room=sid)
            return

        steam_ids = data.get("steam_ids")
        if not steam_ids:
            await sio.emit("error", {"message": "steam_ids is required"}, room=sid)
            return

        faceit_request = RankRequest(steam_ids=steam_ids)
        faceit_data = await api_get_users_faceit_elo_and_url(faceit_request)
        await sio.emit("users_faceit_data", {"data": faceit_data}, room=sid)

    except Exception as e:
        logger.error(f"Error getting users faceit data: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting users faceit data: {str(e)}"}, room=sid
        )


@sio.event
async def get_profile_data(sid, data):
    """Handle getting user profile data"""
    try:
        if data is None:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        steam_id = data.get("steam_id")
        if not steam_id:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        profile_data = await api_get_profile_data(steam_id)
        await sio.emit(
            "profile_data", {"steam_id": steam_id, "data": profile_data}, room=sid
        )

    except Exception as e:
        logger.error(f"Error getting profile data: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting profile data: {str(e)}"}, room=sid
        )


@sio.event
async def get_player_matchup(sid, data):
    """Handle getting player matchup data"""
    try:
        if data is None:
            await sio.emit(
                "error",
                {"message": "Both player1_steam_id and player2_steam_id are required"},
                room=sid,
            )
            return

        player1_steam_id = data.get("player1_steam_id")
        player2_steam_id = data.get("player2_steam_id")

        if not player1_steam_id or not player2_steam_id:
            await sio.emit(
                "error",
                {"message": "Both player1_steam_id and player2_steam_id are required"},
                room=sid,
            )
            return

        matchup = await api_get_player_matchup(player1_steam_id, player2_steam_id)
        await sio.emit("player_matchup", {"data": matchup}, room=sid)

    except Exception as e:
        logger.error(f"Error getting player matchup: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting player matchup: {str(e)}"}, room=sid
        )


@sio.event
async def get_teams_map_stats(sid, data):
    """Handle getting teams map stats"""
    try:
        if data is None:
            await sio.emit(
                "error",
                {"message": "Both team1_steam_ids and team2_steam_ids are required"},
                room=sid,
            )
            return

        team1_steam_ids = data.get("team1_steam_ids")
        team2_steam_ids = data.get("team2_steam_ids")

        if not team1_steam_ids or not team2_steam_ids:
            await sio.emit(
                "error",
                {"message": "Both team1_steam_ids and team2_steam_ids are required"},
                room=sid,
            )
            return

        map_stats_request = TeamMapStatsRequest(
            team1_steam_ids=team1_steam_ids, team2_steam_ids=team2_steam_ids
        )
        map_stats = await api_get_teams_map_stats(map_stats_request)
        await sio.emit("teams_map_stats", {"data": map_stats}, room=sid)

    except Exception as e:
        logger.error(f"Error getting teams map stats: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting teams map stats: {str(e)}"}, room=sid
        )


@sio.event
async def get_players_stats(sid, data):
    """Handle getting players stats"""
    try:
        if data is None:
            await sio.emit("error", {"message": "steam_ids is required"}, room=sid)
            return

        steam_ids = data.get("steam_ids")
        if not steam_ids:
            await sio.emit("error", {"message": "steam_ids is required"}, room=sid)
            return

        stats_request = PlayerStatsRequest(steam_ids=steam_ids)
        stats = await api_get_players_stats(stats_request)
        await sio.emit("players_stats", {"data": stats}, room=sid)

    except Exception as e:
        logger.error(f"Error getting players stats: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting players stats: {str(e)}"}, room=sid
        )


@sio.event
async def get_user_last_matches(sid, data):
    """Handle getting user last matches"""
    try:
        if data is None:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        steam_id = data.get("steam_id")
        if not steam_id:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        limit = data.get("limit", 3)
        matches = await api_get_user_last_matches(steam_id, limit)
        await sio.emit(
            "user_last_matches", {"steam_id": steam_id, "data": matches}, room=sid
        )

    except Exception as e:
        logger.error(f"Error getting user last matches: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting user last matches: {str(e)}"}, room=sid
        )


@sio.event
async def get_live_matches(sid, data=None):
    """Handle getting live matches"""
    try:
        matches = await api_get_live_matches()
        await sio.emit("live_matches", {"data": matches}, room=sid)

    except Exception as e:
        logger.error(f"Error getting live matches: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting live matches: {str(e)}"}, room=sid
        )


@sio.event
async def get_faceit_elo_by_names(sid, data):
    """Handle getting faceit elo by player names"""
    try:
        if data is None:
            await sio.emit("error", {"message": "player_names is required"}, room=sid)
            return

        player_names = data.get("player_names")
        if not player_names:
            await sio.emit("error", {"message": "player_names is required"}, room=sid)
            return

        names_request = PlayerNamesRequest(player_names=player_names)
        elo_data = await api_get_faceit_elo_by_names(names_request)
        await sio.emit("faceit_elo_by_names", {"data": elo_data}, room=sid)

    except Exception as e:
        logger.error(f"Error getting faceit elo by names: {str(e)}")
        await sio.emit(
            "error",
            {"message": f"Error getting faceit elo by names: {str(e)}"},
            room=sid,
        )


@sio.event
async def get_user_renown_stats(sid, data):
    """Handle getting user renown stats"""
    try:
        if data is None:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        steam_id = data.get("steam_id")
        if not steam_id:
            await sio.emit("error", {"message": "steam_id is required"}, room=sid)
            return

        stats = await api_get_user_renown_stats(steam_id)
        await sio.emit(
            "user_renown_stats", {"steam_id": steam_id, "data": stats}, room=sid
        )

    except Exception as e:
        logger.error(f"Error getting user renown stats: {str(e)}")
        await sio.emit(
            "error", {"message": f"Error getting user renown stats: {str(e)}"}, room=sid
        )


# Admin functions for broadcasting
async def broadcast_to_client(message: dict, client_id: str):
    """Broadcast message to specific client"""
    await sio.emit("broadcast", message, room=f"client_{client_id}")


async def broadcast_to_steam_id(message: dict, steam_id: int):
    """Broadcast message to specific steam_id"""
    await sio.emit("broadcast", message, room=f"steam_{steam_id}")


async def broadcast_to_all(message: dict):
    """Broadcast message to all connected clients"""
    await sio.emit("broadcast", message)


def get_connected_clients_info():
    """Get information about connected clients"""
    clients_by_id = {}
    for sid, info in client_info.items():
        client_id = info["client_id"]
        if client_id not in clients_by_id:
            clients_by_id[client_id] = []
        clients_by_id[client_id].append(
            {
                "sid": sid,
                "steam_id": info.get("steam_id"),
                "connected_at": info["connected_at"].isoformat(),
            }
        )

    return {
        "total_connections": len(client_info),
        "unique_clients": len(clients_by_id),
        "clients": clients_by_id,
    }


# Set the connection manager for chat (moved outside of __main__ block)
class SocketIOConnectionManager:
    async def broadcast(self, message):
        # Extract the message type and emit the appropriate event
        message_type = message.get("type")
        if message_type == "chat_message":
            # Emit chat message to all clients
            await sio.emit("chat_message", message)
        else:
            # For other types, use the general broadcast event
            await sio.emit("broadcast", message)


# Set the connection manager immediately
chat_manager.set_connection_manager(SocketIOConnectionManager())

# Create ASGI app after defining all events
app = socketio.ASGIApp(sio)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=80)
