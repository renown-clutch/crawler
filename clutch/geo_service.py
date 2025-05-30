import asyncio
import logging
import time
from functools import lru_cache
from typing import Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)


class GeoLocationService:
    """Service for converting IP addresses to geographic coordinates."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_time = 0
        self._min_request_interval = (
            1.0  # Minimum seconds between requests to avoid rate limiting
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=5.0)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _rate_limit(self):
        """Implement basic rate limiting."""
        current_time = time.time()
        time_since_last = current_time - self._last_request_time
        if time_since_last < self._min_request_interval:
            await asyncio.sleep(self._min_request_interval - time_since_last)
        self._last_request_time = time.time()

    @lru_cache(maxsize=1000)
    def _is_private_ip(self, ip: str) -> bool:
        """Check if IP is private/local."""
        try:
            parts = ip.split(".")
            if len(parts) != 4:
                return True  # Invalid IP, treat as private

            first = int(parts[0])
            second = int(parts[1])

            # Private IP ranges
            if first == 10:  # 10.0.0.0/8
                return True
            if first == 172 and 16 <= second <= 31:  # 172.16.0.0/12
                return True
            if first == 192 and second == 168:  # 192.168.0.0/16
                return True
            if first == 127:  # 127.0.0.0/8 (localhost)
                return True

            return False
        except (ValueError, IndexError):
            return True  # Invalid IP, treat as private

    async def get_location_from_ip(self, ip_address: str) -> Dict[str, Optional[str]]:
        """
        Get geographic location data from IP address.

        Returns a dictionary with location data or None values if lookup fails.
        """
        default_result = {
            "country": None,
            "country_code": None,
            "region": None,
            "city": None,
            "latitude": None,
            "longitude": None,
            "timezone": None,
            "isp": None,
        }

        # Skip private IPs
        if self._is_private_ip(ip_address):
            logger.debug(f"Skipping geo lookup for private IP: {ip_address}")
            return default_result

        try:
            await self._rate_limit()
            session = await self._get_session()

            # Using ipapi.co - free tier allows 30,000 requests/month
            url = f"http://ipapi.co/{ip_address}/json/"

            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()

                    # Check if we got an error response
                    if data.get("error"):
                        logger.warning(
                            f"Geo API error for IP {ip_address}: {data.get('reason', 'Unknown error')}"
                        )
                        return default_result

                    result = {
                        "country": data.get("country_name"),
                        "country_code": data.get("country_code"),
                        "region": data.get("region"),
                        "city": data.get("city"),
                        "latitude": data.get("latitude"),
                        "longitude": data.get("longitude"),
                        "timezone": data.get("timezone"),
                        "isp": data.get("org"),
                    }

                    logger.debug(
                        f"Geo lookup successful for IP {ip_address}: {result['city']}, {result['country']}"
                    )
                    return result
                else:
                    logger.warning(
                        f"Geo API returned status {response.status} for IP {ip_address}"
                    )
                    return default_result

        except asyncio.TimeoutError:
            logger.warning(f"Geo lookup timeout for IP {ip_address}")
            return default_result
        except Exception as e:
            logger.error(f"Error in geo lookup for IP {ip_address}: {str(e)}")
            return default_result


# Global instance
geo_service = GeoLocationService()


async def lookup_ip_location(ip_address: str) -> Dict[str, Optional[str]]:
    """Convenience function for IP location lookup."""
    return await geo_service.get_location_from_ip(ip_address)


# Cleanup function to be called on app shutdown
async def cleanup_geo_service():
    """Close the geo service session."""
    await geo_service.close()
