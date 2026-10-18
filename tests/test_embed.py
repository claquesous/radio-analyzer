import numpy as np

from analyzer.embed import genre_embedding

AXES = {"post punk": [1, 0], "new wave": [0.9, 0.1], "country": [0, 1]}


def encode(tags):
    return [np.array(AXES[t], dtype=float) / np.linalg.norm(AXES[t]) for t in tags]


def cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def test_genre_embedding_weights_tags_by_source():
    post_punk = genre_embedding([{"tag": "post punk", "weight": 1.0, "source": "musicbrainz"}], encode=encode)
    new_wave = genre_embedding([{"tag": "new wave", "weight": 1.0, "source": "discogs"},
                                {"tag": "country", "weight": 0.2, "source": "id3"}], encode=encode)
    country = genre_embedding([{"tag": "country", "weight": 1.0, "source": "musicbrainz"}], encode=encode)
    assert cosine(post_punk, new_wave) > 0.9
    assert cosine(post_punk, country) < 0.1
    assert abs(np.linalg.norm(new_wave) - 1) < 1e-6


def test_genre_embedding_without_tags():
    assert genre_embedding([], encode=encode) is None
