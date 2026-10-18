"""Turning raw genre/style/tag data into weighted song_tags."""
from .match import normalize

ESSENTIA_TOP = 5
ESSENTIA_MIN = 0.1
PARENT_GENRE_FACTOR = 0.5


def style_tags(styles):
    """Discogs-400 labels ("Rock---Post-Punk") to {"post-punk": p, "rock": p/2}.
    The top styles only; the parent genre rides along at reduced weight."""
    top = sorted(styles.items(), key=lambda kv: -kv[1])[:ESSENTIA_TOP]
    tags = {}
    for label, prob in top:
        if prob < ESSENTIA_MIN:
            continue
        parent, _, style = label.partition("---")
        merge(tags, clean_tag(style or parent), prob)
        merge(tags, clean_tag(parent), prob * PARENT_GENRE_FACTOR)
    return tags


def counted_tags(entries, scale=1.0):
    """MusicBrainz genres/tags ([{"name", "count"}]) to weights relative to the top vote."""
    entries = [e for e in entries or [] if e.get("count", 0) > 0]
    if not entries:
        return {}
    top = max(e["count"] for e in entries)
    return {clean_tag(e["name"]): scale * e["count"] / top for e in entries}


def merge(tags, tag, weight):
    if tag:
        tags[tag] = max(tags.get(tag, 0.0), round(weight, 4))
    return tags


def clean_tag(name):
    return normalize(name, strip_parentheticals=False)
