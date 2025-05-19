import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CacheManager:
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._key_metadata: Dict[
            str, Dict[str, Any]
        ] = {}  # Stores path and other metadata for each key

    def _generate_key(self, func_name: str, *args, **kwargs) -> str:
        """Generate a unique cache key based on function name and arguments."""
        # Sort kwargs to ensure consistent key generation
        sorted_kwargs = sorted(kwargs.items())
        key_parts = [func_name, str(args), str(sorted_kwargs)]
        key_string = json.dumps(key_parts, sort_keys=True)

        # Store metadata about this key
        path = None
        # Try to extract path information from args or kwargs
        if len(args) > 0 and hasattr(args[0], "__dict__"):
            # For request objects with path
            req = args[0]
            if hasattr(req, "url") and hasattr(req.url, "path"):
                path = req.url.path

        # Store metadata for this key
        key = hashlib.md5(key_string.encode()).hexdigest()
        self._key_metadata[key] = {
            "func_name": func_name,
            "path": path,
            "created_at": datetime.now(timezone.utc).timestamp(),
        }

        return key

    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache if it exists and hasn't expired."""
        if key not in self._cache:
            return None

        cache_entry = self._cache[key]
        if datetime.now(timezone.utc).timestamp() > cache_entry["expires"]:
            del self._cache[key]
            if key in self._key_metadata:
                del self._key_metadata[key]
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
            self._key_metadata.pop(key, None)
        else:
            self._cache.clear()
            self._key_metadata.clear()

    def invalidate_pattern(self, pattern: str) -> int:
        """
        Invalidate all cache entries where metadata matches the given pattern.

        Args:
            pattern: A string pattern to match against paths or function names

        Returns:
            Number of cache entries invalidated
        """
        pattern_regex = re.compile(pattern)
        keys_to_invalidate = []

        # First pass: find all keys that need to be invalidated
        for key, metadata in self._key_metadata.items():
            func_name = metadata.get("func_name", "")
            path = metadata.get("path", "")

            # Match against function name or path
            if (path and pattern_regex.search(path)) or pattern_regex.search(func_name):
                keys_to_invalidate.append(key)

        # Second pass: invalidate the found keys
        for key in keys_to_invalidate:
            self.clear(key)

        logger.info(
            f"Invalidated {len(keys_to_invalidate)} cache entries matching pattern '{pattern}'"
        )
        return len(keys_to_invalidate)

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get cache usage statistics.

        Returns:
            Dictionary with cache statistics
        """
        now = datetime.now(timezone.utc).timestamp()
        active_entries = 0
        expired_entries = 0

        for key, entry in self._cache.items():
            if now <= entry["expires"]:
                active_entries += 1
            else:
                expired_entries += 1

        total_size = len(self._cache)

        # Group cache entries by function name
        function_counts = {}
        for key, metadata in self._key_metadata.items():
            func_name = metadata.get("func_name", "unknown")
            if func_name not in function_counts:
                function_counts[func_name] = 0
            function_counts[func_name] += 1

        return {
            "total_entries": total_size,
            "active_entries": active_entries,
            "expired_entries": expired_entries,
            "function_counts": function_counts,
        }

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
