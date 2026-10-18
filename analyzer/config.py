import os

MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
CACHE_DIR = os.environ.get("CACHE_DIR", "/cache")
MUSIC_ROOT = os.path.realpath(os.environ.get("MUSIC_ROOT", "/music"))

ACOUSTID_API_KEY = os.environ.get("ACOUSTID_API_KEY") or None
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN") or None
# MusicBrainz requires a meaningful User-Agent with contact info (URL or email).
MB_CONTACT = os.environ.get("MB_CONTACT") or None

USER_AGENT = f"ClaqRadio-Analyzer/0.1 ( {MB_CONTACT or 'no contact configured'} )"

PORT = int(os.environ.get("PORT", "8000"))
ANALYZE_WORKERS = int(os.environ.get("ANALYZE_WORKERS", "2"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
