"""HTTP API. Stateless: the caller sends what a step needs and stores the result.

Run as a single process. The MusicBrainz/AcoustID/Discogs rate limits are enforced
in-process (http.RateLimiter), so several server processes would exceed them.
Essentia runs in a process pool inside this one server process, or in the server
process itself with ANALYZE_WORKERS=0 (on Lambda).

A song's audio is either a path under MUSIC_ROOT, when the library is mounted
here, or a URL to download it from (e.g. a presigned S3 GET), when it isn't."""
import hmac
import logging
import multiprocessing
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from typing import Optional
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, model_validator

from . import audio, config, embed, enrich, identify

log = logging.getLogger("analyzer")


@asynccontextmanager
async def lifespan(app):
    if not config.MB_CONTACT:
        raise SystemExit("MB_CONTACT must be set (a URL or email for the MusicBrainz User-Agent)")
    if not config.ACOUSTID_API_KEY:
        log.warning("ACOUSTID_API_KEY not set; identifying by MusicBrainz text search only")
    if not config.DISCOGS_TOKEN:
        log.warning("DISCOGS_TOKEN not set; skipping Discogs credits and styles")
    if config.ANALYZE_WORKERS > 0:
        context = multiprocessing.get_context("spawn")
        executor = ProcessPoolExecutor(config.ANALYZE_WORKERS, mp_context=context, initializer=audio.init_worker)
    else:
        executor = ThreadPoolExecutor(1, initializer=audio.init_worker)
    with executor as pool:
        app.state.pool = pool
        yield


def require_token(authorization: Optional[str] = Header(None)):
    if config.API_TOKEN and not hmac.compare_digest(authorization or "", f"Bearer {config.API_TOKEN}"):
        raise HTTPException(401, "invalid or missing bearer token")


app = FastAPI(title="radio-analyzer", lifespan=lifespan)
api = APIRouter(dependencies=[Depends(require_token)])


class SongAudio(BaseModel):
    path: Optional[str] = None
    url: Optional[str] = None

    @model_validator(mode="after")
    def exactly_one_source(self):
        if (self.path is None) == (self.url is None):
            raise ValueError("send exactly one of path or url")
        return self


class IdentifyRequest(SongAudio):
    title: str
    artist: str
    album: Optional[str] = None
    duration: Optional[float] = None


class AnalyzeRequest(SongAudio):
    pass


class EnrichRequest(BaseModel):
    title: str
    artist: str
    album: Optional[str] = None
    year: Optional[int] = None
    id3_genre: Optional[str] = None
    mb_recording_id: Optional[str] = None
    discogs_master_id: Optional[int] = None


class Tag(BaseModel):
    tag: str
    weight: float
    source: str


class GenreEmbeddingRequest(BaseModel):
    tags: list[Tag]


def music_path(path):
    """Only files under MUSIC_ROOT; this service should not read anything else."""
    real = os.path.realpath(path)
    if os.path.commonpath([real, config.MUSIC_ROOT]) != config.MUSIC_ROOT:
        raise HTTPException(400, f"path must be under {config.MUSIC_ROOT}")
    return real


@contextmanager
def song_file(song):
    """A local file with the song's audio, or None if the song is missing: the file
    itself for a path, or a temporary download for a URL."""
    if song.path is not None:
        path = music_path(song.path)
        yield path if os.path.exists(path) else None
        return

    suffix = os.path.splitext(urlparse(song.url).path)[1] or ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix) as f:
        yield f.name if download(song.url, f) else None


def download(url, f):
    """Write url to f. False if there's nothing there: S3 answers 403 rather than 404
    for a missing key unless the signer may list the bucket. Any other failure is a
    502, so the caller retries later instead of recording the song as missing."""
    try:
        with requests.get(url, stream=True, timeout=config.DOWNLOAD_TIMEOUT) as resp:
            if resp.status_code in (403, 404):
                return False
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    except requests.RequestException as error:
        raise HTTPException(502, f"downloading the song failed: {error}")
    f.flush()
    return True


@app.get("/health")
def health():
    return {"ok": True}


@api.get("/versions")
def versions():
    return {"sound": audio.MODEL_VERSION, "genre": embed.GENRE_MODEL_VERSION}


@api.post("/identify")
def identify_song(request: IdentifyRequest):
    with song_file(request) as path:
        if path is None:
            return {"status": "missing_file", "mb_recording_id": None, "acoustid": None}
        song = {"path": path, "title": request.title, "artist_name": request.artist,
                "album_title": request.album, "time": request.duration}
        try:
            return identify.identify_song(song)
        except identify.AcoustIDError as error:
            # A config problem (e.g. a bad API key), not a miss: fail loudly.
            raise HTTPException(502, f"AcoustID: {error}")


@api.post("/analyze")
def analyze_song(request: AnalyzeRequest):
    with song_file(request) as path:
        if path is None:
            raise HTTPException(404, "missing_file")
        try:
            return app.state.pool.submit(audio.analyze_in_worker, path).result()
        except Exception as error:
            log.exception("analyze failed for %s", request.path or "song from url")
            raise HTTPException(422, f"analysis failed: {error}")


@api.post("/enrich")
def enrich_song(request: EnrichRequest):
    return enrich.enrich_song({
        "title": request.title, "artist_name": request.artist, "album_title": request.album,
        "year": request.year, "id3_genre": request.id3_genre,
        "mb_recording_id": request.mb_recording_id, "discogs_master_id": request.discogs_master_id,
    })


@api.post("/genre-embedding")
def genre_embedding(request: GenreEmbeddingRequest):
    vector = embed.genre_embedding([tag.model_dump() for tag in request.tags])
    return {"embedding": None if vector is None else vector.tolist(), "model_version": embed.GENRE_MODEL_VERSION}


app.include_router(api)
