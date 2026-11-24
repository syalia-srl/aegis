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
