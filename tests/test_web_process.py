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


async def test_multiple_marked_commands_are_refused_to_the_model_without_execution(
    tmp_path,
):
    class AmbiguousBrowser(Browser):
        async def __call__(self, op, args):
            result = await super().__call__(op, args)
            if op == "send" and args["text"] == "Request two commands":
                self.messages[-1]["markdown"] += (
                    "\n```bash\n# aegis: execute\ntouch forbidden-two\n```"
                )
            if op == "send" and args["text"].startswith("Aegis protocol refusal"):
                self.messages[-1]["markdown"] = "I will request one command at a time."
            return result

    browser = AmbiguousBrowser("touch forbidden-one")
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("Request two commands")
        await until(lambda: bool(done(events)), timeout=5)
        assert not (tmp_path / "forbidden-one").exists()
        assert not (tmp_path / "forbidden-two").exists()
        assert any(text.startswith("Aegis protocol refusal") for text in browser.sent)
        assert done(events)[-1]["error"] is False
    finally:
        await process.terminate()


async def test_manual_chrome_conversation_is_imported_before_the_next_aegis_task(
    tmp_path,
):
    browser = Browser("printf 'first\\n'")
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("First Aegis task")
        await until(lambda: len(done(events)) == 1, timeout=5)
        browser.users.append(
            {"id": "manual-user", "text": "Research the alternative in Chrome"}
        )
        browser.messages.append(
            {
                "id": "manual-answer",
                "markdown": "A manually requested proposal",
                "complete": True,
            }
        )
        await process.send("Use that proposal")
        await until(lambda: len(done(events)) == 2, timeout=5)
        manuals = [e for e in events if e["type"] == "manual"]
        assert [(e["id"], e["text"]) for e in manuals] == [
            ("manual-user", "Research the alternative in Chrome")
        ]
        assert (
            sum(e.get("text") == "A manually requested proposal" for e in events) == 1
        )
        assert process.ledger.data["observed"]["manual-answer"]
        assert browser.sent.count("Use that proposal") == 1
    finally:
        await process.terminate()


async def test_manual_marked_request_completes_before_queued_aegis_input(tmp_path):
    browser = Browser("printf 'ordinary\\n'")
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("First task")
        await until(lambda: len(done(events)) == 1, timeout=5)
        browser.users.append({"id": "manual-user", "text": "Write the manual proof"})
        browser.messages.append(
            {
                "id": "manual-answer",
                "markdown": "```bash\n# aegis: execute\ntouch manual-proof\n```",
                "complete": True,
            }
        )
        await process.send("The queued task")
        await until(lambda: len(done(events)) == 2, timeout=5)
        assert (tmp_path / "manual-proof").exists()
        assert browser.sent.count("The queued task") == 1
        assert browser.sent.index("The queued task") > next(
            i
            for i, text in enumerate(browser.sent)
            if "execution manual-answer/0" in text
        )
        assert len([e for e in events if e["type"] == "tool_call"]) == 3
    finally:
        await process.terminate()


async def test_replaced_source_history_stops_before_another_submission(tmp_path):
    browser = Browser()
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("First task")
        await until(lambda: len(done(events)) == 1, timeout=5)
        before = list(browser.sent)
        browser.messages = [
            {
                "id": "branched-answer",
                "markdown": "```bash\n# aegis: execute\ntouch forbidden-branch\n```",
                "complete": True,
            }
        ]
        await process.send("Continue the same conversation")
        await until(lambda: len(done(events)) == 2, timeout=5)
        assert done(events)[-1]["error"] is True
        assert browser.sent == before
        assert not (tmp_path / "forbidden-branch").exists()
    finally:
        await process.terminate()


async def test_changed_permissions_are_conveyed_without_repriming(tmp_path):
    browser = Browser("printf 'allowed\\n'")
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("First task")
        await until(lambda: len(done(events)) == 1, timeout=5)
        await process.set("permission", "read")
        await process.send("Second task")
        await until(lambda: len(done(events)) == 2, timeout=5)
        updates = [
            text for text in browser.sent if text.startswith("Aegis session update")
        ]
        assert len(updates) == 1 and "Permission: read" in updates[0]
        assert (
            sum(
                text.startswith(
                    "You are an Aegis agent connected through a browser bridge."
                )
                for text in browser.sent
            )
            == 1
        )
        assert browser.sent.index(updates[0]) < browser.sent.index("Second task")
    finally:
        await process.terminate()


@pytest.mark.parametrize("stop", ["deadline", "permission"])
async def test_background_descendant_cannot_outlive_the_command_boundary(
    tmp_path, monkeypatch, stop
):
    import asyncio

    module = importlib.import_module("aegis.web_agent.process")
    monkeypatch.setattr(module, "COMMAND_TIMEOUT_S", 0.03 if stop == "deadline" else 5)
    browser = Browser("(sleep 0.2; touch descendant-survived) &")
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("Start a background descendant")
        if stop == "permission":
            await until(
                lambda: (
                    process._child is not None and process._child.returncode is not None
                ),
                timeout=5,
            )
            await process.set("permission", "read")
        await until(lambda: bool(done(events)), timeout=5)
        await asyncio.sleep(0.25)
        assert not (tmp_path / "descendant-survived").exists()
    finally:
        await process.terminate()


async def test_return_from_manual_control_revalidates_the_captured_command(
    tmp_path, monkeypatch
):
    from aegis.chrome import CallError

    module = importlib.import_module("aegis.web_agent.process")
    monkeypatch.setattr(module, "POLL_S", 0.01)

    class ChangedBrowser(Browser):
        changed = False

        async def __call__(self, op, args):
            if (
                op == "check"
                and not self.changed
                and self.messages
                and "# aegis: execute" in self.messages[-1]["markdown"]
            ):
                self.changed = True
                self.messages[-1]["markdown"] = (
                    "The person cancelled that command in Chrome."
                )
                raise CallError("person_has_control", "Paused for the person")
            return await super().__call__(op, args)

    browser = ChangedBrowser("touch stale-command-ran")
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("A command that is cancelled during handover")
        await until(lambda: bool(done(events)), timeout=5)
        assert not (tmp_path / "stale-command-ran").exists()
        assert done(events)[-1]["error"] is True
    finally:
        await process.terminate()


async def test_unrequested_regeneration_is_not_a_new_manual_command(tmp_path):
    browser = Browser()
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("First task")
        await until(lambda: len(done(events)) == 1, timeout=5)
        browser.messages.append(
            {
                "id": "regenerated",
                "markdown": "```bash\n# aegis: execute\ntouch forbidden-regeneration\n```",
                "complete": True,
            }
        )
        await process.send("Continue")
        await until(lambda: len(done(events)) == 2, timeout=5)
        assert done(events)[-1]["error"] is True
        assert not (tmp_path / "forbidden-regeneration").exists()
    finally:
        await process.terminate()


async def test_redirected_background_children_end_with_the_command(tmp_path):
    import asyncio

    browser = Browser(
        "(sleep 0.3; touch redirected-descendant-survived) >/dev/null 2>&1 &"
    )
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("Start a redirected descendant")
        await until(lambda: bool(done(events)), timeout=5)
        await process.set("permission", "read")
        await asyncio.sleep(0.4)
        assert not (tmp_path / "redirected-descendant-survived").exists()
    finally:
        await process.terminate()


async def test_permission_update_follows_the_pending_manual_command_result(tmp_path):
    browser = Browser()
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("First task")
        await until(lambda: len(done(events)) == 1, timeout=5)
        browser.users.append({"id": "manual-user", "text": "Write the manual proof"})
        browser.messages.append(
            {
                "id": "manual-answer",
                "markdown": "```bash\n# aegis: execute\ntouch forbidden-manual-proof\n```",
                "complete": True,
            }
        )
        await process.set("permission", "read")
        await process.send("The queued task")
        await until(lambda: len(done(events)) == 2, timeout=5)
        assert done(events)[-1]["error"] is False
        assert not (tmp_path / "forbidden-manual-proof").exists()
        result = next(
            i
            for i, text in enumerate(browser.sent)
            if "execution manual-answer/0" in text
        )
        update = next(
            i
            for i, text in enumerate(browser.sent)
            if text.startswith("Aegis session update")
        )
        assert result < update < browser.sent.index("The queued task")
        assert process.ledger.get("manual-answer/0")["phase"] == "delivered"
    finally:
        await process.terminate()


async def test_web_priming_is_compact_and_loads_workspace_context_only_on_demand(
    tmp_path,
):
    from dataclasses import replace

    (tmp_path / "AGENTS.md").write_text("WORKSPACE_SENTINEL " * 3000)
    (tmp_path / "CLAUDE.md").write_text("ANCESTOR_SENTINEL " * 3000)
    browser = Browser("printf 'compact works'")
    process, events = make(tmp_path, browser)
    process.context = replace(
        process.context, system_prompt="DEFAULT_PRIMER_SENTINEL " * 3000
    )
    await process.start()
    try:
        await process.send("Research the actual requested topic")
        await until(lambda: bool(done(events)), timeout=5)
        brief = browser.sent[0]
        assert len(brief) < 2000
        assert not any(
            s in brief
            for s in (
                "WORKSPACE_SENTINEL",
                "ANCESTOR_SENTINEL",
                "DEFAULT_PRIMER_SENTINEL",
            )
        )
        assert "# aegis: execute" in brief
        assert "aegis tools" in brief and "aegis call" in brief
        assert str(tmp_path) in brief and "Permission: full" in brief
        assert browser.sent[1] == "Research the actual requested topic"
        assert "stdout:\ncompact works" in browser.results[0]
    finally:
        await process.terminate()


async def test_chatgpt_pins_the_canonical_chat_after_its_provisional_route(tmp_path):
    class ChatGPT(Browser):
        provisional_observed = False

        async def __call__(self, op, args):
            page = await super().__call__(op, args)
            if op == "start":
                page["url"] = "https://chatgpt.com/"
            if op == "observe":
                if len(self.sent) == 1 and not self.provisional_observed:
                    self.provisional_observed = True
                    page["url"] = "https://chatgpt.com/c/local-chatgpt%3Atest"
                else:
                    page["url"] = (
                        "https://chatgpt.com/c/canonical-chat"
                        if self.sent
                        else "https://chatgpt.com/"
                    )
            return page

    browser = ChatGPT("printf 'verified' > canonical-proof")
    process, events = make(tmp_path, browser)
    process.provider = importlib.import_module("aegis.web_agent.providers").PROVIDERS[
        "web-chatgpt"
    ]
    await process.start()
    try:
        await process.send("Verify this conversation")
        await until(lambda: bool(done(events)), timeout=5)
        assert done(events)[-1]["error"] is False
        assert browser.provisional_observed is True
        assert (tmp_path / "canonical-proof").read_text() == "verified"
        assert (
            process.ledger.data["conversation"]["url"]
            == "https://chatgpt.com/c/canonical-chat"
        )
    finally:
        await process.terminate()


async def test_partial_history_allows_eviction_but_refuses_changed_visible_answers(
    tmp_path,
):
    class VirtualBrowser(Browser):
        async def __call__(self, op, args):
            result = await super().__call__(op, args)
            if op == "observe":
                result["partial_history"] = True
            return result

    browser = VirtualBrowser()
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("Verify virtual history")
        await until(lambda: bool(done(events)), timeout=5)
        browser.messages = browser.messages[-1:]
        page = await process._observe()
        assert page["messages"][0]["markdown"] == browser.final
        browser.messages[0]["markdown"] = "A replacement answer"
        with pytest.raises(RuntimeError, match="already observed answer changed"):
            await process._observe()
    finally:
        await process.terminate()


async def test_initial_blank_document_waits_before_priming(tmp_path):
    class LoadingBrowser(Browser):
        blank = True

        async def __call__(self, op, args):
            result = await super().__call__(op, args)
            if op == "observe" and self.blank:
                self.blank = False
                result["url"] = "about:blank"
            return result

    browser = LoadingBrowser()
    process, events = make(tmp_path, browser)
    await process.start()
    try:
        await process.send("Wait for this provider to load")
        await until(lambda: bool(done(events)), timeout=5)
        assert done(events)[-1]["error"] is False
        assert "AEGIS_READY" in browser.sent[0]
    finally:
        await process.terminate()
