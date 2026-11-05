"""TODO盤面 — MattermostのTODOチャンネルを読み書きする小さなBFF。

Mattermostを正本にし、親投稿（案件カード）の表をそのまま編集する。
ブラウザにはトークンを渡さない（サーバ側で保持）。
"""
from __future__ import annotations

import base64
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

BASE = os.environ.get("MM_BASE", "https://mattermost.example.com")
TOKEN = os.environ.get("MM_BOT_TOKEN") or os.environ.get("MATTERMOST_BOT_TOKEN") or ""
TEAM = os.environ.get("MM_TEAM", "ai")
CHANNEL = os.environ.get("MM_CHANNEL", "town-square")
BASIC_USER = os.environ.get("BOARD_USER", "")
BASIC_PASS = os.environ.get("BOARD_PASS", "")
JST = timezone(timedelta(hours=9))

app = FastAPI(title="todo-board")

ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$")
STATE_RE = re.compile(r"^状態:\s*(.+)$")
UPDATED_RE = re.compile(r"^更新:\s*(.+)$")


@dataclass
class Card:
    post_id: str
    title: str
    state: str = ""
    updated: str = ""
    rows: list[dict] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)
    tags: str = ""


def _auth_header() -> dict[str, str]:
    if not TOKEN:
        raise HTTPException(500, "MM_BOT_TOKEN が未設定です")
    return {"Authorization": f"Bearer {TOKEN}"}


async def mm_get(path: str, **params):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{BASE}/api/v4{path}", headers=_auth_header(), params=params)
    if r.status_code >= 400:
        raise HTTPException(502, f"Mattermost GET {path}: {r.status_code}")
    return r.json()


async def mm_post(path: str, payload: dict):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{BASE}/api/v4{path}", headers=_auth_header(), json=payload)
    if r.status_code >= 400:
        raise HTTPException(502, f"Mattermost POST {path}: {r.status_code}")
    return r.json()


async def mm_put(path: str, payload: dict):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.put(f"{BASE}/api/v4{path}", headers=_auth_header(), json=payload)
    if r.status_code >= 400:
        raise HTTPException(502, f"Mattermost PUT {path}: {r.status_code}")
    return r.json()


async def channel_id() -> str:
    ch = await mm_get(f"/teams/name/{TEAM}/channels/name/{CHANNEL}")
    return ch["id"]


def parse_card(post: dict) -> Card | None:
    msg = post.get("message", "")
    lines = msg.splitlines()
    if not lines or not lines[0].startswith("## "):
        return None
    card = Card(post_id=post["id"], title=lines[0][3:].strip())
    for line in lines[1:]:
        s = line.strip()
        if s.startswith("|"):
            m = ROW_RE.match(s)
            if m:
                card.rows.append(
                    {"n": int(m.group(1)), "todo": m.group(2), "status": m.group(3), "due": m.group(4)}
                )
            continue
        if m := STATE_RE.match(s):
            card.state = m.group(1)
        elif m := UPDATED_RE.match(s):
            card.updated = m.group(1)
        elif s.startswith("#"):
            card.tags = s
    return card


def render_card(card: Card) -> str:
    out = [f"## {card.title}"]
    if card.state:
        out.append(f"状態: {card.state}")
    out.append(f"更新: {datetime.now(JST).strftime('%m-%d %H:%M')}")
    out.append("")
    out.append("| # | TODO | 状況 | 期限 |")
    out.append("| --- | --- | --- | --- |")
    for i, row in enumerate(card.rows, 1):
        row["n"] = i
        out.append(f"| {i} | {row['todo']} | {row['status']} | {row['due']} |")
    links = []
    if card.links:
        out.append("")
        links = card.links
        for name, url in links:
            out.append(f"- {name}: {url}")
    if card.tags:
        out.append("")
        out.append(card.tags)
    return "\n".join(out) + "\n"


async def load_cards() -> list[Card]:
    ch = await channel_id()
    data = await mm_get(f"/channels/{ch}/posts", per_page=60)
    order = data.get("order", [])
    posts = data.get("posts", {})
    cards: list[Card] = []
    for pid in order:
        post = posts.get(pid, {})
        if post.get("root_id"):
            continue
        card = parse_card(post)
        if card:
            card.links = parse_links(post.get("message", ""))
            cards.append(card)
    return cards


def parse_links(msg: str) -> list[tuple[str, str]]:
    links = []
    for line in msg.splitlines():
        m = re.match(r"^-\s*([^:]+):\s*(https?://\S+)\s*$", line.strip())
        if m:
            links.append((m.group(1).strip(), m.group(2)))
    return links


async def save_card(card: Card) -> None:
    await mm_put(f"/posts/{card.post_id}", {"id": card.post_id, "message": render_card(card)})


def _check_auth(request: Request) -> None:
    if not BASIC_USER:
        return
    hdr = request.headers.get("authorization", "")
    if not hdr.startswith("Basic "):
        raise HTTPException(401, "auth required", headers={"WWW-Authenticate": "Basic"})
    try:
        user, _, pw = base64.b64decode(hdr[6:]).decode().partition(":")
    except Exception:
        raise HTTPException(401, "bad auth")
    ok = secrets.compare_digest(user, BASIC_USER) & secrets.compare_digest(pw, BASIC_PASS)
    if not ok:
        raise HTTPException(401, "bad auth", headers={"WWW-Authenticate": "Basic"})


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if request.url.path.startswith("/api/") or request.url.path == "/":
        try:
            _check_auth(request)
        except HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status_code,
                                headers=e.headers or {})
    return await call_next(request)


class StatusIn(BaseModel):
    post_id: str
    n: int
    status: str


class RowIn(BaseModel):
    post_id: str
    todo: str
    status: str = "未着手"
    due: str = "—"


class StateIn(BaseModel):
    post_id: str
    state: str


class NewCardIn(BaseModel):
    title: str
    state: str = ""
    todos: list[str] = []


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/api/board")
async def board():
    cards = await load_cards()
    return {"cards": [c.__dict__ for c in cards], "channel": CHANNEL}


@app.post("/api/status")
async def set_status(body: StatusIn):
    cards = await load_cards()
    card = next((c for c in cards if c.post_id == body.post_id), None)
    if not card:
        raise HTTPException(404, "card not found")
    for row in card.rows:
        if row["n"] == body.n:
            row["status"] = body.status
            await save_card(card)
            return {"ok": True}
    raise HTTPException(404, "row not found")


@app.post("/api/row")
async def add_row(body: RowIn):
    cards = await load_cards()
    card = next((c for c in cards if c.post_id == body.post_id), None)
    if not card:
        raise HTTPException(404, "card not found")
    card.rows.append({"n": len(card.rows) + 1, "todo": body.todo, "status": body.status, "due": body.due})
    await save_card(card)
    return {"ok": True}


@app.post("/api/state")
async def set_state(body: StateIn):
    cards = await load_cards()
    card = next((c for c in cards if c.post_id == body.post_id), None)
    if not card:
        raise HTTPException(404, "card not found")
    card.state = body.state
    await save_card(card)
    return {"ok": True}


@app.post("/api/card")
async def new_card(body: NewCardIn):
    ch = await channel_id()
    card = Card(post_id="", title=body.title, state=body.state)
    card.rows = [{"n": i + 1, "todo": t, "status": "未着手", "due": "—"} for i, t in enumerate(body.todos)]
    msg = render_card(card)
    created = await mm_post("/posts", {"channel_id": ch, "message": msg})
    return {"ok": True, "post_id": created.get("id")}
