# Clutch crawler

> **Archived.** [Renown.gg shut down on 30 January 2026](https://renown.gg/blog/renown-is-shutting-down), so this backend and the [Clutch extension](https://github.com/renown-clutch/extension) it served are retired. Clutch moved on to [setpos.gg](https://setpos.gg), a CS2 utility site.

The backend behind Clutch, a browser extension for Renown.gg (CS2 matchmaking). It crawled Renown with Playwright, stored players, matches, stats, skin drops, bans and FACEIT profiles in PostgreSQL, and answered the extension's requests through a FastAPI service. It ran from April to October 2025.

## Layout

- `core.py`: entry point. Runs the scheduler (APScheduler) that queues and runs the crawl jobs.
- `clutch/crawl_users.py`, `crawl_matches.py`, `crawl_friends.py`, `crawl_banlist.py`: the crawlers.
- `clutch/db/models.py`: the database schema (peewee on PostgreSQL).
- `api.py`: the API the extension called (FACEIT ranks and ELO, ban history, matchups, team map stats, live matches, recent drops, chat).
- `clutch/cache.py`, `cache_invalidator.py`: Redis response cache.
- `socketio_server.py`, `clutch/chat.py`: real-time chat for extension users.
- `clutch/social_graph_server.py`, `analyze_social_graph.py`: a visualizer for Renown's friend graph.
- `oneshots/`: one-off scripts. The datasets they produced aren't included.

## Running it

```
uv sync
uv run python -m playwright install chromium
uv run python core.py         # crawlers and scheduler
uv run uvicorn api:app        # API
```

Configuration comes from environment variables (`python-decouple`); see `clutch/db/models.py` and `clutch/chat.py` for the names. There is nothing left to crawl: Renown is gone.
