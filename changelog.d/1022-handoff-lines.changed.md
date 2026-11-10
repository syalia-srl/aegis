- **A handoff shows in both tabs, and the session's settings go with it.** The old tab reads "continued in" and the new handle, and the new one reads "continued from" and the old handle. Both list what moved: monitors, feeds, queue tasks, schedules, and the `/autohandoff` and `/autoclose` settings, which now follow the new session. Before this, the only trace was inside the new session's first message, and the new session dropped both settings. When autohandoff fires, the old tab shows a line with the context size.

help: handoffs#continue-in-a-fresh-session
