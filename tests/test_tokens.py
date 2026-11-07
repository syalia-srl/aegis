import os
import stat

import pytest

from aegis.ops import OpError, Principal
from aegis.tokens import TokenStore


@pytest.fixture
def store(tmp_path):
    return TokenStore(tmp_path, owner="ana")


def test_the_admin_token_is_the_owners_and_private(store, tmp_path):
    token = (tmp_path / "token").read_text().strip()
    assert store.resolve(token) == Principal("ana")
    assert store.resolve("nope") is None
    assert stat.S_IMODE(os.stat(tmp_path / "token").st_mode) == 0o600


def test_revoking_the_admin_rotates_it(store, tmp_path):
    old = store.admin_token()
    new = store.revoke("ana")
    assert new and new != old
    assert store.resolve(old) is None
    assert store.resolve(new) == Principal("ana")
    assert (tmp_path / "token").read_text().strip() == new


def test_revoking_anyone_else_is_refused(store):
    with pytest.raises(OpError) as e:
        store.revoke("ben")
    assert e.value.code == "unknown_token"


def test_a_hand_rotated_token_file_applies_at_the_next_call(store, tmp_path):
    (tmp_path / "token").write_text("fresh-token\n")
    os.utime(tmp_path / "token", ns=(1, 1))
    assert store.resolve("fresh-token") == Principal("ana")
