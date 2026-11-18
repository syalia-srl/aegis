`/autohandoff [N]` continues a long session on its own. Past `N` tokens of context (the configured `handoff_at:` by default), `turn_end` asks the agent for a brief, and the `turn_end` that carries one continues the session in a fresh one when the turn ends, with no click. A sidebar button toggles it.

help: handoffs#autohandoff
