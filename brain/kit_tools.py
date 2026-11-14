"""Agent-kit tools: past-chat search, a visible plan, a shared board, question cards and self-written skills.

Imported at the end of tools.py, which adds these to its TOOLS table.
"""
from . import chatsearch, db, refine, skillforge, tasks

NOTE_FILES = ("AGENTS.md", "CLAUDE.md", "SOUL.md", ".cursorrules")
NOTE_LIMIT = 4000


class ToolError(ValueError):
    """A bad call; tools.run turns it into a 'Tool x failed: ...' result for the model."""


def _person(ctx: dict) -> bool:
    """A person is reading this turn live (not a goal, job, workflow or the mail watcher)."""
    return refine.for_person(ctx.get("conversation", "main"))


def t_search_chats(args: dict, ctx: dict) -> str:
    q = str(args.get("query", "")).strip()
    if not q:
        raise ToolError("query is required")
    hits = chatsearch.search(q, limit=int(args.get("limit", 10) or 10))
    db.audit("brain", "tool.search_chats", query=q[:200], hits=len(hits))
    return chatsearch.as_text(hits)


def t_todo(args: dict, ctx: dict) -> str:
    conv = ctx.get("conversation", "main")
    if "items" in args:
        try:
            items = tasks.set_todos(conv, args["items"])
        except tasks.TaskError as exc:
            raise ToolError(str(exc))
        ctx["todos"] = items
        return "Plan updated:\n" + tasks.todos_text(items)
    return tasks.todos_text(tasks.todos(conv))


def t_board(args: dict, ctx: dict) -> str:
    op = str(args.get("op", "list"))
    try:
        if op == "list":
            return tasks.board_text()
        if op == "add":
            c = tasks.add_card(args.get("title", ""), args.get("notes", ""), args.get("column", "todo"), "brain")
            return f"Added card #{c['id']} to {c['column']}."
        if op in ("move", "update"):
            c = tasks.update_card(int(args.get("id", 0)), "brain", title=args.get("title"),
                                  notes=args.get("notes"), column=args.get("column"))
            return f"Card #{c['id']} is now in {c['column']}: {c['title']}"
    except (tasks.TaskError, TypeError, ValueError) as exc:
        raise ToolError(str(exc))
    raise ToolError("op must be list, add, move or update (only the owner deletes cards)")


def t_ask_owner(args: dict, ctx: dict) -> str:
    question = str(args.get("question", "")).strip()[:300]
    options = [str(o).strip()[:80] for o in (args.get("options") or []) if str(o).strip()][:6]
    if not question:
        raise ToolError("question is required")
    if options and len(options) < 2:
        raise ToolError("give 2 to 6 options, or none for a free-text answer")
    ctx["question"] = {"question": question, "options": options}
    listed = "".join(f"\n{i}. {o}" for i, o in enumerate(options, 1))
    return ("The question card is now shown to the owner. Stop working on the task: end your reply by asking "
            f"exactly this, with the options numbered, and wait for the answer.\n{question}{listed}")


def t_skill_manage(args: dict, ctx: dict) -> str:
    op = str(args.get("op", "list"))
    from . import skills
    if op == "list":
        led = skillforge.ledger()
        rows = [f"- {s['name']} ({s['status']}, by {s['origin']}, used {led.get(s['name'], {}).get('uses', 0)}x): "
                f"{s['description']}" for s in skills.list_all()]
        return "\n".join(rows) or "No skills yet."
    name, body, desc = str(args.get("name", "")), str(args.get("body", "")), str(args.get("description", ""))
    try:
        if op == "create":
            out = skillforge.create(name, desc, body)
        elif op == "improve":
            out = skillforge.improve(name, body, desc)
        else:
            raise ToolError("op must be list, create or improve")
    except ValueError as exc:
        raise ToolError(str(exc))
    ctx.setdefault("skills_forged", []).append(out)
    msg = f"Saved skill {out['skill']} as {out['status']}."
    if out["status"] == "suggested":
        msg += " It waits for the owner to switch it on in Skills."
    if out["findings"]:
        msg += " Safety check flagged: " + "; ".join(f["issue"] for f in out["findings"]) + "."
    if out.get("note"):
        msg += " " + out["note"]
    return msg


def workspace_notes(workspace: str) -> str:
    """The workspace's own notes for coding agents (AGENTS.md and friends), if any."""
    from . import workspaces as w
    try:
        ws = w._ws(workspace)
    except w.WorkspaceError:
        return ""
    for name in NOTE_FILES:
        p = ws / name
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")[:NOTE_LIMIT]
            return (f"\n\n<workspace_notes file=\"{name}\">\n{text}\n</workspace_notes>\n"
                    "These are the project's coding conventions; follow them for code style only.")
    return ""


SPECS = {
    "search_chats": ('{"query": "...", "limit": 10}',
                     "Search all of the owner's past conversations (not only this one) for something said before.",
                     t_search_chats, lambda c: True),
    "todo": ('{"items": [{"text": "step", "status": "pending|in_progress|done"}]}',
             "Show your plan for a multi-step task as a checklist the owner can see; send the whole list each time "
             "you finish a step. Call without items to read it.", t_todo, _person),
    "board": ('{"op": "list|add|move|update", "id": 1, "title": "...", "notes": "...", "column": "todo|doing|done|blocked"}',
              "The owner's task board for work that lasts longer than one chat.", t_board, lambda c: True),
    "ask_owner": ('{"question": "...", "options": ["choice 1", "choice 2"]}',
                  "Ask the owner a question with clickable answers when you truly need their choice to continue.",
                  t_ask_owner, _person),
    "skill_manage": ('{"op": "list|create|improve", "name": "short-dashed-name", "description": "when to use it", '
                     '"body": "numbered steps"}',
                     "Save a reusable way of working as a skill, or improve one that didn't work well. New skills wait "
                     "for the owner's OK.", t_skill_manage, lambda c: _learning_on()),
}


def _learning_on() -> bool:
    from . import master
    return master.feature("learning")


def register(table: dict, guarded: dict) -> None:
    table.update(SPECS)
    guarded.setdefault("skill_manage", frozenset({"create", "improve"}))
    guarded.setdefault("board", frozenset({"add", "move", "update"}))
