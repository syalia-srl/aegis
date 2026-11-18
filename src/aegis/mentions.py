"""@ mentions (docs/superpowers/specs/2026-11-16-at-mentions-design.html).

A person's line that starts with ``@name`` goes somewhere other than the tab
it was typed in. A session's handle sends that session a note, through the
same path as peer.handoff: held while it is busy, so the tab it was typed in
neither waits nor starts a turn. An agent's name runs the line once with that
agent, as a task on the built-in ``mention`` queue, with a tail of the asking
tab and its handle for peer_read. Two or more names make a committee: every
member answers, and once the last does (or MERGE_AFTER_S passes), the asking
tab's agent gets one message with every answer to merge.

The asking session's transcript is the only store. ``mention`` records the
line and its members; ``mention_answer`` one member's answer, from
mention_reply or a run's result; ``mention_merge`` the committee's merge,
sent or skipped; ``mention_carried`` the single answers that rode a person's
next message to the tab, quoted at its head, so the tab's agent knows what
the person read. A restart rearms a waiting committee's timer from them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time

from . import btw
from .commands import Mention
from .monitors import iso_now
from .ops import OpError
from .queues import MENTION_QUEUE
from .transcript.entries import NOTICE_HEADER

log = logging.getLogger("aegis.mentions")

# How long a committee waits for its slowest member before the merge goes out
# with the answers it has.
MERGE_AFTER_S = 30 * 60
# A single answer rides the next person message cut to this many characters.
CARRY_CHARS = 2000

NOTE_ASK = (
    "(Act on it as you see fit. If it asks for an answer, give it with "
    "mention_reply id={id}; the person reads it in {asker}. Do not answer "
    "otherwise.)"
)
NOTE_COMMITTEE = (
    "(A committee waits for your answer: give it with mention_reply id={id}. "
    "The person reads it in {asker}, and {asker} merges it with the others.)"
)
NOTE_FAR = "(Act on it as you see fit.)"

RUN = """You are answering one question from a person, asked from the session \
{asker}. Answer it and stop: do not start work it did not ask for. If it needs \
real work, say so and suggest /enqueue. Your final message is the answer the \
person reads.

Question: {body}

Where they are ({header}; read more with peer_read {asker}):
{tail}"""


def note(body: str, mid: str, asker: str, committee: bool, far: bool) -> str:
    tail = (
        NOTE_FAR
        if far
        else (NOTE_COMMITTEE if committee else NOTE_ASK).format(id=mid, asker=asker)
    )
    return f"A note typed in {asker} (mention#{mid}):\n{body}\n\n{tail}"


def merge_message(e: dict) -> str:
    det = e["detail"]
    names = [m["name"] for m in det["members"]]
    parts = [
        f'The person asked {", ".join(names)}: "{det["body"]}". They read each '
        "answer in this tab. Merge the answers: where they agree, where they "
        "differ and why, and what you recommend.",
    ]
    for m in det["members"]:
        if m["state"] == "answered":
            parts.append(f"## {m['name']}\n{m['answer']}")
        elif m["state"] == "failed":
            parts.append(f"## {m['name']}\n(failed: {m.get('why') or 'no reason'})")
        else:
            parts.append(f"## {m['name']}\n(no answer within the wait)")
    return "\n\n".join(parts)


def carry_text(entries: list[dict]) -> tuple[str, list[str]]:
    """The block a person's next message carries, and the mention ids in it:
    every single answer the tab's agent has not seen yet. A notice block, so
    the person's row shows what they typed and the block a row of its own
    (transcript/entries.py, split_notices)."""
    quotes, ids = [], []
    for e in entries:
        det = e.get("detail") or {}
        if e["kind"] != "mention" or det.get("committee") or det.get("carried"):
            continue
        for m in det.get("members", []):
            if m["state"] != "answered":
                continue
            text = m["answer"]
            if len(text) > CARRY_CHARS:
                text = (
                    text[:CARRY_CHARS]
                    + "… (cut; the whole answer is in this tab's mention block)"
                )
            q = "\n".join(f"> {line}" for line in text.splitlines())
            quotes.append(
                f'{m["name"]} answered the person\'s @ mention ("{det["body"]}"), '
                f"which they read in this tab:\n{q}"
            )
            ids.append(det["id"])
    if not quotes:
        return "", []
    head = f"{NOTICE_HEADER}mention · {', '.join(ids)} · {iso_now()}"
    return "\n\n" + head + "\n" + "\n\n".join(quotes), ids


class Mentions:
    def __init__(self, app) -> None:
        self.app = app
        self._timers: dict[str, asyncio.TimerHandle] = {}

    # -- sending ---------------------------------------------------------------
    async def send(self, s, mentions: list[Mention], body: str, caller) -> dict:
        if not body:
            raise OpError("bad_mention", "a mention needs a message after the names")
        agents, _ = self.app._agents()
        names = {a.name for a in agents if a.enabled}
        reg = self.app.sessions
        members: list[dict] = []
        for m in mentions:
            if m.server is None and not m.session and m.name in names:
                members.append({"name": m.name, "kind": "agent", "m": m})
                continue
            if m.server is None:
                to = reg.by_handle(m.name)
                if to is None:
                    raise OpError("no_session", f'no agent or open session "{m.name}"')
                if to is s:
                    raise OpError("bad_mention", "that is this tab; drop the @")
            members.append({"name": m.address, "kind": "session", "m": m})
        committee = len(members) > 1
        if committee and any(x["m"].server for x in members):
            raise OpError(
                "bad_mention",
                "a committee's members are on this server; mention a linked "
                "server's session alone",
            )
        if any(x["kind"] == "agent" and x["m"].urgent for x in members):
            raise OpError("bad_mention", "@! is for a session's note, not an agent")
        mid = f"m-{secrets.token_hex(3)}"
        rows = []
        for x in members:
            if x["kind"] == "agent":
                state = await self._run(s, mid, x["name"], body)
            else:
                state = await self._note(s, mid, x["m"], body, committee, caller)
            rows.append({"name": x["name"], "kind": x["kind"], "state": state})
        s.report(
            {
                "kind": "mention",
                "id": mid,
                "body": body,
                "committee": committee,
                "members": rows,
            }
        )
        if committee:
            self._arm(s.log_id, mid, MERGE_AFTER_S)
        return {"mention": mid, "members": rows}

    async def _note(self, s, mid, m: Mention, body, committee, caller) -> str:
        r = await self.app.registry.call(
            "peer.handoff",
            {
                "target": m.address,
                "context": note(body, mid, s.handle, committee, m.server is not None),
                **({"urgent": True} if m.urgent else {}),
            },
            caller,
        )
        return "held" if str(r).startswith("held") else "asked"

    async def _run(self, s, mid: str, agent: str, body: str) -> str:
        w = btw.window(s.fold().entries())
        await self.app.queues.enqueue(
            MENTION_QUEUE,
            RUN.format(asker=s.handle, body=body, header=w.header, tail=w.text),
            False,
            s.log_id,
            str(s.spec.cwd),
            owner=s.spec.owner or self.app.user,
            scope=s.spec.scope.record(),
            agent=agent,
            mention={"id": mid, "log_id": s.log_id, "member": agent},
        )
        return "asked"

    # -- answers ---------------------------------------------------------------
    def find(self, mid: str):
        """The open session that asked ``mid`` and its mention entry."""
        for s in list(self.app.sessions.sessions.values()):
            for e in s.fold().entries():
                if e["kind"] == "mention" and e["detail"].get("id") == mid:
                    return s, e
        return None, None

    def reply(self, caller_handle: str, mid: str, text: str) -> dict:
        s, e = self.find(mid)
        if s is None:
            raise OpError("no_mention", f"no open mention {mid!r}")
        member = next(
            (
                m
                for m in e["detail"]["members"]
                if m["kind"] == "session" and m["name"] == caller_handle
            ),
            None,
        )
        if member is None:
            raise OpError("not_asked", f"mention#{mid} did not ask {caller_handle}")
        s.report(
            {
                "kind": "mention_answer",
                "id": mid,
                "member": caller_handle,
                "status": "ok",
                "answer": text,
            }
        )
        self._settle(s, mid)
        return {"status": "answered", "to": s.handle}

    async def task_done(self, t, outcome: str, body: str) -> None:
        """An @agent run finished (queues.on_mention)."""
        s = self.app.sessions.sessions.get(t.mention["log_id"])
        if s is None or s.archived:
            return
        rec = {"kind": "mention_answer", "id": t.mention["id"]}
        rec["member"] = t.mention["member"]
        if outcome == "ok":
            rec.update(status="ok", answer=body)
        else:
            rec.update(status="failed", why=body)
        s.report(rec)
        self._settle(s, t.mention["id"])

    def _settle(self, s, mid: str) -> None:
        e = self._entry(s, mid)
        if e is None or not e["detail"]["committee"] or e["detail"].get("merge"):
            return
        if all(m["state"] in ("answered", "failed") for m in e["detail"]["members"]):
            asyncio.get_running_loop().create_task(self._merge(s.log_id, mid))

    def _entry(self, s, mid: str) -> dict | None:
        return next(
            (
                e
                for e in s.fold().entries()
                if e["kind"] == "mention" and e["detail"].get("id") == mid
            ),
            None,
        )

    # -- the committee's merge -------------------------------------------------
    def _arm(self, log_id: str, mid: str, after: float) -> None:
        loop = asyncio.get_running_loop()
        self._timers[mid] = loop.call_later(
            max(after, 0.0),
            lambda: loop.create_task(self._merge(log_id, mid)),
        )

    async def _merge(self, log_id: str, mid: str) -> None:
        if (h := self._timers.pop(mid, None)) is not None:
            h.cancel()
        s = self.app.sessions.sessions.get(log_id)
        if s is None or s.archived:
            return
        e = self._entry(s, mid)
        if e is None or e["detail"].get("merge"):
            return
        members = e["detail"]["members"]
        n = sum(m["state"] == "answered" for m in members)
        s.report({"kind": "mention_merge", "id": mid, "how": "sent"})
        try:
            await s.deliver(
                f"> from committee · mention#{mid} · {n} of {len(members)} "
                f"answered · {iso_now()}",
                merge_message(e),
                through_lock=True,
            )
        except Exception:
            log.exception("aegis: could not deliver the merge of %s", mid)

    def skip(self, s, mid: str) -> dict:
        e = self._entry(s, mid)
        if e is None or not e["detail"]["committee"]:
            raise OpError("no_mention", f"no committee {mid!r} in this session")
        if e["detail"].get("merge"):
            return {"status": e["detail"]["merge"]}
        if (h := self._timers.pop(mid, None)) is not None:
            h.cancel()
        s.report({"kind": "mention_merge", "id": mid, "how": "skipped"})
        return {"status": "skipped"}

    # -- the person's next message -----------------------------------------------
    def carry(self, s) -> tuple[str, list[str]]:
        return carry_text(s.fold().entries())

    def carried(self, s, ids: list[str]) -> None:
        if ids:
            s.report({"kind": "mention_carried", "ids": ids})

    # -- lifecycle -------------------------------------------------------------
    def boot(self) -> None:
        """Rearm the timer of every committee still waiting for its merge."""
        now = time.time()
        for s in list(self.app.sessions.sessions.values()):
            for e in s.fold().entries():
                det = e["detail"] if e["kind"] == "mention" else None
                if det and det.get("committee") and not det.get("merge"):
                    self._arm(
                        s.log_id, det["id"], (e["ts"] or now) + MERGE_AFTER_S - now
                    )

    def shutdown(self) -> None:
        for h in self._timers.values():
            with contextlib.suppress(Exception):
                h.cancel()
        self._timers.clear()
