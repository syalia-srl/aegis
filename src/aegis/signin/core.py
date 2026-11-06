"""SignIns: one card per harness whose sign-in failed, driving its own login (#946).

When a turn fails to sign in, ``failed`` opens a card in that session's
transcript, or adds the session to the card already open for that harness, and
starts the harness's login command (recipes.py) in a terminal that stores
nothing on disk. Every chunk the command prints is read raw into a Screen, to
know which step it is on, and masked before the terminal's ring and browsers
see it. What the person types on the card goes to the command's input only.

When the screen shows a token, the harness's check runs on it (for Claude:
one request with it, then the token file); then every waiting session restarts
and is told to go on. A wrong code, a cancel, a timeout or a screen no rule
knows ends the card ``failed``, and the sessions keep waiting for the next
one. A card left open by a server restart is ended ``interrupted``.

Spec: docs/superpowers/specs/2026-11-05-harness-sign-in-design.html.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .mask import Masker
from .recipes import Recipe
from .screen import Screen

if TYPE_CHECKING:
    from ..session import Session
    from ..terminal import Terminal
    from ..terminals import Terminals

log = logging.getLogger("aegis.signin")

UNKNOWN_SCREEN_S = 15.0
TICK_S = 1.0
TAIL_ROWS = 6
ROWS = 40
OPEN = ("starting", "open_url", "paste", "device_code", "choose", "checking")
LABEL = {"claude-code": "Claude", "opencode": "OpenCode"}


@dataclass
class Card:
    card_id: str
    harness: str
    holder: str  # the log id whose transcript holds the card
    waiting: list[str]
    step: str = "starting"
    fields: dict = field(default_factory=dict)
    term: Terminal | None = None
    screen: Screen | None = None
    matched_at: float = 0.0
    started_at: float = 0.0
    task: asyncio.Task | None = None


class Tap:
    """What a sign-in terminal's output goes through: raw into the screen,
    masked out to the ring and browsers."""

    def __init__(
        self, screen: Screen, masker: Masker, on_screen: Callable[[], None]
    ) -> None:
        self.screen, self.masker, self.on_screen = screen, masker, on_screen

    def feed(self, text: str) -> str:
        self.screen.feed(text)
        self.on_screen()
        return self.masker.feed(text)

    def flush(self) -> str:
        return self.masker.flush()


async def restart_waiting(
    sessions: Mapping[str, "Session"], keys: list[str], harness: str
) -> list[str]:
    """Restart each waiting session that is still open and tell it to go on."""
    restarted = []
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for key in keys:
        s = sessions.get(key)
        if s is None or s.archived:
            continue
        try:
            await s.restart()
            await s.deliver(
                f"> from aegis:login · renewed · {stamp}",
                f"The {LABEL.get(harness, harness)} sign-in was renewed. Continue where you stopped.",
            )
            restarted.append(s.handle)
        except Exception as e:  # one session must not keep the others down
            log.warning("%s: restart after a new sign-in failed: %s", s.handle, e)
    return restarted


class SignIns:
    def __init__(
        self,
        sessions: Mapping[str, "Session"],
        terminals: "Terminals",
        recipes: Callable[[], dict[str, Recipe]],
        toast: Callable[[str, "Session"], object],
        checks: dict[str, Callable[[dict], Awaitable[str | None]]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessions = sessions
        self._terminals = terminals
        self._recipes = recipes
        self._toast = toast
        self._checks = checks
        self._clock = clock
        self.cards: dict[str, Card] = {}
        self._open: dict[str, str] = {}  # harness -> card id of its open card
        # harness -> log ids of the sessions waiting on its sign-in; a failed
        # card leaves them for the next one.
        self.waiting: dict[str, list[str]] = {}
        self._background: set[asyncio.Task] = set()

    def recipes(self) -> dict[str, Recipe]:
        return self._recipes()

    # -- what starts a card --------------------------------------------------
    def failed(self, s: "Session", harness: str = "claude-code") -> None:
        """A turn of ``s`` failed to sign in. Members' sessions sign in with
        their own account, which the server's must not replace."""
        if s.spec.linux_user is not None or harness not in self._recipes():
            return
        waiting = self.waiting.setdefault(harness, [])
        if s.log_id not in waiting:
            waiting.append(s.log_id)
        card = self._card_for(harness)
        if card is not None and card.holder in self._sessions:
            self._record(card)
            return
        card = self._new_card(harness, s)
        self._toast(
            f"{LABEL.get(harness, harness)} sign-in failed: {s.handle} needs a new sign-in",
            s,
        )
        self._spawn(self._run(card))

    async def start(self, harness: str, holder: "Session", started_by: str) -> str:
        """Open a card for ``harness`` without a failed turn (Settings, an
        agent's sign_in_start), or return the one already open."""
        if harness not in self._recipes():
            raise ValueError(f"no sign-in recipe for {harness}")
        card = self._card_for(harness)
        if card is not None and card.holder in self._sessions:
            return card.card_id
        card = self._new_card(harness, holder)
        self._spawn(self._run(card))
        return card.card_id

    # -- what the person does on the card --------------------------------------
    async def answer(self, card_id: str, value: str) -> None:
        card = self._live(card_id)
        if card.step not in ("paste", "device_code") or card.term is None:
            raise ValueError("this sign-in is not waiting for a code")
        card.term.input(value.strip())
        await asyncio.sleep(0.3)  # a code and its Enter in one write reads as a paste
        card.term.input("\r")
        self._set(card, "checking")

    async def choose(self, card_id: str, option: str) -> None:
        card = self._live(card_id)
        options = card.fields.get("options") or []
        if card.step != "choose" or option not in options or card.term is None:
            raise ValueError("this sign-in is not offering that choice")
        card.term.input(option + "\r")
        self._set(card, "checking")

    async def cancel(self, card_id: str) -> None:
        card = self._live(card_id)
        await self._fail(card, "cancelled")

    async def provide_token(self, holder: "Session", card_id: str, token: str) -> dict:
        """The card's paste-a-token field: a token the person got elsewhere.
        Raises ``ValueError`` with the person's words when it is refused."""
        card = self.cards.get(card_id)
        harness = card.harness if card else "claude-code"
        if why := await self._checks[harness]({"token": token.strip(), "pasted": True}):
            raise ValueError(why)
        if card is None:
            card = self._card_for(harness)
        return await self._done(card, harness, holder)

    def boot(self) -> None:
        """End the cards a server restart left open: their command is gone."""
        for s in list(self._sessions.values()):
            for e in s.entries():
                if e["kind"] == "sign_in" and e["detail"].get("step") in OPEN:
                    s.record_card(
                        {
                            **e["detail"],
                            "kind": "sign_in",
                            "step": "failed",
                            "error": "interrupted: start again",
                        }  # fmt: skip
                    )

    async def shutdown(self) -> None:
        for task in list(self._background):
            task.cancel()
        for card in list(self.cards.values()):
            if card.term is not None and card.term.id in self._terminals.open:
                await self._terminals.close(card.term.id)

    # -- the run -------------------------------------------------------------
    def _new_card(self, harness: str, holder: "Session") -> Card:
        cid = f"signin-{secrets.token_hex(4)}"
        now = self._clock()
        waiting = self.waiting.setdefault(harness, [])
        card = Card(
            cid, harness, holder.log_id, waiting, matched_at=now, started_at=now
        )
        card.fields["expires"] = time.time() + self._recipes()[harness].timeout_s
        self.cards[cid] = card
        self._open[harness] = cid
        self._record(card)
        return card

    async def _run(self, card: Card) -> None:
        recipe = self._recipes()[card.harness]
        card.screen = Screen(recipe.cols, ROWS)
        tap = Tap(
            card.screen, Masker(recipe.shapes), lambda: self._on_screen(card, recipe)
        )
        try:
            card.term = await self._terminals.spawn_ephemeral(
                f"sign-in-{card.harness}", recipe.cmd, recipe.env, recipe.unset,
                recipe.cols, self._owner(card), tap,
            )  # fmt: skip
        except Exception as e:
            await self._fail(card, f"could not start {recipe.cmd[0]}: {e}")
            return
        card.fields["term_id"] = card.term.id
        self._record(card)
        while card.step in OPEN:
            await asyncio.sleep(TICK_S)
            now = self._clock()
            if now - card.started_at > recipe.timeout_s:
                await self._fail(card, "timed out")
            elif card.step != "checking" and now - card.matched_at > UNKNOWN_SCREEN_S:
                await self._fail(card, "unknown screen")
            elif card.term.meta["state"] != "running" and card.step != "checking":
                await self._fail(card, f"{recipe.cmd[0]} exited")

    def _on_screen(self, card: Card, recipe: Recipe) -> None:
        if card.step not in OPEN or card.screen is None:
            return
        found = recipe.match(card.screen.text())
        if found is None:
            return
        rule, captures = found
        card.matched_at = self._clock()
        if rule.step == "failed":
            self._spawn(
                self._fail(card, "the sign-in was refused", self._tail(card, recipe))
            )
        elif rule.step == "checking" and "token" in captures:
            if card.fields.get("checking") is None:
                card.fields["checking"] = True
                self._spawn(self._check(card, captures["token"]))
        elif rule.step != card.step and card.step != "checking":
            card.fields.update({k: v for k, v in captures.items() if k != "token"})
            self._set(card, rule.step)

    async def _check(self, card: Card, token: str) -> None:
        self._set(card, "checking")
        why = await self._checks[card.harness]({"token": token})
        if why:
            await self._fail(card, why)
            return
        holder = self._sessions.get(card.holder)
        await self._done(card, card.harness, holder)

    async def _done(
        self, card: Card | None, harness: str, holder: "Session | None"
    ) -> dict:
        waiting, self.waiting[harness] = self.waiting.get(harness, []), []
        restarted = await restart_waiting(self._sessions, waiting, harness)
        if card is not None:
            card.waiting = []
            self._set(card, "done")
            self._close(card)
            if holder is not None:
                holder.record_card(
                    {
                        "kind": "sign_in_done",
                        "card_id": card.card_id,
                        "restarted": restarted,
                    }
                )
        return {"restarted": restarted}

    async def _fail(self, card: Card, error: str, tail: str | None = None) -> None:
        if card.step not in OPEN:
            return
        card.fields["error"] = error
        if tail:
            card.fields["tail"] = tail
        self._set(card, "failed")
        self._close(card)

    # -- helpers ---------------------------------------------------------------
    def _owner(self, card: Card) -> str:
        """Who may open the terminal: the owner of the session holding the card."""
        holder = self._sessions.get(card.holder)
        return (getattr(holder.spec, "owner", None) if holder else None) or "aegis"

    def _card_for(self, harness: str) -> Card | None:
        cid = self._open.get(harness)
        return self.cards.get(cid) if cid else None

    def _live(self, card_id: str) -> Card:
        card = self.cards.get(card_id)
        if card is None or card.step not in OPEN:
            raise ValueError("this sign-in is over; start again")
        return card

    def _close(self, card: Card) -> None:
        """Stop the command; the card stays, the harness may open a new one.
        Sessions still waiting join the next card."""
        if self._open.get(card.harness) == card.card_id:
            del self._open[card.harness]
        if card.term is not None and card.term.id in self._terminals.open:
            self._spawn(self._terminals.close(card.term.id))

    def _tail(self, card: Card, recipe: Recipe) -> str:
        rows = (
            [r for r in card.screen.rows() if r.strip()][-TAIL_ROWS:]
            if card.screen
            else []
        )
        m = Masker(recipe.shapes)
        return (m.feed("\n".join(rows)) + m.flush()).strip()

    def _set(self, card: Card, step: str) -> None:
        card.step = step
        self._record(card)

    def _record(self, card: Card) -> None:
        holder = self._sessions.get(card.holder)
        if holder is None:
            return
        rec = {
            "kind": "sign_in",
            "card_id": card.card_id,
            "harness": card.harness,
            "label": LABEL.get(card.harness, card.harness),
            "step": card.step,
            "handles": [
                self._sessions[k].handle for k in card.waiting if k in self._sessions
            ],
        }
        keep = (
            "url",
            "code",
            "prompt",
            "options",
            "error",
            "tail",
            "term_id",
            "expires",
        )
        rec |= {k: v for k, v in card.fields.items() if k in keep}
        holder.record_card(rec)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
