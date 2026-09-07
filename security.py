"""
GainARK OntoLeap — Shared Security Controls

Single source of truth for the cross-cutting controls the API and the crawler rely on:

1. Identifier validation (vertical IDs are used to build filesystem paths).
2. Safe HTTP header values (filenames echoed into Content-Disposition).
3. CSV/spreadsheet formula-injection neutralisation for exported cells.
4. API key comparison in constant time.
5. Bounded LRU registry used to cap the pipeline cache.

Nothing here performs I/O. Network-facing SSRF checks live in scraper.py, which is
the only module that opens sockets.
"""

import os
import re
import hmac
import logging
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger("gainark.security")

# ---------------------------------------------------------------------------
# Identifier validation
# ---------------------------------------------------------------------------
# vertical_id is interpolated into "verticals/<id>.json". Without this a request
# body could walk out of that directory and have an arbitrary JSON file on disk
# loaded as a pipeline config. Mirrors industry_profiler._sanitize_slug, which is
# what actually produces these IDs, so no legitimate ID is rejected.
_VERTICAL_ID_RE = re.compile(r"^[a-zA-Z0-9_]{1,64}$")


def is_valid_vertical_id(vertical_id: str) -> bool:
    """True if the ID is a bare slug and therefore safe to use in a file path."""
    return bool(vertical_id) and bool(_VERTICAL_ID_RE.match(vertical_id))


# ---------------------------------------------------------------------------
# HTTP header / filename safety
# ---------------------------------------------------------------------------
_UNSAFE_FILENAME_CHARS = re.compile(r'[^A-Za-z0-9._-]+')


def safe_filename(raw: str, fallback: str = "report", max_length: int = 100) -> str:
    """
    Reduce arbitrary text to a token safe to embed in a Content-Disposition header.

    Strips quotes, CR/LF and every other character that could terminate the header
    value or inject an additional header, then collapses the remainder to a single
    underscore-separated token.
    """
    collapsed = _UNSAFE_FILENAME_CHARS.sub("_", str(raw or "")).strip("._-")
    collapsed = collapsed[:max_length]
    return collapsed or fallback


def content_disposition(filename: str, inline: bool = False) -> str:
    """Build a Content-Disposition value that cannot be broken out of."""
    disposition = "inline" if inline else "attachment"
    return f'{disposition}; filename="{safe_filename(filename)}"'


# ---------------------------------------------------------------------------
# CSV / spreadsheet formula injection
# ---------------------------------------------------------------------------
# A cell beginning with =, +, -, @ or a control character is executed as a formula
# by Excel, LibreOffice and Sheets. Values here originate from crawled third-party
# pages, so they are untrusted.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> str:
    """Neutralise a value so a spreadsheet renders it as text, never as a formula."""
    text = "" if value is None else str(value)
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


# ---------------------------------------------------------------------------
# API key authentication
# ---------------------------------------------------------------------------
API_KEY_HEADER = "x-api-key"


def configured_api_key() -> str:
    """The API key the deployment expects, or '' when auth is disabled."""
    return os.environ.get("ONTOLEAP_API_KEY", "").strip()


def api_key_matches(presented: Optional[str]) -> bool:
    """Constant-time comparison against the configured key."""
    expected = configured_api_key()
    if not expected:
        return False
    return hmac.compare_digest(str(presented or ""), expected)


# ---------------------------------------------------------------------------
# Bounded caches
# ---------------------------------------------------------------------------
class BoundedRegistry:
    """
    Thread-safe LRU map with a hard entry cap.

    Used for the per-vertical pipeline registry: that map is keyed by request input,
    so without a cap a caller can grow it without limit.
    """

    def __init__(self, max_entries: int = 16):
        self.max_entries = max(1, int(max_entries))
        self._data: "OrderedDict[str, Any]" = OrderedDict()
        self._lock = threading.Lock()

    def get_or_create(self, key: str, factory: Callable[[], Any]) -> Any:
        """Return the cached value for `key`, building it once if absent."""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]

        # Built outside the lock: construction is slow and must not block readers.
        value = factory()

        with self._lock:
            if key in self._data:            # another thread won the race
                self._data.move_to_end(key)
                return self._data[key]
            self._data[key] = value
            while len(self._data) > self.max_entries:
                evicted, _ = self._data.popitem(last=False)
                logger.info("Evicting cached entry '%s' (registry cap %d reached)", evicted, self.max_entries)
            return value

    # Mapping-style helpers kept so existing call sites reading the registry
    # (health checks, tests) continue to work unchanged.
    def keys(self):
        with self._lock:
            return list(self._data.keys())

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
