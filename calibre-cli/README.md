# calibre-cli

Your Calibre instance, replacing linuxserver/calibre: the same desktop GUI
(browsable over the web), the same Content Server, **plus** a small HTTP API
that bookkeep's backend calls to push metadata/cover updates and re-embed
that metadata into the book files on disk — neither of which bookkeep's own
backend can safely do itself, since it normally only has **read-only**
network access to a library (see the main repo's
`backend/app/services/calibre_service.py`).

This is built `FROM lscr.io/linuxserver/calibre` rather than reimplementing
its GUI/VNC stack from scratch. Calibre itself (GUI, Content Server,
`calibredb`) all come from that base image, unmodified — this only adds one
extra small service (the agent API) alongside it, using linuxserver's own
supported extension mechanism (`/custom-services.d`, see
[Container Customization](https://docs.linuxserver.io/general/container-customization/)).
It does not touch how the GUI or Content Server work.

## How it fits together

```
                         one container (calibre-cli, FROM linuxserver/calibre)
                        ┌────────────────────────────────────────────────────┐
bookkeep backend --HTTP(X-Api-Key)-->  agent API (:8100)                     │
                        │        |                                           │
                        │        +--calibredb (loopback, authed)-->  Content Server (:8081) --> /config/Calibre Library
                        └────────────────────────────────────────────────────┘
                                 ^                        ^
desktop GUI (browser) --HTTP--/  (:8080 / :8181, unchanged from linuxserver/calibre)
```

Everything the agent does — pushing metadata, pushing a cover, re-embedding
metadata into book files — goes through the Content Server via
`calibredb --with-library=http://localhost:8081`, Calibre's own supported
way to let multiple writers (its own GUI included) touch a library safely.
The agent never opens `metadata.db` itself and never touches book files
directly — `calibredb embed_metadata` does that server-side. The desktop
**GUI** (`8080` HTTP / `8181` HTTPS) is exactly what linuxserver/calibre
already gives you — unrelated to and unaffected by the agent.

## Ports

| Port | What | Notes |
|---|---|---|
| `8080` | Desktop GUI, HTTP | For use behind your own reverse proxy — same as linuxserver/calibre |
| `8181` | Desktop GUI, HTTPS | Direct access, self-signed cert — same as linuxserver/calibre |
| `8081` | Calibre Content Server | Must be enabled once in Calibre's own Preferences (see setup below) |
| `8100` | Agent API | Bound to `127.0.0.1` in the provided compose file — only bookkeep needs it, keep it off the public internet |

## Setup (migrating from an existing linuxserver/calibre container)

1. Copy `.env.example` to `.env`. Set `CALIBRE_CONFIG_HOST_PATH` to the
   **same** host directory your old linuxserver/calibre container used for
   `/config` — this carries over your existing library, GUI settings, and
   users unchanged. Set `PUID`/`PGID`/`TZ`/`PASSWORD` to match what you had
   before. Generate `AGENT_API_KEY` with `openssl rand -hex 32`.
2. Stop and remove your old linuxserver/calibre container — nothing else
   can hold the same `/config` directory open at once.
3. `docker compose up -d --build`
4. Open `https://<host>:8181` — you should see the same Calibre GUI, same
   library, as before.
5. If you haven't already: in the GUI, go to **Preferences → Sharing over
   the net**, enable the Content Server, set its port to `8081`, and turn on
   a username/password (this is a GUI-driven setting — nothing to configure
   from Docker/env for the server side).
6. Put that same username/password into `.env` as `CALIBRE_SERVER_USERNAME`
   / `CALIBRE_SERVER_PASSWORD`, then `docker compose up -d` again so the
   agent picks them up.
7. `curl http://<host>:8100/health` should return `{"status": "ok", ...}`.
8. In bookkeep's admin Settings → Services → Calibre → **Calibre server**,
   enter `http://<host>:8100` as the agent URL and the same `AGENT_API_KEY`,
   enable it, and hit *Test Connection*.

## Known rough edge: cover push

Covers are pushed via `calibredb set_metadata <id> metadata.opf`, with a
`cover.jpg` written alongside a minimal OPF referencing it — Calibre's own
on-disk convention for a book's metadata. This works reliably for **local**
libraries; it hasn't been independently verified against a **remote**
Content Server connection (which is what the agent uses, even over
loopback) in every Calibre version. Test it (`POST /books/<id>/cover` with
an image body) before relying on it — if it doesn't take, per-field
metadata pushes still work fine either way.

## Endpoints (agent API, port 8100)

All except `/health` require `X-Api-Key: <AGENT_API_KEY>`.

- `GET /health`
- `POST /books/{calibre_id}/metadata` — body `{"fields": {"title": "...", "authors": "...", "comments": "...", "tags": ["..."], "identifiers": {"isbn": "..."}, ...}}`
- `POST /books/{calibre_id}/cover` — raw image bytes as the request body
- `POST /books/{calibre_id}/convert` — body `{"target_format": "epub"}` (optional). Runs `calibredb embed_metadata`, re-embedding the book's current metadata into its existing file(s) in place — not a format conversion, despite the path name (kept for compatibility). `target_format` restricts it to one format via `--only-formats`; omit it to re-embed into every format the book has. Always runs, even if that format already exists — the whole point is refreshing what's already there.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `CALIBRE_CONFIG_HOST_PATH` | yes | Your existing Calibre `/config` directory (library + settings) |
| `PUID` / `PGID` / `TZ` | no (defaults 1000/1000/Etc/UTC) | Same as linuxserver/calibre's own |
| `PASSWORD` | no | Optional GUI basic auth, same as linuxserver/calibre's own |
| `CALIBRE_SERVER_USERNAME` / `CALIBRE_SERVER_PASSWORD` | after first-run GUI setup | Content Server auth |
| `CALIBRE_LIBRARY_ID` | no | Only needed if you host more than one library |
| `AGENT_API_KEY` | yes | Shared secret bookkeep sends as `X-Api-Key` |
| `CALIBREDB_TIMEOUT_SECONDS` | no (default 60) | Timeout for metadata/cover calibredb calls |
| `EMBED_METADATA_TIMEOUT_SECONDS` | no (default 300) | Timeout for `calibredb embed_metadata` |

There is no TLS on the agent's own port — put it behind your own network
controls (VPN, firewall, reverse proxy) the same way you would anything
else internal.
