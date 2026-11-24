"""The browser is external; commands, persistence and lifecycle are real."""

import copy
import importlib
import json

import pytest

from aegis.harness import Launch

from .conftest import until


class Browser:
    def __init__(self, command="printf '437\\n'", *, final="ROUND_TRIP_OK 437"):
        self.command, self.final = command, final
        self.messages = []
        self.users = []
        self.sent = []
        self.results = []
        self.states = []
        self.hold_results = False
        self.intervention = None

    async def __call__(self, op, args):
        if op == "start":
            return {
                "tab": "test-web",
                "url": "https://chat.qwen.ai/",
                "device": "test-browser",
            }
        if op == "state":
            self.states.append(args)
            return {}
        if op in ("check", "release"):
            return {}
        if op == "stop":
            return {"stopped": True}
        if op == "observe":
            return {
                "url": "https://chat.qwen.ai/c/test",
                "title": "Qwen",
                "messages": copy.deepcopy(self.messages),
                "users": copy.deepcopy(self.users),
                "busy": False,
                "intervention": self.intervention,
            }
        assert op == "send"
        text = args["text"]
        self.sent.append(text)
        self.users.append({"id": f"user-{len(self.users)}", "text": text})
        if "AEGIS_READY" in text:
            answer = "AEGIS_READY"
        elif text.startswith("Aegis Bash result"):
            self.results.append(text)
            if self.hold_results:
                return {"submitted": True}
            answer = self.final
        else:
            answer = "```bash\n# aegis: execute\n" + self.command + "\n```"
        self.messages.append(
            {"id": f"answer-{len(self.messages)}", "markdown": answer, "complete": True}
        )
        return {"submitted": True}


def make(tmp_path, browser, *, permission="full", **extra):
    events = []
    launch = Launch(
        cwd=tmp_path,
        model="web",
        effort="",
        permission=permission,
        resume_id=None,
        mcp=None,
        system_prompt="You are a research sidekick.",
        stderr_path=tmp_path / "state" / "stderr" / "web-test.log",
        on_line=lambda line: events.append(json.loads(line)),
        on_exit=lambda *_: None,
        log_id="web-test",
        web=browser,
        **extra,
    )
    module = importlib.import_module("aegis.web_agent.process")
    providers = importlib.import_module("aegis.web_agent.providers")
    return module.WebProcess(launch, providers.PROVIDERS["web-qwen"]), events


def done(events):
    return [e for e in events if e["type"] == "result"]


async def test_a_marked_request_runs_bash_and_returns_the_actual_output(tmp_path):
    browser = Browser("printf '437\\n' > result.txt; cat result.txt")
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("Verify the arithmetic")
        await until(lambda: bool(done(events)), timeout=5)
        assert (tmp_path / "result.txt").read_text() == "437\n"
        assert "stdout:\n437\n" in browser.results[0]
        assert "exit_code: 0" in browser.results[0]
        assert process.ledger.data["messages"]["answer-1"]["markdown"].startswith(
            "```bash"
        )
        assert process.ledger.data["prime_hash"]
        assert done(events)[-1]["error"] is False
        assert sum(e["type"] == "tool_call" for e in events) == 1
        assert any(
            e["type"] == "text" and e["text"] == "ROUND_TRIP_OK 437" for e in events
        )
    finally:
        await process.terminate()


@pytest.mark.parametrize("permission", ["read", "write"])
async def test_restrictive_permissions_refuse_bash_without_running_it(
    tmp_path, permission
):
    browser = Browser("touch forbidden.txt")
    process, events = make(tmp_path, browser, permission=permission)
    await process.start()
    try:
        await process.send("Try the command")
        await until(lambda: bool(done(events)), timeout=5)
        assert not (tmp_path / "forbidden.txt").exists()
        assert "does not allow" in browser.results[0]
        assert "exit_code: 126" in browser.results[0]
    finally:
        await process.terminate()


async def test_priming_does_not_execute_code_in_its_acknowledgement(tmp_path):
    browser = Browser()
    original = browser.__class__.__call__

    class BadPrime(Browser):
        async def __call__(self, op, args):
            result = await original(self, op, args)
            if op == "send" and "AEGIS_READY" in args["text"]:
                self.messages[-1]["markdown"] = (
                    "```bash\n# aegis: execute\ntouch forbidden.txt\n```"
                )
            return result

    browser = BadPrime()
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("Check the context")
        await until(lambda: bool(done(events)), timeout=5)
        assert not (tmp_path / "forbidden.txt").exists()
        assert sum(e["type"] == "tool_call" for e in events) == 1
    finally:
        await process.terminate()


async def test_interrupt_kills_the_real_process_group_and_prevents_late_work(tmp_path):
    browser = Browser("touch started; sleep 30; touch forbidden.txt")
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("Start the long command")
        await until(lambda: (tmp_path / "started").exists(), timeout=5)
        await process.interrupt()
        assert process.pid is None
        assert not (tmp_path / "forbidden.txt").exists()
        assert done(events)[-1]["stop_reason"] == "interrupted"
        assert process.ledger.uncertain() == []
    finally:
        await process.terminate()


async def test_completed_execution_is_not_repeated_when_the_driver_resumes(tmp_path):
    browser = Browser("printf x >> executions.txt")
    browser.hold_results = True
    process, events = make(tmp_path, browser)
    await process.start()
    await process.send("Run once")
    await until(lambda: bool(browser.results), timeout=5)
    await process.terminate()
    browser.messages.append(
        {"id": "answer-after-result", "markdown": "Finished", "complete": True}
    )
    browser.hold_results = False
    resumed, events = make(tmp_path, browser)
    await resumed.start()
    try:
        await resumed.send("Continue after restart")
        await until(lambda: bool(done(events)), timeout=5)
        assert (tmp_path / "executions.txt").read_text() == "x"
        assert len(browser.results) == 1
        assert sum("AEGIS_READY" in text for text in browser.sent) == 1
    finally:
        await resumed.terminate()


async def test_a_started_execution_without_a_result_requires_human_recovery(tmp_path):
    browser = Browser("touch forbidden.txt")
    process, events = make(tmp_path, browser)
    process.ledger.prepare("answer-1/0", "touch forbidden.txt")
    process.ledger.start("answer-1/0")
    await process.start()
    try:
        await process.send("Resume")
        await until(lambda: bool(done(events)), timeout=5)
        assert done(events)[-1]["error"] is True
        assert "uncertain" in done(events)[-1]["text"].lower()
        assert not (tmp_path / "forbidden.txt").exists()
    finally:
        await process.terminate()


async def test_reducing_permission_stops_a_running_command(tmp_path):
    browser = Browser("touch started; sleep 30; touch forbidden.txt")
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("Start work")
        await until(lambda: (tmp_path / "started").exists(), timeout=5)
        await process.set("permission", "read")
        assert process.pid is None
        assert not (tmp_path / "forbidden.txt").exists()
    finally:
        await process.terminate()


async def test_disabling_harness_tools_prevents_free_form_bash(tmp_path):
    browser = Browser("touch forbidden.txt")
    process, events = make(tmp_path, browser, harness_tools=False)
    await process.start()
    try:
        await process.send("Check the restrictions")
        await until(lambda: bool(done(events)), timeout=5)
        assert not (tmp_path / "forbidden.txt").exists()
        assert "disabled" in browser.results[0]
    finally:
        await process.terminate()


async def test_the_session_token_is_not_exposed_in_the_shell_environment_or_results(
    tmp_path,
):
    browser = Browser(
        'printf "token-env:%s\\n" "$AEGIS_SESSION_TOKEN"; cat "$AEGIS_CALL_AUTH"'
    )
    events = []
    launch = Launch(
        cwd=tmp_path,
        model="web",
        effort="",
        permission="full",
        resume_id=None,
        mcp=("http://127.0.0.1:1/mcp", "example-session-credential"),
        system_prompt="Research",
        stderr_path=tmp_path / "state" / "stderr" / "web-test.log",
        on_line=lambda line: events.append(json.loads(line)),
        on_exit=lambda *_: None,
        log_id="web-test",
        web=browser,
    )
    cls = importlib.import_module("aegis.web_agent.process").WebProcess
    provider = importlib.import_module("aegis.web_agent.providers").PROVIDERS[
        "web-qwen"
    ]
    process = cls(launch, provider)
    await process.start()
    try:
        await process.send("Check credential handling")
        await until(lambda: bool(done(events)), timeout=5)
        assert "token-env:\n" in browser.results[0]
        assert "example-session-credential" not in browser.results[0]
        assert "example-session-credential" not in json.dumps(events)
        for output in (tmp_path / "state" / "web").rglob("*.stdout"):
            assert "example-session-credential" not in output.read_text()
    finally:
        await process.terminate()


async def test_human_verification_pauses_before_any_browser_submission(
    tmp_path, monkeypatch
):
    module = importlib.import_module("aegis.web_agent.process")
    monkeypatch.setattr(module, "POLL_S", 0.01)
    browser = Browser("touch allowed-after-confirmation")
    browser.intervention = "Confirm your age to continue"
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("Wait for the person")
        await until(lambda: any(s["state"] == "needs_you" for s in browser.states))
        assert browser.sent == []
        browser.intervention = None
        await until(lambda: bool(done(events)), timeout=5)
        assert (tmp_path / "allowed-after-confirmation").exists()
    finally:
        await process.terminate()


async def test_command_timeout_kills_the_child_and_returns_a_real_failure(
    tmp_path, monkeypatch
):
    module = importlib.import_module("aegis.web_agent.process")
    monkeypatch.setattr(module, "COMMAND_TIMEOUT_S", 0.05)
    browser = Browser("sleep 30; touch forbidden.txt")
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("Run within the deadline")
        await until(lambda: bool(done(events)), timeout=5)
        assert "exit_code: 124" in browser.results[0]
        assert process.pid is None
        assert not (tmp_path / "forbidden.txt").exists()
    finally:
        await process.terminate()


async def test_large_output_is_saved_but_the_provider_receives_a_capped_preview(
    tmp_path,
):
    browser = Browser("head -c 70000 /dev/zero | tr '\\0' x")
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("Generate a bounded preview")
        await until(lambda: bool(done(events)), timeout=5)
        assert "Truncated at 65536 bytes" in browser.results[0]
        output = next((tmp_path / "state" / "web").rglob("*.stdout"))
        assert output.stat().st_size == 70000
        assert len(browser.results[0]) < 67000
    finally:
        await process.terminate()
