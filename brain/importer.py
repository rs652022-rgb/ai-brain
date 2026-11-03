"""Import a WhatsApp chat export (.txt) as memories.

Only the owner's own messages become memories and writing samples. Other people's messages
are not stored, per the plan's rule that other people's data is context, never a profile.
"""
import re
from collections import defaultdict

from . import memory

# Handles common WhatsApp export formats, e.g.
#   12/03/2024, 9:15 pm - Rahul Sharma: message
#   [12/03/24, 21:15:02] Rahul Sharma: message
LINE = re.compile(
    r"^\[?(?P<date>\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}),?\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?)\]?"
    r"\s*(?:-\s*)?(?P<who>[^:]{1,60}):\s(?P<msg>.*)$",
    re.IGNORECASE,
)
SKIP = ("<Media omitted>", "This message was deleted", "You deleted this message", "null")


def parse(text: str) -> list[dict]:
    out, cur = [], None
    for raw in text.splitlines():
        line = raw.replace("‎", "").strip()
        m = LINE.match(line)
        if m:
            cur = {"date": m["date"], "who": m["who"].strip(), "msg": m["msg"].strip()}
            out.append(cur)
        elif cur and line:
            cur["msg"] += "\n" + line  # continuation of a multi-line message
    return out


def import_whatsapp(text: str, owner_name: str, chat_with: str = "", max_days: int = 400) -> dict:
    msgs = parse(text)
    if not msgs:
        raise ValueError("No messages found. Export the chat from WhatsApp as .txt 'Without media'.")
    owner_key = owner_name.lower().strip()
    mine = [m for m in msgs if m["who"].lower() == owner_key or m["who"].lower() == "you"]
    if not mine:
        names = sorted({m["who"] for m in msgs})
        raise ValueError(f"No messages from '{owner_name}'. Names in this chat: {', '.join(names[:10])}")

    by_day = defaultdict(list)
    for m in mine:
        if any(m["msg"].startswith(s) for s in SKIP):
            continue
        by_day[m["date"]].append(m["msg"])

    created = 0
    label = f" with {chat_with}" if chat_with else ""
    for day, lines in list(by_day.items())[-max_days:]:
        content = f"Messages I wrote{label} on {day}:\n" + "\n".join(f"- {l}" for l in lines[:40])
        memory.add(content, type="episodic", source="import", sensitivity="private", happened_on=day,
                   tags=["whatsapp"] + ([chat_with.lower()] if chat_with else []), auto_tag=False)
        created += 1

    samples = [m["msg"] for m in mine if 20 <= len(m["msg"]) <= 300 and not any(m["msg"].startswith(s) for s in SKIP)]
    return {"messages_found": len(msgs), "mine": len(mine), "memories_created": created,
            "writing_samples": samples[-15:]}
