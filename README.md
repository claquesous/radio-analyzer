# radio-analyzer

A stateless HTTP service that works out per-song metadata and embeddings for a
music recommender. It has **no database**: the caller decides which songs need work,
sends what each step needs, and stores the results. The service only reads the
music library, from a read-only mount or from URLs the caller sends (e.g.
presigned S3 GETs), and keeps its own cache of MusicBrainz/Discogs responses.

## API

| Endpoint | What it does | Source |
|---|---|---|
| `POST /identify` `{path or url, title, artist, album, duration}` | MP3 → MusicBrainz recording ID: `{status, mb_recording_id, acoustid}` | AcoustID fingerprint (`fpcalc`), falling back to MB text search |
| `POST /analyze` `{path or url}` | BPM, key/mode, loudness, danceability, 1280-d `sound_embedding`, Discogs-400 `styles` and the `style_tags` derived from them. 404 if the file is missing, 422 if Essentia can't read it. | Essentia (Discogs-EffNet), computed locally |
| `POST /enrich` `{title, artist, album, year, id3_genre, mb_recording_id, discogs_master_id}` | Credits (performer, producer, engineer, composer, lyricist…), tags per source, original release year, and any MBIDs / Discogs master ID it found. `sources` lists the sources it reached; only those should replace stored data. | MusicBrainz recording + work + release-group, Discogs master/release |
| `POST /genre-embedding` `{tags: [{tag, weight, source}]}` | 384-d embedding of a song's tags (bge-small text embeddings, weighted by source) | — |
| `GET /versions` | Current `sound` / `genre` model versions, so the caller can reprocess songs after an upgrade | |
| `GET /health` | | |

A song's audio is either a `path` under `MUSIC_ROOT` (default `/music`), or a `url`
the service downloads it from. A URL answering 403 or 404 counts as a missing file;
any other download failure is a 502, so the caller retries later.

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
- `ANALYZER_TOKEN`: when set, every endpoint but `/health` requires `Authorization: Bearer <token>`.
- `ANALYZE_WORKERS` (default 2): Essentia processes. `0` analyzes in the server process instead (on Lambda).
- `PORT` (8000), `MUSIC_ROOT`, `CACHE_DIR`, `MODELS_DIR`, `LOG_LEVEL`.

## Deploying to Lambda

`sst.config.ts` runs this same image on Lambda behind a Function URL. The image has the
[Lambda Web Adapter](https://github.com/awslabs/aws-lambda-web-adapter) baked in, which turns
invocations into HTTP requests to the server, so it needs no Lambda-specific code. It needs
Docker running and AWS credentials:

```bash
npm install
npx sst secret set AnalyzerToken "$(openssl rand -hex 32)" --stage production
npx sst secret set MbContact you@example.com --stage production
npx sst secret set AcoustidApiKey <key> --stage production   # optional
npx sst secret set DiscogsToken <token> --stage production   # optional
npm run deploy                                               # prints the Function URL
```

The function has 3 GB of memory (the models peak around 1.6 GB), a 300s timeout and
`ANALYZE_WORKERS=0`, since Lambda has no `/dev/shm` for a process pool. Its reserved
concurrency is 1, which keeps the metadata APIs' in-process rate limits correct; requests
beyond that get a 429. Reserving it fails on an account still on the new-account
concurrency quota of 10, which leaves nothing to reserve; ask for the default 1,000 in
Service Quotas. The metadata cache lives in `/tmp` and lasts as long as the instance
stays warm.

Callers send songs as `url`s and the bearer token. For the ClaqRadio backend that's
`ANALYZER_URL` (the Function URL), `ANALYZER_TOKEN`, `ANALYZER_AUDIO=s3` and
`ANALYZER_CONCURRENCY=1`.

## Notes

- **One server process.** The MusicBrainz (1 req/s), AcoustID and Discogs rate limits
  are enforced in-process, so don't run several uvicorn workers or replicas behind one IP
  (on Lambda, more than one concurrent instance).
  Concurrency comes from the Essentia process pool and from threads waiting on the limiter.
- **amd64 only.** `essentia-tensorflow` publishes x86_64 Linux wheels only, and compose builds and runs
  the image as `linux/amd64` (Rosetta on Apple silicon: about 1.7 s/song with 4 workers).
- **Rate limits** set the backfill time: enrichment makes about 2–4 MusicBrainz requests per song.
  Responses are cached in the `analyzer_cache` volume, so re-runs and retries are cheap.
- **Model versions** are reported by `/versions`. Changing a model's version constant
  tells the caller to reprocess every song for that facet.
