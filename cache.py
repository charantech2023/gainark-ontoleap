"""
GainARK OntoLeap — Dual-Tier Intelligent Content & Response Cache

Provides a high-efficiency caching layer for crawled web pages, OpenAPI specs,
and sitemaps:
1. Tier 1 (Memory LRU): In-process thread-safe cache for sub-millisecond repeated lookups.
2. Tier 2 (Disk Store): Persistent hash-keyed JSON storage in .ontoleap_cache/ that survives
   server restarts and prevents Cloudflare / WAF anti-bot rate limits.
3. Automatic TTL expiration (default: 24 hours).
4. Explicit bypass via `force_refresh=True` or custom max_age.
"""

import os
import json
import time
import hashlib
import logging
import threading
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger("gainark.cache")

# Default configuration
DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ontoleap_cache")
DEFAULT_TTL_SECONDS = 86400  # 24 hours
MAX_MEMORY_ENTRIES = 500


class DualTierCache:
    """Thread-safe in-memory and persistent disk cache."""

    def __init__(
        self,
        cache_dir: str = DEFAULT_CACHE_DIR,
        default_ttl: int = DEFAULT_TTL_SECONDS,
        max_memory_entries: int = MAX_MEMORY_ENTRIES
    ):
        self.cache_dir = cache_dir
        self.default_ttl = default_ttl
        self.max_memory_entries = max_memory_entries
        self._memory_cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

        # Ensure disk cache directory exists
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
        except Exception as e:
            logger.warning("Could not initialize disk cache directory at %s: %s", self.cache_dir, e)

    @staticmethod
    def _hash_key(key: str) -> str:
        """Computes a SHA-256 hash for a given URL or cache key."""
        return hashlib.sha256(key.strip().lower().encode("utf-8")).hexdigest()

    def _disk_path(self, key_hash: str) -> str:
        return os.path.join(self.cache_dir, f"{key_hash}.json")

    def get(self, key: str, max_age: Optional[int] = None, force_refresh: bool = False) -> Optional[str]:
        """
        Retrieves cached content if available and within TTL.
        Checks Memory (Tier 1) first, then Disk (Tier 2).
        Returns None if expired, missing, or if force_refresh=True.
        """
        if force_refresh:
            return None

        ttl = max_age if max_age is not None else self.default_ttl
        now = time.time()
        key_hash = self._hash_key(key)

        with self._lock:
            # 1. Tier 1: Memory Lookup
            if key_hash in self._memory_cache:
                entry = self._memory_cache[key_hash]
                if now - entry["timestamp"] <= ttl:
                    self._hits += 1
                    logger.debug("Memory cache HIT for %s", key)
                    return entry["content"]
                else:
                    # Expired in memory
                    del self._memory_cache[key_hash]

        # 2. Tier 2: Disk Lookup
        disk_file = self._disk_path(key_hash)
        if os.path.exists(disk_file):
            try:
                with open(disk_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                timestamp = data.get("timestamp", 0)
                if now - timestamp <= ttl:
                    content = data.get("content")
                    if content is not None:
                        # Populate back into Memory (Tier 1)
                        with self._lock:
                            if len(self._memory_cache) >= self.max_memory_entries:
                                # Evict oldest entry
                                oldest = min(self._memory_cache.keys(), key=lambda k: self._memory_cache[k]["timestamp"])
                                del self._memory_cache[oldest]
                            self._memory_cache[key_hash] = {
                                "content": content,
                                "timestamp": timestamp,
                                "key": key
                            }
                            self._hits += 1
                        logger.debug("Disk cache HIT for %s", key)
                        return content
                else:
                    # Expired on disk, remove file
                    try:
                        os.remove(disk_file)
                    except OSError:
                        pass
            except Exception as read_err:
                logger.debug("Error reading disk cache for %s: %s", key, read_err)

        with self._lock:
            self._misses += 1
        return None

    def set(self, key: str, content: str, content_type: str = "text/html") -> None:
        """
        Stores content in both Memory (Tier 1) and Disk (Tier 2).
        """
        now = time.time()
        key_hash = self._hash_key(key)

        # 1. Store in Memory
        with self._lock:
            if len(self._memory_cache) >= self.max_memory_entries:
                oldest = min(self._memory_cache.keys(), key=lambda k: self._memory_cache[k]["timestamp"])
                del self._memory_cache[oldest]
            self._memory_cache[key_hash] = {
                "content": content,
                "timestamp": now,
                "key": key
            }

        # 2. Store on Disk
        disk_file = self._disk_path(key_hash)
        try:
            payload = {
                "key": key,
                "timestamp": now,
                "content_type": content_type,
                "content": content
            }
            temp_file = f"{disk_file}.tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(temp_file, disk_file)
        except Exception as write_err:
            logger.debug("Error writing disk cache for %s: %s", key, write_err)

    def clear(self) -> int:
        """Clears both memory and disk caches. Returns count of deleted entries."""
        with self._lock:
            self._memory_cache.clear()

        deleted = 0
        if os.path.exists(self.cache_dir):
            for fname in os.listdir(self.cache_dir):
                if fname.endswith(".json"):
                    try:
                        os.remove(os.path.join(self.cache_dir, fname))
                        deleted += 1
                    except OSError:
                        pass
        return deleted

    def stats(self) -> Dict[str, Any]:
        """Returns cache telemetry statistics."""
        disk_items = 0
        disk_size_bytes = 0
        if os.path.exists(self.cache_dir):
            for fname in os.listdir(self.cache_dir):
                if fname.endswith(".json"):
                    disk_items += 1
                    try:
                        disk_size_bytes += os.path.getsize(os.path.join(self.cache_dir, fname))
                    except OSError:
                        pass

        with self._lock:
            total_reqs = self._hits + self._misses
            hit_ratio = round((self._hits / total_reqs) * 100, 1) if total_reqs > 0 else 0.0
            return {
                "memory_entries": len(self._memory_cache),
                "disk_entries": disk_items,
                "disk_size_kb": round(disk_size_bytes / 1024, 1),
                "hits": self._hits,
                "misses": self._misses,
                "hit_ratio_pct": hit_ratio,
                "cache_dir": self.cache_dir,
                "default_ttl_seconds": self.default_ttl
            }


# Global singleton instance
_global_cache: Optional[DualTierCache] = None

def get_content_cache() -> DualTierCache:
    """Returns the global singleton cache."""
    global _global_cache
    if _global_cache is None:
        _global_cache = DualTierCache()
    return _global_cache
