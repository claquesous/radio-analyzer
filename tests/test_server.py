from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from analyzer import audio, config, enrich, identify, server
from analyzer.credits import CreditSet

# Not entered as a context manager, so the lifespan (MB_CONTACT check, Essentia
# process pool) doesn't run.
client = TestClient(server.app)


@pytest.fixture
def music(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MUSIC_ROOT", str(tmp_path.resolve()))
    song = tmp_path / "Joy Division" / "Substance" / "01 Atmosphere.mp3"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"")
    return song


def test_paths_outside_the_music_root_are_rejected(music):
    response = client.post("/analyze", json={"path": "/etc/passwd"})
    assert response.status_code == 400
    response = client.post("/analyze", json={"path": f"{music.parent}/../../../etc/passwd"})
    assert response.status_code == 400


def test_identify(music, monkeypatch):
    seen = {}

    def resolve(song):
        seen.update(song)
        return "rec-1", "acoustid-1", "acoustid"

    monkeypatch.setattr(identify, "resolve", resolve)
    response = client.post("/identify", json={"path": str(music), "title": "Atmosphere",
                                              "artist": "Joy Division", "album": "Substance", "duration": 250})
    assert response.json() == {"status": "acoustid", "mb_recording_id": "rec-1", "acoustid": "acoustid-1"}
    assert seen["artist_name"] == "Joy Division" and seen["album_title"] == "Substance" and seen["time"] == 250


def test_identify_missing_file_and_errors(music, monkeypatch):
    response = client.post("/identify", json={"path": str(music.parent / "gone.mp3"), "title": "x", "artist": "y"})
    assert response.json()["status"] == "missing_file"

    def boom(song):
        raise RuntimeError("timeout")

    monkeypatch.setattr(identify, "resolve", boom)
    response = client.post("/identify", json={"path": str(music), "title": "x", "artist": "y"})
    assert response.json() == {"status": "error", "mb_recording_id": None, "acoustid": None}


def test_identify_acoustid_config_errors_fail_loudly(music, monkeypatch):
    def bad_key(song):
        raise identify.AcoustIDError("invalid API key")

    monkeypatch.setattr(identify, "resolve", bad_key)
    response = client.post("/identify", json={"path": str(music), "title": "x", "artist": "y"})
    assert response.status_code == 502


def test_analyze(music, monkeypatch):
    monkeypatch.setattr(server.app.state, "pool", ThreadPoolExecutor(1), raising=False)
    monkeypatch.setattr(audio, "analyze_in_worker", lambda path: {"bpm": 120.0, "path": path})
    response = client.post("/analyze", json={"path": str(music)})
    assert response.json() == {"bpm": 120.0, "path": str(music)}

    assert client.post("/analyze", json={"path": str(music.parent / "gone.mp3")}).status_code == 404

    def corrupt(path):
        raise ValueError("bad mp3")

    monkeypatch.setattr(audio, "analyze_in_worker", corrupt)
    assert client.post("/analyze", json={"path": str(music)}).status_code == 422


def test_enrich_reports_only_the_sources_it_reached(monkeypatch):
    credits = CreditSet()
    credits.add({"name": "Martin Hannett", "mb_artist_id": "mb-hannett"}, "producer")
    monkeypatch.setattr(enrich, "musicbrainz_metadata", lambda song: {
        "credits": credits, "tags": {"post-punk": 1.0}, "year": 1980,
        "ids": {"mb_work_ids": ["w1"], "mb_artist_id": "mb-jd"}})
    monkeypatch.setattr(config, "DISCOGS_TOKEN", "token")
    monkeypatch.setattr(enrich, "discogs_metadata", lambda song: None)

    body = client.post("/enrich", json={"title": "Atmosphere", "artist": "Joy Division", "album": "Substance",
                                        "year": 1988, "id3_genre": " Rock ", "mb_recording_id": "rec-1"}).json()
    assert body["status"] == "musicbrainz"
    assert body["sources"] == ["musicbrainz"]
    assert body["release_year"] == 1980
    assert body["credits"] == [{"name": "Martin Hannett", "mb_artist_id": "mb-hannett", "discogs_id": None,
                                "role": "producer", "detail": None, "source": "musicbrainz"}]
    assert body["tags"] == {"musicbrainz": {"post-punk": 1.0}, "id3": {"rock": 1.0}}
    assert body["mb_work_ids"] == ["w1"] and body["mb_artist_id"] == "mb-jd"
    assert "discogs_master_id" not in body


def test_enrich_without_any_source_falls_back_to_id3(monkeypatch):
    monkeypatch.setattr(config, "DISCOGS_TOKEN", None)
    body = client.post("/enrich", json={"title": "x", "artist": "y", "year": 1999}).json()
    assert body == {"status": "id3_only", "sources": [], "release_year": 1999, "credits": [], "tags": {"id3": {}}}


def test_enrich_errors(monkeypatch):
    def boom(song):
        raise RuntimeError("503")

    monkeypatch.setattr(enrich, "musicbrainz_metadata", boom)
    body = client.post("/enrich", json={"title": "x", "artist": "y", "mb_recording_id": "rec-1"}).json()
    assert body == {"status": "error"}


def test_genre_embedding(monkeypatch):
    import numpy as np

    monkeypatch.setattr(server.embed, "_encode", lambda tags: [np.array([1.0, 0.0]) for _ in tags])
    body = client.post("/genre-embedding", json={"tags": [{"tag": "rock", "weight": 1, "source": "id3"}]}).json()
    assert body["embedding"] == [1.0, 0.0]
    assert client.post("/genre-embedding", json={"tags": []}).json()["embedding"] is None
