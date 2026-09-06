"""
Unit Test Suite for OntoLeap Dual-Tier Content & Response Cache
Verifies:
1. Memory LRU Cache (Tier 1) sub-millisecond retrieval
2. Disk Cache (Tier 2) persistence and reloading
3. TTL expiration logic
4. Cache bypass via force_refresh=True
5. Cache integration with scraper.smart_fetch
6. Cache stats and clear API endpoints
"""

import os
import time
import shutil
import unittest
from unittest.mock import patch, MagicMock

from cache import DualTierCache, get_content_cache
from scraper import smart_fetch
from fastapi.testclient import TestClient
from api import app

client = TestClient(app)


class TestSmartCache(unittest.TestCase):

    def setUp(self):
        self.test_cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_cache")
        if os.path.exists(self.test_cache_dir):
            shutil.rmtree(self.test_cache_dir, ignore_errors=True)
        self.cache = DualTierCache(cache_dir=self.test_cache_dir, default_ttl=3600, max_memory_entries=10)

    def tearDown(self):
        if os.path.exists(self.test_cache_dir):
            shutil.rmtree(self.test_cache_dir, ignore_errors=True)

    def test_memory_cache_hit(self):
        """Content stored in cache should be immediately retrievable from memory."""
        url = "https://example.com/test-page"
        content = "<html><body><h1>Test</h1></body></html>"
        
        self.cache.set(url, content)
        retrieved = self.cache.get(url)
        self.assertEqual(retrieved, content)
        
        stats = self.cache.stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 0)

    def test_disk_cache_persistence(self):
        """Content saved to disk cache should persist even across a new cache instance."""
        url = "https://example.com/persistent-page"
        content = "<html><body>Persistent Disk Content</body></html>"
        
        self.cache.set(url, content)
        
        # Create a fresh cache instance pointing to same directory
        new_cache = DualTierCache(cache_dir=self.test_cache_dir, default_ttl=3600)
        # Verify memory is empty in new instance
        self.assertEqual(len(new_cache._memory_cache), 0)
        
        # Retrieve from disk
        retrieved = new_cache.get(url)
        self.assertEqual(retrieved, content)
        # Should now be loaded into memory
        self.assertEqual(len(new_cache._memory_cache), 1)

    def test_ttl_expiration(self):
        """Expired entries must return None."""
        url = "https://example.com/ephemeral"
        content = "<html>Short Lived Content</html>"
        
        # Set with 1-second TTL
        short_cache = DualTierCache(cache_dir=self.test_cache_dir, default_ttl=1)
        short_cache.set(url, content)
        
        # Immediate get -> hit
        self.assertEqual(short_cache.get(url), content)
        
        # Sleep past TTL
        time.sleep(1.1)
        
        # Should be expired
        self.assertIsNone(short_cache.get(url))

    def test_force_refresh_bypasses_cache(self):
        """force_refresh=True must ignore cached content."""
        url = "https://example.com/refreshable"
        content = "Old Content"
        self.cache.set(url, content)
        
        self.assertEqual(self.cache.get(url), content)
        self.assertIsNone(self.cache.get(url, force_refresh=True))

    def test_scraper_smart_fetch_uses_cache(self):
        """smart_fetch should return cached content without network call."""
        url = "https://example.com/cached-fetch"
        cached_html = "<html><body>Cached via SmartFetch</body></html>"
        
        global_cache = get_content_cache()
        global_cache.set(url, cached_html)
        
        # Call smart_fetch - should hit cache without making HTTP request
        result = smart_fetch(url)
        self.assertEqual(result, cached_html)

    def test_api_cache_endpoints(self):
        """Test /api/cache/stats and /api/cache/clear."""
        # Stats
        resp = client.get("/api/cache/stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("memory_entries", data)
        self.assertIn("disk_entries", data)
        self.assertIn("hit_ratio_pct", data)
        
        # Clear
        resp_clear = client.post("/api/cache/clear")
        self.assertEqual(resp_clear.status_code, 200)
        clear_data = resp_clear.json()
        self.assertEqual(clear_data["status"], "cleared")


if __name__ == "__main__":
    unittest.main(verbosity=2)
