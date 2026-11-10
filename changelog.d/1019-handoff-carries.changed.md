A handoff carries what the session waits on. The "continue fresh" chip, `/handoff` and `session_handoff` used to be refused while a monitor, a feed or a queue task would wake the session, so the work had to be cancelled and armed again by hand. Now the monitors, feeds, queue tasks, the schedules that fire into the session and its workflow follow the new session, its first message lists them, and they go back if it fails before answering. Only a background task of the harness still refuses.

help: handoffs#continue-in-a-fresh-session
