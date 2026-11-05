# todo-board

A small web board for a Mattermost TODO channel. Mattermost stays the source of truth:
the app parses the card posts in one channel, edits the tables in place, and keeps no database.

Built for a one-person workflow where TODOs live as parent posts with a table, and updates
are replies. The board makes that editable from a browser, so people who don't use a
terminal can add items and move statuses.

## What it does

- `GET /api/board` — lists cards (title, state, updated, table rows, links)
- `POST /api/status` — `{post_id, n, status}` rewrites one status cell in the parent post
- `POST /api/row` — `{post_id, todo, status, due}` appends a row
- `POST /api/state` — `{post_id, state}` rewrites the state line
- `POST /api/card` — `{title, state, todos[]}` creates a new card

The browser never sees the bot token. A basic-auth gate can be enabled with `BOARD_USER` /
`BOARD_PASS`.

## Post format it expects

```
## <case name>
State: <current state>
Updated: <MM-DD HH:MM>

| # | TODO | status | due |
| --- | --- | --- | --- |
| 1 | Take ID photo | todo | 11/06 |
```

Replies to the parent post are treated as history and ignored by the parser.

## Run locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install fastapi uvicorn httpx
export MM_BASE=https://your-mattermost.example.com
export MATTERMOST_BOT_TOKEN=...      # bot token with post access
export MM_TEAM=your-team
export MM_CHANNEL=your-todo-channel
uvicorn app:app --host 127.0.0.1 --port 8019
```

Docker:

```bash
docker build -t todo-board .
docker run --rm -p 8019:8000 --env-file .env todo-board
```

`docker-compose.example.yml` shows a reverse-proxy setup (Traefik labels) with basic auth.

## Notes

- Parsing is regex-based on the table format above; it is not a general Markdown parser.
- No concurrency control. Edits rewrite the whole post, so simultaneous edits are last-write-wins.
- Designed for a handful of cards and a few dozen rows; no pagination.
