"""HTTP API. Stateless: the caller sends what a step needs and stores the result.

Run as a single process. The MusicBrainz/AcoustID/Discogs rate limits are enforced
in-process (http.RateLimiter), so several server processes would exceed them.
Essentia runs in a process pool inside this one server process."""
import asyncio
import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

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
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(config.ANALYZE_WORKERS, mp_context=context, initializer=audio.init_worker) as pool:
        app.state.pool = pool
        yield


app = FastAPI(title="radio-analyzer", lifespan=lifespan)


class IdentifyRequest(BaseModel):
    path: str
    title: str
    artist: str
    album: Optional[str] = None
    duration: Optional[float] = None


class AnalyzeRequest(BaseModel):
    path: str


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


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/versions")
def versions():
    return {"sound": audio.MODEL_VERSION, "genre": embed.GENRE_MODEL_VERSION}


@app.post("/identify")
def identify_song(request: IdentifyRequest):
    song = {"path": music_path(request.path), "title": request.title, "artist_name": request.artist,
            "album_title": request.album, "time": request.duration}
    try:
        return identify.identify_song(song)
    except identify.AcoustIDError as error:
        # A config problem (e.g. a bad API key), not a miss: fail loudly.
        raise HTTPException(502, f"AcoustID: {error}")


@app.post("/analyze")
async def analyze_song(request: AnalyzeRequest):
    path = music_path(request.path)
    if not os.path.exists(path):
        raise HTTPException(404, "missing_file")
    try:
        return await asyncio.get_running_loop().run_in_executor(app.state.pool, audio.analyze_in_worker, path)
    except Exception as error:
        log.exception("analyze failed for %s", path)
        raise HTTPException(422, f"analysis failed: {error}")


@app.post("/enrich")
def enrich_song(request: EnrichRequest):
    return enrich.enrich_song({
        "title": request.title, "artist_name": request.artist, "album_title": request.album,
        "year": request.year, "id3_genre": request.id3_genre,
        "mb_recording_id": request.mb_recording_id, "discogs_master_id": request.discogs_master_id,
    })


@app.post("/genre-embedding")
def genre_embedding(request: GenreEmbeddingRequest):
    vector = embed.genre_embedding([tag.model_dump() for tag in request.tags])
    return {"embedding": None if vector is None else vector.tolist(), "model_version": embed.GENRE_MODEL_VERSION}
