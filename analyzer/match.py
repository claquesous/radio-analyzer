"""Fuzzy name matching between local tags and MusicBrainz/Discogs results."""
import re
import unicodedata
from difflib import SequenceMatcher

_PARENTHETICAL = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_DISCOGS_SUFFIX = re.compile(r"\s*\(\d+\)$")  # "John Smith (3)" disambiguation
_NON_WORD = re.compile(r"[^\w\s]")


def normalize(name, strip_parentheticals=True):
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = name.lower().replace("&", " and ")
    if strip_parentheticals:
        name = _PARENTHETICAL.sub("", name)
    name = _NON_WORD.sub(" ", name)
    name = re.sub(r"^the\s+", "", name.strip())
    return " ".join(name.split())


def similarity(a, b):
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


_FEATURING = re.compile(r"\s+(?:f\./\s*|(?:f\.|feat\.?|ft\.?|featuring|with)\s+)", re.I)


def artist_similarity(ours, credited):
    """Our artist tag against a credit that may list several artists ("Gotye",
    "Kimbra"). Our tag may itself name a featured artist ("Bad Omens f./Poppy"),
    so the lead artist alone is tried too."""
    names = [n for n in credited if n]
    if not names:
        return 0.0
    ours_options = {ours, _FEATURING.split(ours or "")[0]}
    return max(similarity(o, n) for o in ours_options for n in names + [" ".join(names)])


def contains(a, b):
    """Either normalized name contains the other as whole words ("World" / "My World")."""
    a, b = f" {normalize(a)} ", f" {normalize(b)} "
    return len(a.strip()) > 2 and len(b.strip()) > 2 and (a in b or b in a)


def discogs_name(name):
    return _DISCOGS_SUFFIX.sub("", name or "").strip()


def lucene_quote(text):
    return '"' + re.sub(r'([\\"])', r"\\\1", text or "") + '"'
