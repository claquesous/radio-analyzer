import os

MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
CACHE_DIR = os.environ.get("CACHE_DIR", "/cache")
MUSIC_ROOT = os.path.realpath(os.environ.get("MUSIC_ROOT", "/music"))

ACOUSTID_API_KEY = os.environ.get("ACOUSTID_API_KEY") or None
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN") or None
# MusicBrainz requires a meaningful User-Agent with contact info (URL or email).
MB_CONTACT = os.environ.get("MB_CONTACT") or None

USER_AGENT = f"ClaqRadio-Analyzer/0.1 ( {MB_CONTACT or 'no contact configured'} )"

# When set, every endpoint but /health requires "Authorization: Bearer <token>".
API_TOKEN = os.environ.get("ANALYZER_TOKEN") or None

PORT = int(os.environ.get("PORT", "8000"))
# Essentia processes. 0 analyzes in the server process instead: Lambda has no
# /dev/shm, which a process pool needs, and serves one request at a time anyway.
ANALYZE_WORKERS = int(os.environ.get("ANALYZE_WORKERS", "2"))
# Seconds to connect to, and between bytes from, a song URL.
DOWNLOAD_TIMEOUT = (10, 60)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
