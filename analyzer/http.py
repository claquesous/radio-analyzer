"""Rate-limited, disk-cached JSON GETs for the metadata APIs."""
import hashlib
import json
import logging
import os
import threading
import time

import requests

from . import config

log = logging.getLogger(__name__)

# Minimum seconds between requests, per API. MusicBrainz enforces 1/s per IP.
INTERVALS = {"musicbrainz": 1.1, "acoustid": 0.34, "discogs": 1.0}


class RateLimiter:
    def __init__(self):
        self._last = {}
        self._lock = threading.Lock()

    def wait(self, api):
        with self._lock:
            now = time.monotonic()
            ready = self._last.get(api, 0) + INTERVALS[api]
            if ready > now:
                time.sleep(ready - now)
            self._last[api] = time.monotonic()


_limiter = RateLimiter()
_session = requests.Session()
_session.headers["User-Agent"] = config.USER_AGENT


class NotFound(Exception):
    pass


def _cache_path(api, url, params):
    key = hashlib.sha256(json.dumps([url, sorted((params or {}).items())]).encode()).hexdigest()
    return os.path.join(config.CACHE_DIR, api, key[:2], key + ".json")


def get_json(api, url, params=None, headers=None, cache=True, retries=4):
    """GET a JSON document. 404s are cached as None so misses aren't retried."""
    path = _cache_path(api, url, params)
    if cache and os.path.exists(path):
        with open(path) as f:
            return json.load(f)

    for attempt in range(retries):
        _limiter.wait(api)
        resp = _session.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code in (429, 503) or resp.status_code >= 500:
            # MusicBrainz often sends "Retry-After: 0" with its 503s; back off anyway.
            delay = max(float(resp.headers.get("Retry-After") or 0), 2 ** (attempt + 1))
            log.warning("%s %s -> %s, retrying in %ss", api, url, resp.status_code, delay)
            time.sleep(delay)
            continue
        if resp.status_code == 404:
            data = None
        else:
            resp.raise_for_status()
            data = resp.json()
        if cache:
            # Write-then-rename: concurrent requests may fetch the same document.
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{threading.get_ident()}.tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        return data
    raise RuntimeError(f"{api} request failed after {retries} attempts: {url}")
