---
title: Web agents in Chrome
kind: howto
when: running Gemini, DeepSeek, Qwen or ChatGPT as an aegis agent through your signed-in Chrome extension, selecting a web provider in the composer, priming a web conversation or executing explicitly marked Bash
hints:
  - Choose a web provider in the new session's Harness chip. #start-a-session
  - Web agents use your signed-in Chrome and return actual Bash results. #local-commands
---

Gemini, DeepSeek, Qwen and ChatGPT can run as ordinary aegis sessions using
your own signed-in Chrome tabs. The common driver reads completed responses
through the aegis extension and returns local command results to the same
conversation. There is no provider API key or additional OAuth flow.

## Start a session

Pair your browser in Settings → Chrome first (chrome). In a new session,
choose Gemini web, DeepSeek web, Qwen web or ChatGPT web from the Harness
chip. The agent preset still supplies its permissions, working directory
and instructions. The model and effort chips are hidden: choose the actual
model, research mode and other provider options in Chrome.

Automatic launch requires extension 0.5.2 or newer. Download and reload it
from Settings → Chrome after updating.

Send a task. The harness automatically opens and assigns a new provider tab
in the paired Chrome browser with Scripts enabled. Sign in if needed. New sessions require an empty conversation;
existing chats cannot be adopted. Resuming a session returns to its saved
conversation. Each backing tab belongs to one web session at a time. Submissions bring that
tab forward and use trusted Chrome input; simultaneous web sessions serialize
this short focus-and-submit step.

The transcript uses the usual prose, Bash and result rows. Its connection row
says when Chrome is disconnected, Scripts is off or the person must intervene.
Open in Chrome brings the backing tab forward. This action requires extension
0.5.1 or newer; download and reload it from Settings → Chrome after updating.

## Configure an agent

The four names are web-gemini, web-deepseek, web-qwen and web-chatgpt. For example:

```yaml
agents:
  research:
    harness: web-qwen
    model: web
    effort: high
    permission: full
    description: Research online and prepare proposals for another agent.
```

The internal model value is web; the required effort field is ignored by this
driver. Use the agent with `session_spawn` or as a queue's configured worker,
like any other agent. Starting the harness authorizes its own new provider tab;
generic browser requests by other agents still require consent. The paired
Chrome browser must be connected, and sign-in may require the person.
Extra MCP servers are unsupported; the local aegis command provides aegis tools.
Actual provider model identity, token usage and cost are unavailable and are not
estimated.

## Local commands

Web sessions receive a compact dedicated bridge brief: working directory,
permissions, Bash execution and tool discovery. The task is sent separately.
The normal coding-agent primer and ancestor AGENTS.md and CLAUDE.md files are
not injected automatically; relevant context is loaded on demand. The brief’s
acknowledgement cannot execute commands. A completed response requests one local command with a top-level
Bash fence whose first nonblank line is exactly the execution marker:

~~~~markdown
```bash
# aegis: execute
# Find Chrome's help page
aegis call help_search query="Chrome" limit=1
```
~~~~

Unmarked blocks, quoted examples,
list-nested blocks and incomplete responses do not run. Several marked blocks
in one response are refused together. The provider receives real stdout,
stderr and the exit code as text. It must wait for that result before continuing.

Commands run as Bash in the session's directory and environment, using its own
local aegis credentials. Discover a tool with `aegis tools <tool>` and invoke it
with `aegis call <tool> key=value`. The read and write permission levels refuse
all free-form Bash; auto and full allow it. Disabling the agent's harness tools
also refuses Bash. These levels are a dispatch policy, not an OS sandbox.

A command times out after 120 seconds. Remaining children in its process group
are stopped when the command returns, even if they redirected their output.
Keep required work in the foreground. Interrupt stops its process group and
asks the provider to stop generating. Each output stream sent to the provider
is capped at 64 KiB; the result names the full local output files when truncated.
Execution and message delivery are recorded durably. If a restart leaves a
command or a submission uncertain, aegis stops and asks for reconciliation;
it does not blindly execute or send again.

## Work in Chrome

Pause the tab in the extension before clicking, changing model or uploading
files. Resume when ready; the session waits while the person holds the tab.
Verification and sign-in dialogs also pause the driver. Manual messages and completed replies are imported before the next aegis
task; observed manual prompts are labelled From Chrome and never consume a
pending composer message. Keep the tab on the saved conversation. Changing a completed response or opening a different chat
requires reconciliation before commands can continue.

Upload files manually in Chrome for now. Generated image and artifact import,
provider-specific research activity and automatic model changes are not exposed
by this first version. An agent can use `file_send` through local Bash for files
it already created in the aegis environment.
