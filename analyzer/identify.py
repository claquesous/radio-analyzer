"""identify: fingerprint each MP3 and resolve it to a MusicBrainz recording."""
import logging
import os

import acoustid

from . import config
from .http import _limiter, get_json
from .match import artist_similarity, contains, lucene_quote, similarity

log = logging.getLogger(__name__)

ACOUSTID_MIN_SCORE = 0.5
# Relaxed second pass, for near-certain fingerprints whose MB names differ from our
# tags ("World" / "My World", "Matchbox 20" / "Matchbox Twenty").
RELAXED_MIN_SCORE = 0.95
RELAXED_DURATION_S = 3
RELAXED_TITLE_MIN = 0.6
RELAXED_ARTIST_MIN = 0.5
TITLE_MIN = 0.8
ARTIST_MIN = 0.7
SEARCH_MIN_SCORE = 90
DURATION_TOLERANCE_S = 7
# Text search happily returns live takes, demos and video edits of a song. Skip
# them unless our own title says that's what this file is.
VARIANT_WORDS = ("live", "demo", "video", "remix", "acoustic", "instrumental", "karaoke", "edit",
                 "remaster", "atmos", "mix", "re-record")
# Reissues share the original's fingerprint, so AcoustID offers both; prefer the original.
VARIANT_PENALTY = 1.0
# AcoustID omits MB disambiguation ("Dolby Atmos mix"), so also favor the recording
# on the most release groups: originals appear on singles and compilations, reissue
# mixes on one release. Small enough to only break near-ties.
POPULARITY_BONUS = 0.2
POPULARITY_CAP = 10


class AcoustIDError(Exception):
    pass


def identify_song(song):
    """{status, mb_recording_id, acoustid} for one song. On "error" and "missing_file"
    the ids are None and the caller should keep whatever match it already has."""
    if not os.path.exists(song["path"]):
        return {"status": "missing_file", "mb_recording_id": None, "acoustid": None}
    try:
        recording_id, acoustid_id, status = resolve(song)
    except AcoustIDError:
        raise
    except Exception:
        log.exception("identify failed for %s", song["path"])
        return {"status": "error", "mb_recording_id": None, "acoustid": None}
    log.debug("%r: %s", song["title"], status)
    return {"status": status, "mb_recording_id": recording_id, "acoustid": acoustid_id}


def resolve(song):
    acoustid_id = None
    if config.ACOUSTID_API_KEY:
        acoustid_id, recording_id = lookup_fingerprint(song)
        if recording_id:
            return recording_id, acoustid_id, "acoustid"
    recording_id = search_recording(song)
    if recording_id:
        return recording_id, acoustid_id, "search"
    return None, acoustid_id, "unmatched"


def lookup_fingerprint(song):
    duration, fingerprint = acoustid.fingerprint_file(song["path"], force_fpcalc=True)
    _limiter.wait("acoustid")
    response = acoustid.lookup(
        config.ACOUSTID_API_KEY, fingerprint, duration, meta="recordings releasegroups"
    )
    if response.get("status") != "ok":
        # e.g. "invalid API key": a config problem, not a miss, so don't fall back silently.
        raise AcoustIDError(response.get("error", {}).get("message", response))
    results = [r for r in response.get("results", []) if r.get("score", 0) >= ACOUSTID_MIN_SCORE]
    if not results:
        return None, None
    best_acoustid = results[0]["id"]
    candidates = [(r["score"], rec) for r in results for rec in r.get("recordings", [])]
    recording = best_recording(song, candidates) or relaxed_recording(song, candidates, duration)
    return best_acoustid, recording


def best_recording(song, candidates):
    """Pick the AcoustID recording whose names agree with our tags. A fingerprint
    shared by many recordings (compilations, remasters) is disambiguated by album."""
    best, best_score = None, 0.0
    for fingerprint_score, rec in candidates:
        if "title" not in rec:
            continue
        title = similarity(song["title"], rec["title"])
        artist = artist_similarity(song["artist_name"], [a["name"] for a in rec.get("artists", [])])
        if title < TITLE_MIN or artist < ARTIST_MIN:
            continue
        album = max(
            (similarity(song["album_title"], rg.get("title")) for rg in rec.get("releasegroups", [])),
            default=0.0,
        )
        score = fingerprint_score + title + artist + 0.5 * album
        if is_unwanted_variant(song["title"], rec):
            score -= VARIANT_PENALTY
        score += POPULARITY_BONUS * min(len(rec.get("releasegroups", [])), POPULARITY_CAP) / POPULARITY_CAP
        if score > best_score:
            best, best_score = rec["id"], score
    return best


def relaxed_recording(song, candidates, duration):
    """Only when the strict pass found nothing: accept a strong fingerprint whose
    duration matches the file, if title and artist still loosely agree. Covers by
    other artists are still rejected by the artist check."""
    best, best_gap = None, None
    for fingerprint_score, rec in candidates:
        if fingerprint_score < RELAXED_MIN_SCORE or "title" not in rec or not rec.get("duration"):
            continue
        gap = abs(rec["duration"] - duration)
        if gap > RELAXED_DURATION_S:
            continue
        title_ok = contains(song["title"], rec["title"]) or similarity(song["title"], rec["title"]) >= RELAXED_TITLE_MIN
        names = [a["name"] for a in rec.get("artists", [])]
        artist_ok = artist_similarity(song["artist_name"], names) >= RELAXED_ARTIST_MIN or any(
            contains(song["artist_name"], n) for n in names)
        if title_ok and artist_ok and (best_gap is None or gap < best_gap):
            best, best_gap = rec["id"], gap
    return best


def search_recording(song):
    query = f"recording:{lucene_quote(song['title'])} AND artist:{lucene_quote(song['artist_name'])}"
    if song["album_title"]:
        found = _search(song, query + f" AND release:{lucene_quote(song['album_title'])}")
        if found:
            return found
    return _search(song, query)


def _search(song, query):
    data = get_json(
        "musicbrainz",
        "https://musicbrainz.org/ws/2/recording",
        {"query": query, "fmt": "json", "limit": 5},
    )
    for rec in (data or {}).get("recordings", []):
        if int(rec.get("score", 0)) < SEARCH_MIN_SCORE:
            break
        artist = artist_similarity(song["artist_name"], [c["name"] for c in rec.get("artist-credit", [])])
        if similarity(song["title"], rec["title"]) < 0.9 or artist < 0.8:
            continue
        if song["time"] and (not rec.get("length") or abs(rec["length"] / 1000 - song["time"]) > DURATION_TOLERANCE_S):
            continue
        if is_unwanted_variant(song["title"], rec):
            continue
        return rec["id"]
    return None


def is_unwanted_variant(our_title, rec):
    label = f"{rec.get('disambiguation', '')} {rec.get('title', '')}".lower()
    ours = (our_title or "").lower()
    return any(word in label and word not in ours for word in VARIANT_WORDS)
