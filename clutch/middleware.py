import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from clutch.db.models import ApiRequestLog, db


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Get request start time
        start_time = time.time()

        # Get client IP
        client_ip = request.client.host if request.client else None

        # Get request parameters
        request_params = dict(request.query_params)

        # Get request body if it exists
        request_body = None
        if request.method in ["POST", "PUT", "PATCH"]:
            try:
                request_body = await request.json()
            except:
                pass

        # Process the request
        response = await call_next(request)

        # Calculate response time
        response_time = int(
            (time.time() - start_time) * 1000
        )  # Convert to milliseconds

        # Log the request asynchronously
        await self.log_request(
            path=str(request.url.path),
            method=request.method,
            client_ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            request_params=request_params,
            request_body=request_body,
            response_status=response.status_code,
            response_time_ms=response_time,
        )

        return response

    async def log_request(
        self,
        path: str,
        method: str,
        client_ip: str,
        user_agent: str,
        request_params: dict,
        request_body: dict,
        response_status: int,
        response_time_ms: int,
    ):
        try:
            # Create a new log entry using peewee_async's create method
            await db.create_async(
                ApiRequestLog,
                path=path,
                method=method,
                client_ip=client_ip,
                user_agent=user_agent,
                request_params=request_params,
                request_body=request_body,
                response_status=response_status,
                response_time_ms=response_time_ms,
            )
        except Exception as e:
            # Log the error but don't interrupt the request
            print(f"Error logging request: {str(e)}")
