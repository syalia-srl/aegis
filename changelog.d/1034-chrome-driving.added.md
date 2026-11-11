- **Agents drive the tabs you give them.** On a tab of your own browser
  shared with it, an agent uses `browser_snapshot`, `browser_click`,
  `browser_type`, `browser_press`, `browser_goto`, `browser_screenshot` and
  `browser_events` with the tab's `chrome:<name>` id, as on a server
  browser, and its clicks and keys reach the page as yours would. A ribbon at
  the top of the page names the agents that have it, says what one is doing
  and rings the element it touches. The session's side panel shows each
  tab's last action. The extension, now 0.3.0, asks for the debugger
  permission and access to the sites you share (#1034).

help: chrome#an-agent-at-work
