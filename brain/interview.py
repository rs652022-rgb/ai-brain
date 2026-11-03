"""Weekly life interviews: the richest source of who you are.

Each answer becomes an episodic or semantic memory. Questions are asked in order,
one theme at a time, and skipped once answered.
"""
from . import db, memory

QUESTIONS = [
    # (id, theme, memory type, question)
    ("childhood-1", "Childhood", "episodic", "Where did you grow up, and what is one moment from childhood you still think about?"),
    ("childhood-2", "Childhood", "semantic", "Who shaped you most as a child, and what did they teach you?"),
    ("childhood-3", "Childhood", "episodic", "What did you want to become when you were young, and why?"),
    ("family-1", "Family", "semantic", "Who are the most important people in your family today? Tell me a little about each."),
    ("family-2", "Family", "semantic", "What does a good day with your family look like?"),
    ("values-1", "Values", "semantic", "What are the three things that matter most to you in life, in order?"),
    ("values-2", "Values", "semantic", "What is one thing you would never do, even for a lot of money?"),
    ("values-3", "Values", "semantic", "What kind of person do you respect most? What kind do you avoid?"),
    ("work-1", "Work", "episodic", "How did you start your business? What made you take that step?"),
    ("work-2", "Work", "semantic", "What does a good client look like to you? What makes you turn a client down?"),
    ("work-3", "Work", "procedural", "Walk me through how you price a project."),
    ("work-4", "Work", "semantic", "What do you want your business to look like in five years?"),
    ("turning-1", "Turning points", "episodic", "What is the biggest decision you have made so far? How did you decide?"),
    ("turning-2", "Turning points", "episodic", "Tell me about a time things went badly. What did you learn?"),
    ("turning-3", "Turning points", "episodic", "What is the moment you have been proudest of?"),
    ("mind-1", "How you think", "procedural", "When you face a hard choice, what do you do first?"),
    ("mind-2", "How you think", "semantic", "What drains your energy, and what gives it back?"),
    ("mind-3", "How you think", "semantic", "How do you behave when you are stressed? What helps you most then?"),
    ("style-1", "How you talk", "semantic", "How would your closest friend describe the way you talk?"),
    ("style-2", "How you talk", "semantic", "Which words or phrases do you use all the time?"),
    ("care-1", "Taking care of you", "semantic", "When you are struggling, how do you want to be reminded or nudged?"),
    ("care-2", "Taking care of you", "semantic", "What daily habits do you want to keep, and which do you want to drop?"),
    ("future-1", "Future", "semantic", "What do you want your life to look like in ten years?"),
    ("future-2", "Future", "semantic", "What would you regret not doing?"),
]

BY_ID = {q[0]: q for q in QUESTIONS}


def answered_ids() -> set[str]:
    with db.session() as conn:
        return {r["question_id"] for r in conn.execute("SELECT question_id FROM interview_answers")}


def next_questions(n: int = 1) -> list[dict]:
    done = answered_ids()
    left = [q for q in QUESTIONS if q[0] not in done]
    return [{"id": q[0], "theme": q[1], "question": q[3], "remaining": len(left)} for q in left[:n]]


def progress() -> dict:
    done = len(answered_ids())
    return {"answered": done, "total": len(QUESTIONS)}


def answer(question_id: str, text: str, sensitivity: str = "personal") -> dict:
    if question_id not in BY_ID:
        raise KeyError(question_id)
    text = text.strip()
    if not text:
        raise ValueError("Answer is empty")
    qid, theme, mtype, question = BY_ID[question_id]
    mem = memory.add(f"Q: {question}\nA: {text}", type=mtype, source="interview", sensitivity=sensitivity,
                     tags=[theme.lower()])
    with db.session() as conn:
        conn.execute(
            "INSERT INTO interview_answers (question_id, question, answer, memory_id, created_at) VALUES (?,?,?,?,?)",
            (qid, question, text, mem["id"], db.now()),
        )
    return mem
