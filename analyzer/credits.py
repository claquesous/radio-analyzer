"""Mapping MusicBrainz relationships and Discogs credit strings onto credit roles:
performer, vocal, instrument, producer, engineer, mixer, composer, lyricist,
writer, arranger."""
import re

from .match import discogs_name

# MusicBrainz artist-relationship types, on recordings and on works.
MB_ROLES = {
    "performer": "performer", "performing orchestra": "performer", "conductor": "performer",
    "vocal": "vocal",
    "instrument": "instrument", "programming": "instrument",
    "producer": "producer", "remixer": "producer",
    "engineer": "engineer", "audio": "engineer", "recording": "engineer", "sound": "engineer",
    "mix": "mixer",
    "arranger": "arranger", "instrument arranger": "arranger", "vocal arranger": "arranger",
    "orchestrator": "arranger",
    "composer": "composer", "lyricist": "lyricist", "librettist": "lyricist", "writer": "writer",
}

_DISCOGS_SKIP = re.compile(
    r"master|lacquer|photo|design|\bart\b|artwork|layout|liner|notes|management|a&r|executive|coordinat|"
    r"legal|copyright|translat|illustrat|cover|sleeve|typograph|compiled|edited|\bother\b|direct|"
    r"film|video|booking|publicity|thanks|\blabel\b|licens|publish|pressed|manufactur|distribut|"
    r"marketing|promot|graphics|technician|tour|crew|transfer|restor|research|supervis|"
    r"concept|cgi|render|relations|\bguest\b|photograph|stylist|hair|make-?up|contractor|"
    r"\bpr\b|legal|business|creative|programming \[video\]|animation|logo",
    re.I,
)
_DISCOGS_RULES = [
    (re.compile(r"producer|produced", re.I), "producer"),
    (re.compile(r"written|songwriter|words and music", re.I), "writer"),
    (re.compile(r"lyrics|words by", re.I), "lyricist"),
    (re.compile(r"music by|composed", re.I), "composer"),
    (re.compile(r"mixed|^mix", re.I), "mixer"),
    (re.compile(r"engineer|recorded", re.I), "engineer"),
    (re.compile(r"arrang|orchestrat", re.I), "arranger"),
    (re.compile(r"vocal|voice|choir|chorus|\brap\b|\bmc\b|lead singer", re.I), "vocal"),
    (re.compile(r"^performer|band|ensemble|orchestra|featuring|^musician", re.I), "performer"),
]


def mb_relation_credit(relation):
    """(role, detail) for an artist relationship, or None if it isn't a credit we track."""
    if relation.get("target-type") != "artist" or "artist" not in relation:
        return None
    role = MB_ROLES.get(relation.get("type"))
    if not role:
        return None
    detail = ", ".join(relation.get("attributes") or []) or None
    return role, detail


def discogs_role_credits(role_string):
    """Discogs roles look like "Guitar, Vocals [Backing]" or "Engineer [Recording]".
    Returns [(role, detail)]; unknown musical roles are taken to be instruments."""
    credits = []
    for detail in _split_roles(role_string):
        bare = re.sub(r"\s*\[[^\]]*\]", "", detail).strip()
        if not bare or _DISCOGS_SKIP.search(bare) or _ASSISTANT.search(detail):
            continue
        role = next((r for pattern, r in _DISCOGS_RULES if pattern.search(bare)), "instrument")
        credits.append((role, detail))
    return credits


# Assistant engineers are numerous and say little about how a record sounds.
_ASSISTANT = re.compile(r"\[[^\]]*assistant[^\]]*\]|^assistant", re.I)


def _split_roles(role_string):
    """Split on commas outside brackets: "Recorded By [Studio A, Studio B], Bass"."""
    parts, depth, current = [], 0, ""
    for char in role_string or "":
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(depth - 1, 0)
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    parts.append(current.strip())
    return [p for p in parts if p]


class CreditSet:
    """Collects credits for one song from one source, merging repeated
    (person, role) pairs such as one musician credited on two instruments."""

    def __init__(self):
        self._credits = {}

    def add(self, person, role, detail=None):
        """person is a dict: {"name", "mb_artist_id"} or {"name", "discogs_id"}."""
        key = (person.get("mb_artist_id") or person.get("discogs_id") or person["name"].lower(), role)
        entry = self._credits.setdefault(key, {"person": person, "role": role, "details": []})
        if detail and detail not in entry["details"]:
            entry["details"].append(detail)

    def __iter__(self):
        for entry in self._credits.values():
            yield entry["person"], entry["role"], ", ".join(entry["details"]) or None

    def __len__(self):
        return len(self._credits)

    def roles(self):
        return {role for _, role in self._credits}


def discogs_person(artist):
    return {"name": discogs_name(artist.get("name")), "discogs_id": artist.get("id") or None}
