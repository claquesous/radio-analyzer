# radio-analyzer

A stateless HTTP service that works out per-song metadata and embeddings for a
music recommender. It has **no database**: the caller decides which songs need work,
sends what each step needs, and stores the results. The service only reads the
music library (read-only mount) and keeps its own cache of MusicBrainz/Discogs
responses.

## API

| Endpoint | What it does | Source |
|---|---|---|
| `POST /identify` `{path, title, artist, album, duration}` | MP3 → MusicBrainz recording ID: `{status, mb_recording_id, acoustid}` | AcoustID fingerprint (`fpcalc`), falling back to MB text search |
| `POST /analyze` `{path}` | BPM, key/mode, loudness, danceability, 1280-d `sound_embedding`, Discogs-400 `styles` and the `style_tags` derived from them. 404 if the file is missing, 422 if Essentia can't read it. | Essentia (Discogs-EffNet), computed locally |
| `POST /enrich` `{title, artist, album, year, id3_genre, mb_recording_id, discogs_master_id}` | Credits (performer, producer, engineer, composer, lyricist…), tags per source, original release year, and any MBIDs / Discogs master ID it found. `sources` lists the sources it reached; only those should replace stored data. | MusicBrainz recording + work + release-group, Discogs master/release |
| `POST /genre-embedding` `{tags: [{tag, weight, source}]}` | 384-d embedding of a song's tags (bge-small text embeddings, weighted by source) | — |
| `GET /versions` | Current `sound` / `genre` model versions, so the caller can reprocess songs after an upgrade | |
| `GET /health` | | |

Paths must be under `MUSIC_ROOT` (default `/music`).

Anything that needs the whole library, such as creator embeddings built from
credits or calibrating similarity scores, is left to the caller.

## Running

```bash
docker compose build analyzer
docker compose --profile analyzer up -d analyzer
docker compose run --rm --entrypoint python analyzer -m pytest -q tests
```

Env (in the workspace `.env`):

- `MB_CONTACT` **required**: a URL or email MusicBrainz can reach you at (sent in the User-Agent).
- `ACOUSTID_API_KEY`: register at https://acoustid.org/new-application. Without it, identification uses text search only.
- `DISCOGS_TOKEN`: https://www.discogs.com/settings/developers. Without it, the Discogs credits and styles are skipped.
- `ANALYZE_WORKERS` (default 2): Essentia processes. `PORT` (8000), `MUSIC_ROOT`, `CACHE_DIR`, `MODELS_DIR`, `LOG_LEVEL`.

## Notes

- **One server process.** The MusicBrainz (1 req/s), AcoustID and Discogs rate limits
  are enforced in-process, so don't run several uvicorn workers or replicas behind one IP.
  Concurrency comes from the Essentia process pool and from threads waiting on the limiter.
- **amd64 only.** `essentia-tensorflow` publishes x86_64 Linux wheels only, and compose builds and runs
  the image as `linux/amd64` (Rosetta on Apple silicon: about 1.7 s/song with 4 workers).
- **Rate limits** set the backfill time: enrichment makes about 2–4 MusicBrainz requests per song.
  Responses are cached in the `analyzer_cache` volume, so re-runs and retries are cheap.
- **Model versions** are reported by `/versions`. Changing a model's version constant
  tells the caller to reprocess every song for that facet.
