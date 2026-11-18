"""The code API acting as a host's caller (#959)."""

from __future__ import annotations

from aegis import Server

from .policies import Named, Who


async def test_as_who_acts_as_that_person(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: opus\nagents:\n"
        "  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    )
    policy = Named(hidden={"ben": {"ana"}})
    async with Server(tmp_path, policy=policy, claude=fake_claude) as srv:
        s = await srv.as_who(Who("ana")).sessions.spawn()
        assert srv.app.owner_of(s.log_id) == "ana"
        bens = [x.log_id for x in await srv.as_who(Who("ben")).sessions.list()]
        assert s.log_id not in bens
        assert s.log_id in [x.log_id for x in await srv.sessions.list()]


async def test_token_for_mints_a_token_the_server_reads_back(tmp_path, fake_claude):
    async with Server(tmp_path, policy=Named(), claude=fake_claude) as srv:
        token = srv.token_for(Who("ana"), ttl=60)
        from aegis.policy import Credential

        p = await srv.app.identify(Credential(token, "browser"))
        assert p.name == "ana"
