"""Fixtures for tests that once ran in multi-user mode (#958 removed it): a
world with the server's one person, whom ``BEN`` and ``ADMIN`` both name."""

import pytest

from aegis import launch
from aegis.ops import Caller, OpError

from .test_agents import CONFIG, World

BEN = ADMIN = Caller("user")


class Recorded(list):
    cwds: list

    def harness(self, bin: str) -> list:
        return [argv for s, argv in self if argv[0] == bin and "--version" not in argv]


@pytest.fixture
def recorded(monkeypatch):
    """Every start's shape and argv, started for real."""
    seen = Recorded()
    seen.cwds = []

    def spy(shape, real):
        if shape in ("child", "pty"):

            async def f(argv, **kw):
                seen.append((shape, list(argv)))
                seen.cwds.append(str(kw.get("cwd")))
                return await real(argv, **kw)

        else:

            def f(argv, **kw):
                seen.append((shape, list(argv)))
                return real(argv, **kw)

        return f

    for shape in ("child", "pty", "run"):
        monkeypatch.setattr(launch, shape, spy(shape, getattr(launch, shape)))
    return seen


@pytest.fixture
def homes():
    return {}


@pytest.fixture
def ben_home(tmp_path):
    return tmp_path


async def mu_world(tmp_path, fake_claude, monkeypatch, text: str = CONFIG):
    (tmp_path / ".aegis.yaml").write_text(text)
    return await World(tmp_path, fake_claude).start()


@pytest.fixture
async def world(tmp_path, fake_claude, monkeypatch, recorded):
    w = await mu_world(tmp_path, fake_claude, monkeypatch)
    yield w
    await w.stop()


async def call(world, op: str, params: dict, caller: Caller):
    return await world.app.registry.call(op, params, caller)


async def refused(world, op: str, params: dict, caller: Caller) -> OpError:
    with pytest.raises(OpError) as e:
        await call(world, op, params, caller)
    return e.value


async def spawn(world, caller: Caller) -> str:
    return (await call(world, "session.spawn", {"agent": "opus"}, caller))["log_id"]
