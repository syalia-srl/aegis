"""Small host policies the #959 tests share."""

from __future__ import annotations

from dataclasses import dataclass, field

from aegis.policy import Credential, HostPolicy, Refused


@dataclass(frozen=True)
class Who:
    name: str


@dataclass
class Named(HostPolicy):
    """People named by raw tokens ``tok-<name>``, keyed by name. ``hidden``
    maps a reader's name to the owners whose sessions they cannot see;
    ``gone`` names people whose restore is refused."""

    hidden: dict[str, set[str]] = field(default_factory=dict)
    gone: set[str] = field(default_factory=set)
    read_only: dict[str, set[str]] = field(default_factory=dict)

    async def identify(self, cred: Credential):
        if cred.token.startswith("tok-"):
            return Who(cred.token.removeprefix("tok-"))
        return await super().identify(cred)

    def key(self, who) -> str:
        return who.name if isinstance(who, Who) else super().key(who)

    async def restore(self, key: str):
        if key in self.gone:
            raise Refused(f"{key} was deleted")
        return Who(key)

    def can(self, who, action, session) -> bool:
        name = who.name if isinstance(who, Who) else None
        if name is None or session is None:
            return True
        if session.owner in self.hidden.get(name, set()):
            return False
        if action == "write" and session.owner in self.read_only.get(name, set()):
            return False
        return True


class RefuseAll(HostPolicy):
    async def identify(self, cred: Credential):
        raise Refused("nobody here")

    def can(self, who, action, session) -> bool:
        return False

    async def spawn(self, who, req):
        raise Refused("agents are off")
