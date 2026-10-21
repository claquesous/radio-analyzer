import os
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
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


class FakeDownload:
    def __init__(self, status=200, body=b"ID3 audio"):
        self.status_code = status
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def iter_content(self, chunk_size):
        yield self.body


URL = "https://bucket.s3.amazonaws.com/joy-division/substance/atmosphere.mp3?X-Amz-Signature=abc"


def test_analyze_downloads_a_url(monkeypatch):
    monkeypatch.setattr(server.app.state, "pool", ThreadPoolExecutor(1), raising=False)
    monkeypatch.setattr(requests, "get", lambda url, **kw: FakeDownload())
    seen = {}

    def analyze(path):
        with open(path, "rb") as f:
            seen.update(path=path, body=f.read())
        return {"bpm": 120.0}

    monkeypatch.setattr(audio, "analyze_in_worker", analyze)
    assert client.post("/analyze", json={"url": URL}).json() == {"bpm": 120.0}
    assert seen["body"] == b"ID3 audio" and seen["path"].endswith(".mp3")
    assert not os.path.exists(seen["path"])


def test_identify_downloads_a_url(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, **kw: FakeDownload())
    monkeypatch.setattr(identify, "resolve", lambda song: ("rec-1", "acoustid-1", "acoustid"))
    response = client.post("/identify", json={"url": URL, "title": "Atmosphere", "artist": "Joy Division"})
    assert response.json()["mb_recording_id"] == "rec-1"


@pytest.mark.parametrize("status", [403, 404])
def test_a_url_with_nothing_behind_it_is_a_missing_file(monkeypatch, status):
    monkeypatch.setattr(requests, "get", lambda url, **kw: FakeDownload(status))
    assert client.post("/analyze", json={"url": URL}).status_code == 404
    response = client.post("/identify", json={"url": URL, "title": "x", "artist": "y"})
    assert response.json()["status"] == "missing_file"


def test_a_failed_download_is_a_bad_gateway(monkeypatch):
    def unreachable(url, **kw):
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(requests, "get", unreachable)
    assert client.post("/analyze", json={"url": URL}).status_code == 502
    monkeypatch.setattr(requests, "get", lambda url, **kw: FakeDownload(500))
    assert client.post("/analyze", json={"url": URL}).status_code == 502


def test_songs_need_exactly_one_of_path_and_url(music):
    assert client.post("/analyze", json={}).status_code == 422
    assert client.post("/analyze", json={"path": str(music), "url": URL}).status_code == 422


def test_bearer_token(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "s3cret")
    assert client.get("/versions").status_code == 401
    assert client.get("/versions", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/versions", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200


def test_analyzes_in_process_without_workers(music, monkeypatch):
    monkeypatch.setattr(config, "MB_CONTACT", "test@example.com")
    monkeypatch.setattr(config, "ANALYZE_WORKERS", 0)
    monkeypatch.setattr(audio, "init_worker", lambda: None)
    monkeypatch.setattr(audio, "analyze_in_worker", lambda path: {"pid": os.getpid()})
    with TestClient(server.app) as in_process:
        assert isinstance(server.app.state.pool, ThreadPoolExecutor)
        assert in_process.post("/analyze", json={"path": str(music)}).json() == {"pid": os.getpid()}


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
