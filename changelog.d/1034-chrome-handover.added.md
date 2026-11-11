- **Hand a tab back and forth with agents.** Pause in a shared tab's ribbon
  makes it yours until Resume: agents can read it, and their clicks, typing
  and navigation wait. An agent that needs you calls `browser_handover` on
  the tab: the ribbon turns red with its reason and a Done button, the
  browser shows a notification and aegis a toast, and Done, in the ribbon
  or on the tab in the session's side panel, wakes it with what happened.
  Several agents on one tab act one at a time. Extension 0.4.0 (#1034).

help: chrome#pausing-and-handing-over
