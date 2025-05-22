import json
import logging
from datetime import datetime, timezone
from typing import Dict, List

import redis.asyncio as redis
from decouple import config

logger = logging.getLogger(__name__)

# Redis configuration
REDIS_HOST = config("REDIS_HOST", default="localhost")
REDIS_PORT = config("REDIS_PORT", default=6379, cast=int)
REDIS_DB = config("REDIS_DB", default=0, cast=int)

# Redis keys
CHAT_MESSAGES_KEY = "chat:messages"
MAX_MESSAGES = 100


class ChatManager:
    def __init__(self):
        self.redis_client = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True
        )
        self.connection_manager = None  # Will be set by api.py

    async def add_message(self, username: str, message: str) -> Dict:
        """Add a new chat message and broadcast it to all connected clients."""
        timestamp = datetime.now(timezone.utc).isoformat()
        message_data = {
            "username": username,
            "message": message,
            "timestamp": timestamp,
        }

        # Store message in Redis
        message_json = json.dumps(message_data)
        await self.redis_client.lpush(CHAT_MESSAGES_KEY, message_json)

        # Trim the list to keep only the latest messages
        await self.redis_client.ltrim(CHAT_MESSAGES_KEY, 0, MAX_MESSAGES - 1)

        # Broadcast message to all connected clients
        if self.connection_manager:
            await self.connection_manager.broadcast(
                {"type": "chat_message", "data": message_data}
            )

        return message_data

    async def get_recent_messages(self, limit: int = 50) -> List[Dict]:
        """Get recent chat messages."""
        messages = await self.redis_client.lrange(CHAT_MESSAGES_KEY, 0, limit - 1)
        return [json.loads(msg) for msg in messages]

    def set_connection_manager(self, manager):
        """Set the WebSocket connection manager."""
        self.connection_manager = manager


# Create a global chat manager instance
chat_manager = ChatManager()
