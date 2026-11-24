"""Provider extraction runs in a real DOM, not a mock of the extractor."""

import importlib

import pytest

from .conftest import browser_slot

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture(scope="module")
def page():
    from playwright.sync_api import sync_playwright

    with browser_slot(), sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser.new_page()
        browser.close()


def provider(name):
    return importlib.import_module("aegis.web_agent.providers").PROVIDERS[name]


PAGES = {
    "web-gemini": '<model-response data-message-id="answer-1"><div class="response-content">CONTENT</div><button aria-label="Good response">Good</button></model-response>',
    "web-deepseek": '<div data-message-id="answer-1"><div class="ds-markdown">CONTENT</div><button aria-label="Copy">Copy</button></div>',
    "web-qwen": '<div class="chat-response-message" id="answer-1"><div class="qwen-chat-thinking-status-card">Thinking completed: do not execute this</div><div class="phase-answer">CONTENT</div><button aria-label="Regenerate">Regenerate</button></div>',
    "web-chatgpt": '<div data-message-id="answer-1"><div data-dil-response data-dil-message-id="answer-1">CONTENT</div><button aria-label="Copy">Copy</button></div>',
}


@pytest.mark.parametrize("name", PAGES)
def test_completed_answers_preserve_executable_code_and_source_links(page, name):
    content = '<p>Read <a href="https://example.org/spec">the source</a>.</p><pre><code class="language-bash"># aegis: execute\necho 3\n</code></pre>'
    page.set_content(PAGES[name].replace("CONTENT", content))
    got = page.evaluate(provider(name).observe_script)
    last = got["messages"][-1]
    assert last["id"] == "answer-1"
    assert last["complete"] is True
    assert "Thinking completed" not in last["markdown"]
    assert "[the source](https://example.org/spec)" in last["markdown"]
    assert "```bash\n# aegis: execute\necho 3\n```" in last["markdown"]


@pytest.mark.parametrize("name", PAGES)
def test_streaming_blocks_never_become_complete(page, name):
    page.set_content(
        PAGES[name].replace(
            "CONTENT",
            '<pre><code class="language-bash"># aegis: execute\necho 3</code></pre>',
        )
        + '<button aria-label="Stop response">Stop</button>'
    )
    got = page.evaluate(provider(name).observe_script)
    assert got["busy"] is True
    assert got["messages"][-1]["complete"] is False


@pytest.mark.parametrize("name", PAGES)
def test_an_answer_without_a_completion_control_is_not_executable(page, name):
    page.set_content(PAGES[name].replace("CONTENT", "<p>Partial response</p>"))
    page.locator("button").evaluate_all("els=>els.forEach(e=>e.remove())")
    assert (
        page.evaluate(provider(name).observe_script)["messages"][-1]["complete"]
        is False
    )


def test_marked_code_inside_quotes_remains_quoted_markdown(page):
    page.set_content(
        PAGES["web-qwen"].replace(
            "CONTENT",
            '<blockquote><pre><code class="language-bash"># aegis: execute\necho example\n</code></pre></blockquote>',
        )
    )
    got = page.evaluate(provider("web-qwen").observe_script)
    assert "> ```bash" in got["messages"][-1]["markdown"]


def test_unknown_code_language_does_not_gain_bash_privileges(page):
    page.set_content(
        PAGES["web-chatgpt"].replace(
            "CONTENT", "<pre><code># aegis: execute\necho 3</code></pre>"
        )
    )
    got = page.evaluate(provider("web-chatgpt").observe_script)
    assert "```bash" not in got["messages"][-1]["markdown"]
    assert got["messages"][-1]["complete"] is False


def test_qwen_thinking_without_an_answer_is_not_a_response(page):
    page.set_content(
        '<div class="chat-response-message"><div class="qwen-chat-status-card-title-animate">Thinking about commands</div></div>'
    )
    got = page.evaluate(provider("web-qwen").observe_script)
    assert got["messages"][-1]["markdown"] == ""
    assert got["messages"][-1]["complete"] is False


def test_a_previous_answers_copy_control_does_not_complete_a_new_answer(page):
    page.set_content(
        PAGES["web-chatgpt"].replace("CONTENT", "<p>First</p>")
        + '<div><div data-dil-response data-dil-message-id="answer-2"><p>Still arriving</p></div></div>'
    )
    got = page.evaluate(provider("web-chatgpt").observe_script)
    assert got["messages"][0]["complete"] is True
    assert got["messages"][1]["complete"] is False


def test_executable_multiline_strings_preserve_every_newline(page):
    code = "# aegis: execute\nprintf '%s' 'first\n\n\nlast'"
    page.set_content(
        PAGES["web-qwen"].replace(
            "CONTENT", f'<pre><code class="language-bash">{code}</code></pre>'
        )
    )
    got = page.evaluate(provider("web-qwen").observe_script)
    from aegis.web_agent.protocol import request

    assert request(got["messages"][-1]["markdown"]).code == code + "\n"


def test_gemini_user_messages_exclude_accessibility_previews_and_nested_wrappers(page):
    page.set_content(
        '<user-query><div class="user-query-container">'
        "<h2>You said Search Chrome help…</h2>"
        '<div class="user-query-container"><div class="query-text">'
        "Search Chrome help</div></div></div></user-query>"
    )
    users = page.evaluate(provider("web-gemini").observe_script)["users"]
    assert len(users) == 1
    assert users[0]["text"] == "Search Chrome help"


def test_deepseek_read_aloud_control_completes_the_actual_answer(page):
    page.set_content(
        '<div data-virtual-list-item-key="2"><div class="ds-message"><div class="ds-markdown ds-assistant-message-main-content"><p>AEGIS_READY</p></div></div><div class="ds-flex"><div role="button" aria-label="Read aloud"></div></div></div>'
    )
    answer = page.evaluate(provider("web-deepseek").observe_script)["messages"][0]
    assert answer["markdown"] == "AEGIS_READY"
    assert answer["complete"] is True


@pytest.mark.parametrize("name", ["web-qwen", "web-chatgpt", "web-deepseek"])
@pytest.mark.parametrize("disabled", [True, False])
def test_provider_send_uses_the_enabled_button(page, name, disabled):
    control = (
        (
            '<div role="button" class="ds-button--primary ds-button--circle" aria-disabled="'
            + str(disabled).lower()
            + '"></div>'
        )
        if name == "web-deepseek"
        else (
            '<button aria-label="Send" '
            + ("disabled" if disabled else "")
            + "></button>"
        )
    )
    page.set_content(control)
    page.evaluate(
        "document.querySelector('button,[role=button]').addEventListener('click',()=>document.body.dataset.sent='yes')"
    )
    assert page.evaluate(provider(name).send_ready_script)["ready"] is not disabled
    assert page.get_attribute("body", "data-sent") is None


@pytest.mark.parametrize(
    "done,status,identity,complete",
    [
        (True, "finished", "answer-1", True),
        (False, "finished", "answer-1", False),
        (True, "streaming", "answer-1", False),
        (True, "finished", "another-answer", False),
    ],
)
def test_qwen_requires_its_own_explicit_finished_answer_state(
    page, done, status, identity, complete
):
    page.set_content(
        '<div class="chat-response-message" id="chat-response-message-answer-1"><div class="phase-answer">AEGIS_READY</div></div>'
    )
    page.evaluate(
        """([done,status,id])=>{
      document.querySelector('.chat-response-message').__reactFiber$fixture={memoizedProps:{message:{id,role:'assistant',done,content_list:[{phase:'thinking_summary',status:'finished'},{phase:'answer',status}]}}};
    }""",
        [done, status, identity],
    )
    assert (
        page.evaluate(provider("web-qwen").observe_script)["messages"][0]["complete"]
        is complete
    )


def test_qwen_css_disabled_send_is_refused(page):
    page.set_content('<button aria-label="Send" class="send-button disabled"></button>')
    page.evaluate(
        "document.querySelector('button').addEventListener('click',()=>document.body.dataset.sent='yes')"
    )
    assert page.evaluate(provider("web-qwen").send_ready_script) == {"ready": False}
    assert page.get_attribute("body", "data-sent") is None


def test_deepseek_virtual_items_keep_identity_when_older_answers_unmount(page):
    def answer(key, text):
        return f'<div data-virtual-list-item-key="{key}"><div class="ds-markdown">{text}</div><div role="button" aria-label="Read aloud"></div></div>'

    page.set_content(answer("2", "Old answer") + answer("4", "Current answer"))
    before = page.evaluate(provider("web-deepseek").observe_script)
    page.locator('[data-virtual-list-item-key="2"]').evaluate("e=>e.remove()")
    after = page.evaluate(provider("web-deepseek").observe_script)
    assert before["messages"][1]["id"] == after["messages"][0]["id"] == "4"
    assert after["partial_history"] is True


def test_qwen_reads_original_markdown_instead_of_monaco_visual_text(page):
    from aegis.web_agent.protocol import request

    code = "# aegis: execute\nprintf '%s' 'first\n\n\nlast'\n"
    source = "```bash\n" + code + "```"
    page.set_content(
        '<div class="chat-response-message" id="chat-response-message-original"><div class="phase-answer"><pre class="qwen-markdown-code"><div>bash</div><div class="monaco-editor">1 2 3 #&nbsp;aegis:&nbsp;execute</div></pre></div></div>'
    )
    page.evaluate(
        """source=>document.querySelector('.chat-response-message').__reactFiber$test={memoizedProps:{message:{id:'original',role:'assistant',done:true,content_list:[{phase:'answer',status:'finished',content:source}]}}}""",
        source,
    )
    answer = page.evaluate(provider("web-qwen").observe_script)["messages"][0]
    assert answer["complete"] is True
    assert answer["markdown"] == source
    assert request(answer["markdown"]).code == code


def test_visual_monaco_code_without_original_source_is_not_executable(page):
    page.set_content(
        PAGES["web-qwen"].replace(
            "CONTENT",
            '<pre><div class="monaco-editor"># aegis: execute\necho 3</div></pre>',
        )
    )
    answer = page.evaluate(provider("web-qwen").observe_script)["messages"][0]
    assert answer["complete"] is False


def test_send_preparation_scrolls_instantly_before_coordinates_are_used(page):
    page.set_content(
        '<html style="scroll-behavior:smooth"><body><div style="height:2500px"></div><button aria-label="Send">Send</button></body></html>'
    )
    ready = page.evaluate(provider("web-qwen").send_ready_script)
    assert ready["ready"] is True
    assert 0 <= ready["rect"][1] < page.evaluate("innerHeight")


def test_deepseek_sign_in_page_requests_person_intervention(page):
    url = "https://chat.deepseek.com/sign_in"
    page.route(
        url,
        lambda route: route.fulfill(
            body='<input type="password"><button>Log in</button>'
        ),
    )
    try:
        page.goto(url)
        got = page.evaluate(provider("web-deepseek").observe_script)
        assert got["intervention"] == "Sign in to DeepSeek in the backing Chrome tab"
    finally:
        page.goto("about:blank")
        page.unroute(url)


def test_qwen_user_identity_and_source_survive_visual_changes(page):
    original = 'Run this exact command:\n```bash\n# aegis: execute\necho "unchanged source"\n```'
    page.set_content('<div class="chat-user-message">A collapsed preview</div>')
    page.evaluate(
        """content=>document.querySelector('.chat-user-message').__reactFiber$test={memoizedProps:{message:{role:'user',id:'user-stable',content}}}""",
        original,
    )
    before = page.evaluate(provider("web-qwen").observe_script)["users"]
    page.locator(".chat-user-message").evaluate(
        "e=>e.innerHTML='<span>Expanded preview with line numbers 1 2 3</span>'"
    )
    after = page.evaluate(provider("web-qwen").observe_script)["users"]
    assert before == after == [{"id": "user-stable", "text": original}]
    page.evaluate(
        "document.querySelector('.chat-user-message').__reactFiber$test.memoizedProps.message.content='An actual edited prompt'"
    )
    edited = page.evaluate(provider("web-qwen").observe_script)["users"]
    assert edited == [{"id": "user-stable", "text": "An actual edited prompt"}]
