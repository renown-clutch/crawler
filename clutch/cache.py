import hashlib
import json
import logging
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CacheManager:
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}

    def _generate_key(self, func_name: str, *args, **kwargs) -> str:
        """Generate a unique cache key based on function name and arguments."""
        # Sort kwargs to ensure consistent key generation
        sorted_kwargs = sorted(kwargs.items())
        key_parts = [func_name, str(args), str(sorted_kwargs)]
        key_string = json.dumps(key_parts, sort_keys=True)
        return hashlib.md5(key_string.encode()).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache if it exists and hasn't expired."""
        if key not in self._cache:
            return None

        cache_entry = self._cache[key]
        if datetime.now(timezone.utc).timestamp() > cache_entry["expires"]:
            del self._cache[key]
            return None

        return cache_entry["data"]

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Set a value in cache with expiration time."""
        self._cache[key] = {
            "data": value,
            "expires": datetime.now(timezone.utc).timestamp() + ttl_seconds,
        }

    def clear(self, key: Optional[str] = None) -> None:
        """Clear specific cache entry or entire cache."""
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()

    def cached(self, ttl_seconds: int = 300):
        """Decorator to cache function results.

        Args:
            ttl_seconds: Time to live in seconds for the cache entry
        """

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            @wraps(func)
            async def wrapper(*args, **kwargs) -> T:
                # Skip caching if ttl is 0 or negative
                if ttl_seconds <= 0:
                    return await func(*args, **kwargs)

                cache_key = self._generate_key(func.__name__, *args, **kwargs)
                cached_result = self.get(cache_key)

                if cached_result is not None:
                    logger.debug(f"Cache hit for {func.__name__}")
                    return cached_result

                logger.debug(f"Cache miss for {func.__name__}")
                result = await func(*args, **kwargs)
                self.set(cache_key, result, ttl_seconds)
                return result

            return wrapper

        return decorator


# Create a global cache manager instance
cache_manager = CacheManager()
