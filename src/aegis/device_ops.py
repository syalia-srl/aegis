"""The operations on paired phones (devices.py): device.list and device.call
for agents, device.remove for the person.

A phone is its owner's. An agent reaches only the phones of its session's
owner, and a phone of anyone else is answered as unknown, never as someone
else's, so a name cannot be probed."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .devices import Devices
from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App

TIMEOUT_MAX = 600


class DeviceCall(BaseModel):
    model_config = {"extra": "forbid"}
    op: str = Field(
        min_length=1,
        max_length=80,
        description="The operation, such as device.info, sensor.list, sensor.read, compass.read.",
    )
    args: dict = Field(default_factory=dict, description="The operation's arguments.")
    device: str | None = Field(
        default=None,
        description="The device's name or id; may be left out when the owner has one.",
    )
    timeout: int = Field(
        default=120, ge=1, le=TIMEOUT_MAX, description="Seconds to wait for the phone."
    )


class DeviceRef(BaseModel):
    model_config = {"extra": "forbid"}
    id: str


def register_device_ops(app: App) -> Devices:
    r = app.registry
    devices = Devices(app.roots.state_root / "devices.json")

    def owner(caller: Caller) -> str:
        return app.who(caller).name

    def pick(caller: Caller, name: str | None) -> dict:
        mine = devices.of_owner(owner(caller))
        if name is None:
            if len(mine) == 1:
                return mine[0]
            if not mine:
                raise OpError(
                    "no_device",
                    "no phone is paired; the person pairs one in the aegis Android app, "
                    "Settings → Device",
                )
            raise OpError(
                "which_device",
                "several phones are paired; name one: "
                + ", ".join(d["name"] for d in mine),
            )
        for d in mine:
            if name in (d["id"], d["name"]):
                return d
        raise OpError("unknown_device", f"no paired phone named {name!r}")

    @r.op("device.list", agent=True, read=True, access="read")
    async def device_list(_, caller):
        """The phones paired to your owner: name, online, Android API level,
        app version and the operations each one runs (help: devices)."""
        return [
            {
                "id": d["id"],
                "name": d["name"],
                "online": d["online"],
                "model": d["model"],
                "api": d["api"],
                "shell": d["shell"],
                "ops": d["ops"],
            }
            for d in devices.of_owner(owner(caller))
        ]

    @r.op("device.call", DeviceCall, agent=True)
    async def device_call(p: DeviceCall, caller):
        """Run an operation on a paired phone and get its result: read sensors
        (sensor.list, sensor.read, compass.read) or device.info. The phone may
        ask the person first, and answers denied when they refuse. help_read
        devices lists each operation and its arguments."""
        d = pick(caller, p.device)
        if d["ops"] and p.op not in d["ops"]:
            raise OpError(
                "needs_newer_app",
                f"{d['name']} does not run {p.op}; it runs " + ", ".join(d["ops"]),
            )
        session = ""
        if caller.log_id:
            s = app.sessions.sessions.get(caller.log_id)
            session = s.handle if s is not None else caller.log_id
        return await devices.call(d["id"], session, p.op, p.args, p.timeout)

    @r.op("device.mine", access="read")
    async def device_mine(_, caller):
        """The person's paired phones, for Settings → Device."""
        return devices.of_owner(owner(caller))

    @r.op("device.remove", DeviceRef)
    async def device_remove(p: DeviceRef, caller):
        """Unpair a phone: its key stops working and its socket closes."""
        if caller.is_agent:
            raise OpError("people_only", "only the person unpairs a phone")
        row = devices.get(p.id)
        if row is None or row["owner"] != owner(caller):
            raise OpError("unknown_device", f"no paired phone {p.id}")
        devices.remove(p.id)

    return devices
