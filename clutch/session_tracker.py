import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from clutch.db.models import SessionRequest, UserSession

logger = logging.getLogger(__name__)


class SessionTracker:
    """Service for tracking user sessions and requests."""

    def __init__(self):
        self._sessions: Dict[
            str, UserSession
        ] = {}  # In-memory cache of active sessions
        self._request_queue = asyncio.Queue()
        self._background_tasks = set()
        self._shutdown = False

    async def start_background_worker(self):
        """Start background worker for processing request logs."""
        if not self._shutdown:
            task = asyncio.create_task(self._process_request_queue())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    async def shutdown(self):
        """Shutdown the session tracker and wait for pending operations."""
        self._shutdown = True

        # Wait for queue to be processed
        await self._request_queue.join()

        # Cancel background tasks
        for task in self._background_tasks:
            task.cancel()

        # Wait for tasks to complete
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

    def _extract_ip_from_environ(self, environ: dict) -> str:
        """Extract IP address from ASGI environ."""
        # Check for forwarded headers first
        headers = dict(environ.get("headers", []))

        # Common proxy headers
        for header in ["x-forwarded-for", "x-real-ip", "cf-connecting-ip"]:
            if header.encode() in headers:
                ip = headers[header.encode()].decode()
                # Take first IP if multiple are present
                return ip.split(",")[0].strip()

        # Fall back to client address
        client = environ.get("client", ["127.0.0.1", 0])
        return client[0] if client else "127.0.0.1"

    def _extract_user_agent(self, environ: dict) -> Optional[str]:
        """Extract User-Agent from ASGI environ."""
        headers = dict(environ.get("headers", []))
        user_agent_key = b"user-agent"

        if user_agent_key in headers:
            return headers[user_agent_key].decode("utf-8", errors="ignore")
        return None

    async def create_session(
        self,
        session_id: str,
        environ: dict,
        client_id: Optional[str] = None,
        steam_id: Optional[int] = None,
    ) -> UserSession:
        """Create a new user session."""

        ip_address = self._extract_ip_from_environ(environ)
        user_agent = self._extract_user_agent(environ)

        logger.info(
            f"Creating session {session_id} for client {client_id}, IP: {ip_address}"
        )

        # Check if we have an existing session for this client_id
        existing_session = None
        if client_id:
            try:
                existing_session = (
                    UserSession.select()
                    .where(
                        (UserSession.client_id == client_id)
                        & (UserSession.is_active == True)
                    )
                    .first()
                )
            except Exception as e:
                logger.error(f"Error checking for existing session: {e}")

        # Determine connection count
        connection_count = 1
        if existing_session:
            connection_count = existing_session.connection_count + 1
            # Mark old session as inactive
            try:
                UserSession.update(
                    is_active=False, disconnected_at=datetime.now(timezone.utc)
                ).where(UserSession.id == existing_session.id).execute()
            except Exception as e:
                logger.error(f"Error updating old session: {e}")

        # Create new session
        try:
            session = UserSession.create(
                session_id=session_id,
                steam_id=steam_id,
                client_id=client_id,
                ip_address=ip_address,
                user_agent=user_agent,
                connection_count=connection_count,
            )

            # Cache in memory
            self._sessions[session_id] = session

            logger.info(f"Session created: {session_id}")
            return session

        except Exception as e:
            logger.error(f"Error creating session: {e}")
            # Return a mock session to prevent failures
            return UserSession(
                session_id=session_id,
                steam_id=steam_id,
                client_id=client_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )

    async def end_session(self, session_id: str):
        """Mark a session as ended."""
        try:
            # Update database
            UserSession.update(
                is_active=False, disconnected_at=datetime.now(timezone.utc)
            ).where(UserSession.session_id == session_id).execute()

            # Remove from cache
            if session_id in self._sessions:
                del self._sessions[session_id]

            logger.info(f"Session ended: {session_id}")

        except Exception as e:
            logger.error(f"Error ending session {session_id}: {e}")

    async def log_request(
        self,
        session_id: str,
        event_type: str,
        request_data: Optional[Dict] = None,
        response_data: Optional[Dict] = None,
        processing_time_ms: Optional[int] = None,
        success: bool = True,
        error_message: Optional[str] = None,
    ):
        """Log a request to the queue for background processing (completely non-blocking)."""

        # Get session from memory cache only (no DB lookup in main thread)
        session = self._sessions.get(session_id)

        # Extract session info for background processing
        steam_id = session.steam_id if session else None
        ip_address = session.ip_address if session else None

        # Queue ALL operations for background processing (completely non-blocking)
        request_log_data = {
            "session_id": session_id,
            "event_type": event_type,
            "request_data": request_data,
            "response_data": response_data,
            "processing_time_ms": processing_time_ms,
            "success": success,
            "error_message": error_message,
            "steam_id": steam_id,
            "ip_address": ip_address,
            "update_session_activity": True,  # Flag to update session in background
        }

        # This is the only async operation - queueing is very fast
        await self._request_queue.put(request_log_data)

    async def _process_request_queue(self):
        """Background worker to process request logs."""
        logger.info("Starting session request queue processor")

        while not self._shutdown:
            try:
                # Wait for request with timeout
                request_data = await asyncio.wait_for(
                    self._request_queue.get(), timeout=5.0
                )

                try:
                    # Get session (lookup in background if needed)
                    try:
                        session = UserSession.get(
                            UserSession.session_id == request_data["session_id"]
                        )
                    except UserSession.DoesNotExist:
                        logger.warning(
                            f"Session {request_data['session_id']} not found for request logging"
                        )
                        continue

                    # Update session activity if requested (moved to background)
                    if request_data.get("update_session_activity", False):
                        try:
                            UserSession.update(
                                last_activity_at=datetime.now(timezone.utc),
                                total_requests=UserSession.total_requests + 1,
                            ).where(
                                UserSession.session_id == request_data["session_id"]
                            ).execute()
                        except Exception as e:
                            logger.error(
                                f"Error updating session activity in background: {e}"
                            )

                    # Create request log
                    SessionRequest.create(
                        session=session,
                        event_type=request_data["event_type"],
                        request_data=request_data.get("request_data"),
                        response_data=request_data.get("response_data"),
                        processing_time_ms=request_data.get("processing_time_ms"),
                        success=request_data["success"],
                        error_message=request_data.get("error_message"),
                        steam_id=request_data.get("steam_id"),
                        ip_address=request_data.get("ip_address"),
                    )

                except Exception as e:
                    logger.error(f"Error saving request log: {e}")
                finally:
                    self._request_queue.task_done()

            except asyncio.TimeoutError:
                # Timeout is normal, just continue
                continue
            except Exception as e:
                logger.error(f"Error in request queue processor: {e}")
                await asyncio.sleep(1)  # Brief pause before retrying

        logger.info("Session request queue processor stopped")

    def get_session(self, session_id: str) -> Optional[UserSession]:
        """Get session from cache or database."""
        session = self._sessions.get(session_id)
        if not session:
            try:
                session = UserSession.get(UserSession.session_id == session_id)
                self._sessions[session_id] = session
            except UserSession.DoesNotExist:
                return None
        return session

    async def update_steam_id(self, session_id: str, steam_id: int):
        """Update the steam_id for a session after authentication."""
        try:
            UserSession.update(steam_id=steam_id).where(
                UserSession.session_id == session_id
            ).execute()

            # Update cache
            if session_id in self._sessions:
                self._sessions[session_id].steam_id = steam_id

            logger.info(f"Updated session {session_id} with steam_id {steam_id}")

        except Exception as e:
            logger.error(f"Error updating steam_id for session {session_id}: {e}")


# Global instance
session_tracker = SessionTracker()


# Request timing decorator
def track_request_time(session_id: str, event_type: str):
    """Decorator to track request processing time."""

    def decorator(func):
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            success = True
            error_message = None
            response_data = None

            try:
                result = await func(*args, **kwargs)
                response_data = result if isinstance(result, dict) else None
                return result
            except Exception as e:
                success = False
                error_message = str(e)
                raise
            finally:
                processing_time = int((time.time() - start_time) * 1000)
                await session_tracker.log_request(
                    session_id=session_id,
                    event_type=event_type,
                    processing_time_ms=processing_time,
                    success=success,
                    error_message=error_message,
                    response_data=response_data,
                )

        return wrapper

    return decorator
