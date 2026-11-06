"""aegis 3.0 removed the multi-user mode (#958): a state directory that still
holds its tokens refuses to start rather than run with them ignored."""

import pytest

from aegis.ops import Principal
from aegis.tokens import OldStateError, TokenStore, refuse_old_state


def test_a_state_with_member_tokens_refuses_to_start(tmp_path):
    (tmp_path / "tokens.json").write_text(
        '{"tokens": [{"name": "ben", "role": "member", "sha256": "x"}]}'
    )
    with pytest.raises(OldStateError) as e:
        refuse_old_state(tmp_path)
    assert "tokens.json" in str(e.value) and "multi-user" in str(e.value)


def test_an_empty_tokens_file_or_none_boots(tmp_path):
    refuse_old_state(tmp_path)
    (tmp_path / "tokens.json").write_text("{}")
    refuse_old_state(tmp_path)
    (tmp_path / "tokens.json").write_text('{"tokens": []}')
    refuse_old_state(tmp_path)


def test_only_the_admin_token_resolves(tmp_path):
    store = TokenStore(tmp_path, owner="ana")
    assert store.resolve(store.admin_token()) == Principal("ana")
    assert store.resolve("not-the-token") is None
    assert store.resolve(None) is None


def test_prepare_refuses_old_state_with_the_message(tmp_path):
    from aegis.server import StartError, prepare

    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    state = tmp_path / ".aegis" / "state"
    state.mkdir(parents=True)
    (state / "tokens.json").write_text('{"tokens": [{"name": "ben"}]}')
    with pytest.raises(StartError) as e:
        prepare(tmp_path, tmp_path)
    assert "multi-user" in str(e.value)
