"""embed: a song's genre embedding, from its tags embedded as text.

Creator embeddings and facet calibration need the whole library, so they are
left to the caller, which owns that data."""
import threading

import numpy as np

GENRE_MODEL = "BAAI/bge-small-en-v1.5"
GENRE_MODEL_VERSION = "bge-small-en-v1.5-v1"

TAG_SOURCE_WEIGHTS = {"manual": 1.5, "musicbrainz": 1.0, "discogs": 1.0, "essentia": 0.7, "id3": 0.4}


def genre_embedding(tags, encode=None):
    """tags is [{tag, weight, source}]. Each tag is embedded as text and averaged
    by weight, so related genres ("post punk", "new wave") land near each other
    without a genre taxonomy. None when there is nothing to embed."""
    weighted = {}
    for row in tags:
        weight = row["weight"] * TAG_SOURCE_WEIGHTS.get(row["source"], 1.0)
        weighted[row["tag"]] = weighted.get(row["tag"], 0.0) + weight
    if not weighted:
        return None

    vectors = dict(zip(weighted, (encode or _encode)(list(weighted))))
    total = sum(w * vectors[tag] for tag, w in weighted.items())
    norm = np.linalg.norm(total)
    return (total / norm).astype(np.float32) if norm > 0 else None


_model = None
_lock = threading.Lock()
_cache = {}


def _encode(texts):
    """Tag vectors, cached: the library's tag vocabulary is small and repeats."""
    global _model
    with _lock:
        missing = [t for t in texts if t not in _cache]
        if missing:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(GENRE_MODEL)
            for text, vector in zip(missing, _model.encode(missing, normalize_embeddings=True, batch_size=256)):
                _cache[text] = vector
        return [_cache[t] for t in texts]
