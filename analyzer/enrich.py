"""enrich: credits, tags and original release year from MusicBrainz and Discogs."""
import logging

from . import config
from .credits import CreditSet, discogs_person, discogs_role_credits, mb_relation_credit
from .http import get_json
from .match import discogs_name, normalize, similarity
from .tags import counted_tags, merge

log = logging.getLogger(__name__)

MB = "https://musicbrainz.org/ws/2"
DISCOGS = "https://api.discogs.com"
RECORDING_INC = "artist-credits+releases+release-groups+artist-rels+work-rels+work-level-rels+genres+tags"
ALBUM_MATCH = 0.8
TRACK_MATCH = 0.85
# MB tags are folksonomy; when an entity has no curated genres, its tags count for less.
TAGS_WITHOUT_GENRES = 0.5
TAG_LEVELS = {"recording": 1.0, "release-group": 0.8, "artist": 0.5}


def enrich_song(song):
    """Credits, tags, release year and external ids for one song.

    Only the sources in "sources" were reached: the caller replaces those sources'
    credits and tags, and leaves any other source's earlier data alone. "id3" tags
    are always returned. The ids are suggestions to fill in where still blank."""
    try:
        return _enrich(song)
    except Exception:
        log.exception("enrich failed for %r", song["title"])
        return {"status": "error"}


def _enrich(song):
    sources, credits, tags, ids = [], [], {}, {}
    year = None

    if song["mb_recording_id"]:
        mb = musicbrainz_metadata(song)
        if mb:
            sources.append("musicbrainz")
            credits += credit_rows(mb["credits"], "musicbrainz")
            tags["musicbrainz"] = mb["tags"]
            ids.update(mb["ids"])
            year = mb["year"]

    if config.DISCOGS_TOKEN and song["album_title"]:
        dg = discogs_metadata(song)
        if dg:
            sources.append("discogs")
            credits += credit_rows(dg["credits"], "discogs")
            tags["discogs"] = dg["tags"]
            ids["discogs_master_id"] = dg["master_id"]
            year = year or dg["year"]

    tags["id3"] = {}
    if song["id3_genre"]:
        merge(tags["id3"], song["id3_genre"].strip().lower(), 1.0)

    return {
        "status": "+".join(sources) or "id3_only",
        "sources": sources,
        "release_year": year or song["year"],
        "credits": credits,
        "tags": tags,
        **ids,
    }


def credit_rows(credits, source):
    return [{"name": person["name"], "mb_artist_id": person.get("mb_artist_id"),
             "discogs_id": person.get("discogs_id"), "role": role, "detail": detail, "source": source}
            for person, role, detail in credits]


# ── MusicBrainz ──────────────────────────────────────────────────────────────

def mb_get(entity, mbid, inc):
    return get_json("musicbrainz", f"{MB}/{entity}/{mbid}", {"inc": inc, "fmt": "json"})


def musicbrainz_metadata(song):
    recording = mb_get("recording", song["mb_recording_id"], RECORDING_INC)
    if recording is None:
        log.warning("recording %s not found (merged or deleted?)", song["mb_recording_id"])
        return None

    credits = CreditSet()
    for credit in recording.get("artist-credit", []):
        artist = credit["artist"]
        credits.add(mb_person(artist), "performer", "primary")
    work_ids = []
    for rel in recording.get("relations", []):
        if rel.get("target-type") == "work" and rel.get("type") == "performance":
            work = rel["work"]
            work_ids.append(work["id"])
            # work-level-rels usually inlines the work's relations; fetch if not.
            work_rels = work.get("relations") or (mb_get("work", work["id"], "artist-rels") or {}).get("relations", [])
            add_mb_relations(credits, work_rels)
        else:
            add_mb_relations(credits, [rel])

    release_group = pick_release_group(song, recording.get("releases", []))

    tags = {}
    merge_mb_tags(tags, recording, TAG_LEVELS["recording"])
    if release_group:
        rg = mb_get("release-group", release_group["id"], "genres+tags")
        merge_mb_tags(tags, rg or {}, TAG_LEVELS["release-group"])
    for credit in recording.get("artist-credit", [])[:1]:
        artist = mb_get("artist", credit["artist"]["id"], "genres+tags")
        merge_mb_tags(tags, artist or {}, TAG_LEVELS["artist"])

    # The earliest of the two: a remastered or remixed recording's own first
    # release is the reissue, but its album still dates from the original.
    years = [parse_year(recording.get("first-release-date")),
             parse_year((release_group or {}).get("first-release-date"))]
    year = min((y for y in years if y), default=None)
    return {"credits": credits, "tags": tags, "year": year,
            "ids": linked_ids(song, recording, release_group, work_ids)}


def add_mb_relations(credits, relations):
    for rel in relations:
        mapped = mb_relation_credit(rel)
        if mapped:
            credits.add(mb_person(rel["artist"]), *mapped)


def mb_person(artist):
    return {"name": artist["name"], "mb_artist_id": artist["id"]}


def merge_mb_tags(tags, entity, level_weight):
    if entity.get("genres"):
        entries, scale = entity["genres"], level_weight
    else:
        entries, scale = entity.get("tags"), level_weight * TAGS_WITHOUT_GENRES
    for tag, weight in counted_tags(entries, scale).items():
        merge(tags, tag, weight)


def pick_release_group(song, releases):
    """The release group matching our album title, else the earliest plain studio album."""
    groups = {r["release-group"]["id"]: r["release-group"] for r in releases if "release-group" in r}
    if not groups:
        return None
    if song["album_title"]:
        best = max(groups.values(), key=lambda g: similarity(song["album_title"], g.get("title")))
        if similarity(song["album_title"], best.get("title")) >= ALBUM_MATCH:
            return best

    def preference(group):
        studio = group.get("primary-type") == "Album" and not group.get("secondary-types")
        return (not studio, group.get("first-release-date") or "9999")

    return min(groups.values(), key=preference)


def linked_ids(song, recording, release_group, work_ids):
    """The recording's work ids, plus artist/album MBIDs when the names agree."""
    ids = {"mb_work_ids": work_ids}
    credit = (recording.get("artist-credit") or [None])[0]
    if credit and similarity(song["artist_name"], credit["artist"]["name"]) >= ALBUM_MATCH:
        ids["mb_artist_id"] = credit["artist"]["id"]
    if release_group and song["album_title"] and similarity(song["album_title"], release_group.get("title")) >= ALBUM_MATCH:
        ids["mb_release_group_id"] = release_group["id"]
    return ids


def parse_year(date):
    try:
        return int((date or "")[:4]) or None
    except ValueError:
        return None


# ── Discogs ──────────────────────────────────────────────────────────────────

def discogs_get(path, params=None):
    return get_json(
        "discogs", f"{DISCOGS}{path}", params,
        headers={"Authorization": f"Discogs token={config.DISCOGS_TOKEN}"},
    )


def discogs_metadata(song):
    master_id = song["discogs_master_id"] or find_master(song)
    if not master_id:
        return None
    master = discogs_get(f"/masters/{master_id}")
    if not master:
        return None
    release = discogs_get(f"/releases/{master['main_release']}") or {}

    credits = CreditSet()
    tracklist = [t for t in release.get("tracklist", []) if t.get("type_", "track") == "track"]
    track = match_track(song, tracklist)
    positions = [t.get("position") for t in tracklist]
    for artist in release.get("extraartists", []):
        if applies_to_track(artist.get("tracks", ""), track and track.get("position"), positions):
            for role, detail in discogs_role_credits(artist.get("role", "")):
                credits.add(discogs_person(artist), role, detail)
    for artist in (track or {}).get("extraartists", []):
        for role, detail in discogs_role_credits(artist.get("role", "")):
            credits.add(discogs_person(artist), role, detail)

    tags = {}
    for style in release.get("styles") or master.get("styles") or []:
        merge(tags, style.lower(), 1.0)
    for genre in release.get("genres") or master.get("genres") or []:
        merge(tags, genre.lower(), 0.6)
    return {"credits": credits, "tags": tags, "year": master.get("year") or None, "master_id": master_id}


def find_master(song):
    """Search as tagged, then with punctuation/parentheticals stripped
    ("Weezer (Blue Album)" -> "weezer", "A.F.I." -> "a f i" -> "afi")."""
    queries = [(song["artist_name"], song["album_title"]),
               (normalize(song["artist_name"]).replace(" ", "") if "." in song["artist_name"] else normalize(song["artist_name"]),
                normalize(song["album_title"]))]
    for artist, title in dict.fromkeys(queries):
        master_id = _search_master(song, artist, title)
        if master_id:
            return master_id
    return None


def _search_master(song, artist_query, title_query):
    data = discogs_get(
        "/database/search",
        {"type": "master", "artist": artist_query, "release_title": title_query, "per_page": 5},
    ) or {}
    for result in data.get("results", []):
        artist, _, title = result.get("title", "").partition(" - ")
        artist_score = max(similarity(song["artist_name"], discogs_name(artist)),
                           similarity(artist_query, discogs_name(artist)))
        if similarity(song["album_title"], title) >= ALBUM_MATCH and artist_score >= ALBUM_MATCH:
            return result["id"]
    return None


def match_track(song, tracklist):
    best = max(tracklist, key=lambda t: similarity(song["title"], t.get("title")), default=None)
    if best and similarity(song["title"], best.get("title")) >= TRACK_MATCH:
        return best
    return None


def applies_to_track(tracks, position, positions):
    """Discogs release credits name their tracks like "A1 to A3, B2"; empty means all."""
    tracks = (tracks or "").strip()
    if not tracks:
        return True
    if not position:
        return False
    for part in tracks.split(","):
        part = part.strip()
        if " to " in part:
            start, end = (p.strip() for p in part.split(" to ", 1))
            if start in positions and end in positions:
                if position in positions[positions.index(start): positions.index(end) + 1]:
                    return True
        elif part == position:
            return True
    return False
