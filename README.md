# Bookkeep

Bookkeep is a self-hosted library companion for discovering books, exploring series, and requesting formats through integrated services.

## Features

- Discover trending and popular titles
- Search books, authors, and series
- Author detail pages with bio, portrait, books, and series
- Book and series request flows
- Admin tooling for users, requests, and settings

## Screenshots

### Discover
![Discover](docs/imgs/discover.png)

### Book Details
![Book Details](docs/imgs/book.png)

### Series
![Series](docs/imgs/series.png)

### Search
![Search](docs/imgs/search.png)

### Requests
![Requests](docs/imgs/requests.png)

## Tech Stack

- React + Vite + TypeScript
- Tailwind CSS + shadcn/ui
- FastAPI backend
- PostgreSQL database
- Optional Redis cache

## Requirements

- Docker + Docker Compose
- Hardcover API token (from https://hardcover.app)

## Running with Docker Compose (from source)

The default `docker-compose.yml` builds from source and runs the app with Postgres + Redis.

```sh
export HARDCOVER_API_TOKEN=your_token_here
docker-compose up --build
```

Access:
- App (frontend + API): http://localhost:8000
- API docs: http://localhost:8000/docs

## Running with Docker Compose (Docker Hub image)

Create a separate compose file (e.g. `docker-compose.dockerhub.yml`) with a prebuilt image:

```yaml
version: '3.8'

services:
  app:
    image: akiraslingshot/bookkeep:latest
    container_name: bookkeep
    environment:
      DATABASE_URL: postgresql://bookkeep:bookkeep_password@db:5432/bookkeep_db
      HARDCOVER_API_TOKEN: ${HARDCOVER_API_TOKEN:-}
      REDIS_URL: redis://redis:6379/0
      BOOKKEEP_SECRET_KEY: ${BOOKKEEP_SECRET_KEY:-}
    # volumes:
    #   # Mount host download directories so Bookkeep can access completed downloads.
    #   # Adjust the left side (host path) to match your download client's output directory.
    #   - /path/to/downloads:/downloads
    #   # Example: separate mounts for different media types
    #   # - /data/media/ebooks:/downloads/ebooks
    #   # - /data/media/audiobooks:/downloads/audiobooks
    ports:
      - "8000:8000"
    depends_on:
      - db
      - redis
    restart: unless-stopped

  db:
    image: postgres:16-alpine
    container_name: bookkeep-db
    environment:
      POSTGRES_DB: bookkeep_db
      POSTGRES_USER: bookkeep
      POSTGRES_PASSWORD: bookkeep_password
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: bookkeep-redis
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    restart: unless-stopped

  # Optional: FlareSolverr for direct downloads (solves DDoS-Guard challenges)
  # flaresolverr:
  #   image: ghcr.io/flaresolverr/flaresolverr:latest
  #   container_name: bookkeep-flaresolverr
  #   environment:
  #     LOG_LEVEL: info
  #   ports:
  #     - "8191:8191"
  #   restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
```

Run it:

```sh
docker-compose -f docker-compose.dockerhub.yml up
```

## Local Development with Docker

For local development, create a `dev.docker-compose.yml` (gitignored) based on the production compose file. This lets you add personal paths, volume mounts, and other dev-specific overrides without affecting the committed config.

```yaml
version: '3.8'

services:
  app:
    image: book-hound:latest
    build:
      context: .
      dockerfile: Dockerfile
    container_name: book-hound
    environment:
      DATABASE_URL: postgresql://bookhound:bookhound_password@db:5432/bookhound_db
      HARDCOVER_API_TOKEN: ${HARDCOVER_API_TOKEN:-}
      REDIS_URL: redis://redis:6379/0
      BOOKKEEP_SECRET_KEY: ${BOOKKEEP_SECRET_KEY:-}
    volumes:
      # Map your host download directory into the container
      - /path/to/downloads:/downloads
    ports:
      - "8000:8000"
    depends_on:
      - db
      - redis
    restart: unless-stopped

  db:
    image: postgres:16-alpine
    container_name: book-hound-db
    environment:
      POSTGRES_DB: bookhound_db
      POSTGRES_USER: bookhound
      POSTGRES_PASSWORD: bookhound_password
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: book-hound-redis
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
```

Run it:

```sh
export HARDCOVER_API_TOKEN=your_token_here
docker-compose -f dev.docker-compose.yml up --build
```

## Configuration

Required environment variables:
- `HARDCOVER_API_TOKEN`
- `DATABASE_URL`

Optional:
- `REDIS_URL`
- `BOOKKEEP_SECRET_KEY` - JWT secret key for token signing (see Authentication below)
- `BOOKKEEP_ACCESS_TOKEN_EXPIRE_MINUTES` - Access token expiration (default: 30)
- `BOOKKEEP_REFRESH_TOKEN_EXPIRE_DAYS` - Refresh token expiration (default: 7)

Build-time (frontend):
- `GEOCITIES_MODE` - set to `true` for a retro 90s GeoCities reskin of the whole
  UI (Comic Sans, starfield wallpaper, marquee, hit counter). It's baked into the
  frontend bundle, so set it in `.env` and run `docker compose build` (or build
  with `VITE_GEOCITIES=true` for a local `npm run build`/`npm run dev`). Default `false`.

## Download Client Volumes

If you use download clients (qBittorrent, NZBGet, SABnzbd) running in Docker alongside Bookkeep, you need shared volume mounts so the Bookkeep container can access completed downloads on the host filesystem.

Uncomment and adjust the `volumes` section in your compose file:

```yaml
    volumes:
      # Map your host download directory into the container
      - /path/to/downloads:/downloads
      # Or separate mounts for different media types
      - /data/media/ebooks:/downloads/ebooks
      - /data/media/audiobooks:/downloads/audiobooks
```

The **container path** (right side, e.g. `/downloads`) is what you configure in **Settings > Download Clients** as the download directory. The **host path** (left side) should match where your download client writes completed files.

> **Tip:** If your download client runs in its own container, mount the same host directory into both containers so the paths align. For example, if qBittorrent saves to `/data/downloads` on the host, mount `-v /data/downloads:/downloads` in both containers.

## Authentication

Bookkeep uses JWT (JSON Web Tokens) for authentication. Users log in with username/password and receive an access token and refresh token.

- **Access tokens** are short-lived (default 30 minutes) and used for API requests
- **Refresh tokens** are longer-lived (default 7 days) and used to obtain new access tokens

### Secret Key

Set `BOOKKEEP_SECRET_KEY` to a secure random string for production deployments. This key is used to sign JWT tokens.

```sh
# Generate a secure key
openssl rand -base64 32
```

If not set, a random key is generated at startup. This means **all users will be logged out when the server restarts**. For persistent sessions across restarts, always set this variable.

## Direct Downloads

Bookkeep can download books directly from Anna's Archive and Z-Library without requiring Prowlarr or external download clients. Enable this feature in **Settings > Direct Downloads**.

### How it works

When you search for a book, direct download results appear alongside Prowlarr results. The backend handles the full download lifecycle: finding sources, waiting through server countdowns, solving access challenges, and streaming the file to your configured download path.

### FlareSolverr (recommended)

Anna's Archive slow download servers are protected by DDoS-Guard. To access these sources, you need a running [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) instance to solve the challenges.

Add FlareSolverr to your `docker-compose.yml`:

```yaml
  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
    container_name: bookkeep-flaresolverr
    environment:
      LOG_LEVEL: info
    ports:
      - "8191:8191"
    restart: unless-stopped
```

Then set the FlareSolverr URL in **Settings > Direct Downloads** to:

```
http://flaresolverr:8191
```

Without FlareSolverr, direct downloads will still work but only with sources that don't require challenge solving, which significantly reduces the number of available sources.

### Download paths

Configure your ebook and audiobook download paths in **Settings > Direct Downloads**. The paths must be accessible from within the Docker container. For example, add a volume mount:

```yaml
  app:
    volumes:
      - /path/on/host/ebooks:/downloads/ebooks
      - /path/on/host/audiobooks:/downloads/audiobooks
```

Then set the download paths in settings to `/downloads/ebooks` and `/downloads/audiobooks`.

## Jobs & Scheduling

Bookkeep runs background jobs via APScheduler. You can view and change schedules in Settings → Jobs, and manually trigger a job without changing its schedule.

Default jobs:
- `refresh_seed_data` (daily): pulls fresh books from Hardcover to keep the local catalog populated.
- `check_processing_requests` (every 5 minutes): checks for request status changes and updates requests that have completed.
- `sync_from_booklore` (daily): syncs availability from Booklore, importing items and marking matching requests as available.
- `sync_missing_metadata` (every 6 hours): fills missing metadata (cover, rating, IDs, series) using Hardcover.

Notes:
- Job state (e.g., seed refresh offset) is stored in the database.
- If Redis is unavailable, jobs still run; caching falls back to memory.

## Search Performance Notes

Autocomplete uses cached/local data only. Full searches call the Hardcover search API and cache results for faster subsequent queries.

## Releases & Docker Hub

Images are published to Docker Hub on pushes to `main` and `develop` with multi-arch support (amd64 + arm64).

Required GitHub secrets:
- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

Version bump labels for `develop` → `main` PRs:
- `bump:major`
- `bump:minor`
- `bump:patch`

Main branch publishes:
- `vX.Y.Z`
- `X.Y.Z`
- `latest`

Develop branch publishes:
- `vX.Y.Z-develop-<shortsha>`

## Caching

Redis is used to cache Hardcover API responses. If Redis is unavailable, the app falls back to in-memory caching.

See `backend/README.md` for backend API details.

## A Note on AI Assistance

**In the interest of transparency**: the frontend components in this project were all built with help from AI tools. This includes the React components, UI/UX bits, and styling, especially the theme picker!

It's totally understandable if you are anti-AI or if that's just not your cup of tea, no hard feelings, there are plenty of great alternatives out there.

As a backend-focused Python developer with **many** failed attempts at front end development, I can't express it enough just how helpful it is to offload the cumbersome and mundane parts of this project. 

The backend, API design, database work, and overall system design will remain human.


sync_missing_metadata — every 6 h (tasks.py:1838)
Nothing to do with Calibre. It works on the books table directly and backfills rows that are missing key fields by hitting Hardcover only (not the enrich_book multi-source merge).

It gathers four buckets (tasks.py:1852-1872):

has a hardcover_slug but no numeric hardcover_id
no cover_url and no hardcover_id (max 50)
has hardcover_id but no rating (max 50)
has hardcover_id and a series name/position but no series_id (max 50)
For each, it looks the book up on Hardcover by slug, then by title/author (tasks.py:1897-1904), skips it if another row already owns that hardcover_id, and otherwise fills hardcover_id, cover, description, page_count, rating, series, genres — fill-only (X or book.X), never overwriting. 0.5 s between calls for rate limiting. This is the job that keeps seed-data / Booklore / Audiobookshelf-imported books from staying half-populated.

reconcile_calibre_library — every 60 s (tasks.py:506)
The fast loop that treats Calibre as the source of truth for ebook availability. Per run:

Promote parked downloads — reconcile_ebook_library_imports: any ebook download sitting in awaiting_library that Calibre has now indexed flips to imported, the book goes ebook_available, and a confirmed link is written (tasks.py:528).
Resolve open requests — loads the whole library once, matches every pending/approved/processing/not_found ebook request against it; a hit flips the request to available (however the book got there — download, manual add, side-load) and writes a link (tasks.py:554-606).
Link-table maintenance, throttled to once per 10 min (tasks.py:539-547): heal_stale_links (re-point/drop links whose Calibre id vanished on a delete+re-add), backfill_fuzzy_links (match still-unlinked library books to existing Book rows), sync_availability_flags (set ebook_available/audiobook_available from Calibre formats).
Small enrichment slice — _enrich_linked_calibre_books: up to 10 linked books (tasks.py:630) whose overlay metadata (description/cover/genres) has a gap or was never refreshed, filled via Open Library + Hardcover (enrich_book defaults, no Google/Apple).
Refreshes just-downloaded books, busts the request cache, sends availability emails (tasks.py:611-621).
sync_calibre_metadata — startup + once a day (tasks.py:800)
The full sweep version of the same work, gated on a Calibre library being configured and the overlay toggle being on. Three passes, each capped at 400 books/run (tasks.py:640); leftovers wait for the next run or get nibbled by the per-minute batch:

Link — heal_stale_links + backfill_fuzzy_links (same as above, but unthrottled).
Import unlinked — _import_unlinked_calibre_books (tasks.py:695): for each Calibre book that matches no existing Book row (a side-load you've never seen), create a Book from the Calibre title/author/ISBN, enrich it (Open Library → Hardcover, resolve_hardcover=True), and link it only if something was actually found — otherwise roll back and leave it "metadata from Calibre only". This is the pass that clears that label.
Enrich linked — _enrich_calibre_metadata with limit 400: refresh linked books whose overlay metadata is absent or stale-incomplete.
So: reconcile_calibre_library is the minute-by-minute slice (availability + 10-book enrichment), sync_calibre_metadata is the daily deep pass that also does the "create a Book row for side-loaded Calibre books" step, and sync_missing_metadata is unrelated — it patches thin rows in your own books table straight from Hardcover.




when a book is added to calibre, explain to me how the metadata is attached to the book entry. i thought the cover was coming form itunes but i see in my library it looks like most are just using the hardcover metadata for all fields

Short version: bookkeep never writes metadata into Calibre. Calibre imports the downloaded file itself and builds its record from the EPUB's embedded metadata. What you're looking at in My Books / the Calibre detail page is bookkeep's own overlay — a separate Book row's metadata painted on top of the Calibre entry — and that row is populated almost entirely from Hardcover because of which code path fills it.

The flow when a download lands
The orchestrator copies the file to your Ebook Download Path and parks the task as awaiting_library (orchestrator.py:803). It does not run calibredb — calibre_service.py opens metadata.db read-only and immutable.
Your external Calibre (calibre-web auto-import, a folder watch, whatever) imports the file and derives its title/author/cover/description from the EPUB's OPF.
Once a minute, reconcile_ebook_library_imports matches the parked task to the new Calibre book by ISBN/title, flips it to imported, and writes a confirmed link in calibre_book_links (tasks.py:414-425).
On that state transition — and only then — _refresh_downloaded_books calls enrich_book(..., overwrite=True, use_google=True) (tasks.py:470).
The cover priority you expected
book_metadata.py does say cover = Apple Books → Hardcover → Google → Open Library. That's real, but Apple and Google are queried only when use_google=True (book_metadata.py:159-170).

use_google=True happens on exactly these paths:

a freshly downloaded book being promoted (step 4 above)
admin "Refresh metadata" (calibre.py:537)
admin linking a Calibre book to a Hardcover record (calibre.py:436) — this also sets metadata_locked=True
the metadata source picker
Why most of your library shows Hardcover instead
The jobs that link and fill metadata for books you didn't just download all call enrich_book(db, book) with the defaults — use_google=False, overwrite=False:

sync_calibre_metadata (daily full sweep) → heal_and_backfill
reconcile_calibre_library (per-minute) → books_missing_metadata → tasks.py:668
With use_google=False, Apple's iTunes API is never contacted — only Open Library + Hardcover. And overwrite=False means an existing cover is never replaced. So:

Books created by refresh_seed_data, Booklore sync (tasks.py:1228), or Audiobookshelf sync get cover_url set straight from Hardcover's cached_image at creation, and the gap-fill enrichment never touches it again.
Books first linked by the fuzzy backfill sweep get their gaps filled from Open Library → Hardcover; OL rarely has a usable cover, so it resolves to Hardcover.
Even on the Apple-enabled paths, a Book row with no ISBN (seed data never sets one) forces Apple into a title/author search that must pass titles_match and have artwork (applebooks_metadata.py:124-130); misses fall through to Hardcover.
What the UI actually renders
overlay_book_dict sets overlay_cover_url = book.cover_url, and the frontend uses that first, falling back to Calibre's embedded cover.jpg only when the Book row has no cover (MyBooks.tsx:39-44, CalibreBookDetails.tsx:179). If the linked book has a hardcover_id, the page redirects to /book/:hardcoverId (CalibreBookDetails.tsx:139), which just shows Book.cover_url directly.

To get iTunes covers on existing books: run the admin "Refresh metadata" action (or re-link) on them — that's the only way to trigger the use_google=True merge with overwrite=True after the fact. If you want it to be the default for the background sweeps, sync_calibre_metadata / heal_and_backfill would need to pass use_google=True (at the cost of Google's daily quota and Apple's ~20 req/min limit across your whole library).