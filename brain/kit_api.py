"""HTTP API for the agent kit: chat search, plans, the board, undo points and skill ratings.

server.py includes this router. Every input is checked by pydantic models with limits.
"""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import chatsearch, checkpoints, db, master, settings, skillforge, tasks

router = APIRouter(prefix="/api")
Column = Literal["todo", "doing", "done", "blocked"]


def _bad(exc: Exception, code: int = 400):
    raise HTTPException(status_code=code, detail=str(exc))


@router.get("/chats/search")
def chats_search(q: str = Query("", max_length=300), limit: int = 20, background: bool = False):
    return {"hits": chatsearch.search(q, limit=max(1, min(limit, 100)), include_background=background)}


@router.get("/chats/around/{message_id}")
def chats_around(message_id: int):
    return {"messages": chatsearch.around(message_id)}


# ---------- Plans ----------

class TodoItem(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    status: Literal["pending", "in_progress", "done"] = "pending"


class TodosIn(BaseModel):
    items: list[TodoItem] = Field(max_length=tasks.MAX_ITEMS)


@router.get("/todos/{conversation}")
def todos_get(conversation: str):
    return {"items": tasks.todos(conversation[:200])}


@router.put("/todos/{conversation}")
def todos_put(conversation: str, body: TodosIn):
    return {"items": tasks.set_todos(conversation[:200], [i.model_dump() for i in body.items])}


# ---------- Board ----------

class CardIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    notes: str = Field("", max_length=2000)
    column: Column = "todo"


class CardPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    notes: str | None = Field(None, max_length=2000)
    column: Column | None = None


@router.get("/board")
def board_get():
    return {"columns": list(tasks.COLUMNS), "cards": tasks.board()}


@router.post("/board")
def board_add(body: CardIn):
    try:
        return tasks.add_card(body.title, body.notes, body.column, "owner")
    except tasks.TaskError as exc:
        _bad(exc)


@router.patch("/board/{card_id}")
def board_update(card_id: int, body: CardPatch):
    try:
        return tasks.update_card(card_id, "owner", **body.model_dump())
    except tasks.TaskError as exc:
        _bad(exc, 404 if "No card" in str(exc) else 400)


@router.delete("/board/{card_id}")
def board_delete(card_id: int):
    tasks.delete_card(card_id, "owner")
    return {"ok": True}


# ---------- Undo points ----------

@router.get("/checkpoints")
def checkpoints_get(workspace: str = "", limit: int = 30):
    return {"checkpoints": checkpoints.list_all(workspace[:100], max(1, min(limit, 50)))}


@router.post("/checkpoints/{cid}/restore")
def checkpoints_restore(cid: str):
    from . import workspaces
    try:
        return {"restored": checkpoints.restore(cid, "owner")}
    except (checkpoints.CheckpointError, workspaces.WorkspaceError) as exc:
        _bad(exc, 404)


# ---------- Skill ratings and safety ----------

class RateIn(BaseModel):
    good: bool


@router.get("/skills-ledger")
def skills_ledger():
    return {"ledger": skillforge.ledger(), "needs_work": skillforge.needs_work()}


@router.post("/skills/{name}/rate")
def skills_rate(name: str, body: RateIn):
    _skill_or_404(name)
    return skillforge.rate(name, body.good)


@router.get("/skills/{name}/scan")
def skills_scan(name: str):
    _skill_or_404(name)
    return {"findings": skillforge.scan_skill(name)}


def _skill_or_404(name: str) -> None:
    from . import skills
    try:
        found = skills._read(skills.root() / skills._slug(name))
    except ValueError:
        found = None
    if not found or found["name"] != name:
        raise HTTPException(status_code=404, detail="No skill with that name.")


# ---------- Self-written skills switch ----------

class KitSettingsIn(BaseModel):
    auto_skills: bool


@router.get("/kit-settings")
def kit_settings_get():
    return {"auto_skills": bool(settings.load().get("auto_skills", False))}


@router.put("/kit-settings")
def kit_settings_put(body: KitSettingsIn, request: Request):
    if not master.session_ok(request.cookies.get("brain_session")):
        raise HTTPException(status_code=401, detail="Enter your Master PIN to change this.")
    with settings.edit_lock:
        saved = settings.save({**settings.load(), "auto_skills": body.auto_skills})
    db.audit("owner", "settings.auto_skills", on=body.auto_skills)
    return {"auto_skills": saved["auto_skills"]}
