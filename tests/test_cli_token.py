import getpass

from typer.testing import CliRunner

from aegis.cli import app


def run(root, *args):
    return CliRunner().invoke(
        app, ["token", *args, "--root", str(root)], env={"COLUMNS": "200"}
    )


def test_revoking_the_admin_prints_its_new_token(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    state = tmp_path / ".aegis" / "state"
    state.mkdir(parents=True)
    (state / "token").write_text("old-token\n")
    out = run(tmp_path, "revoke", getpass.getuser())
    assert out.exit_code == 0, out.output
    new = out.stdout.strip()
    assert new and new != "old-token"
    assert (state / "token").read_text().strip() == new


def test_add_and_list_are_gone(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    assert run(tmp_path, "add", "ben").exit_code != 0
    assert run(tmp_path, "list").exit_code != 0


def test_revoking_another_name_is_refused(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    out = run(tmp_path, "revoke", "ben")
    assert out.exit_code == 1 and "no token named ben" in out.output
