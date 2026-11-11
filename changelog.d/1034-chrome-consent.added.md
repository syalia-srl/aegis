- **Share tabs of your own browser with agents.** The extension's popup has a
  switch per tab that shares it with the agent sessions you tick; a shared
  tab joins the browser's aegis tab group. An agent asks for a tab with
  `browser_request`, or to open a page with `browser_open(where="person")`;
  a card with Give and Refuse appears in its transcript and in the popup, and
  the agent is woken with granted or denied. `browser_list` shows the tabs
  granted to it. The session's side panel lists them with Disconnect, and the
  popup's chips revoke one agent. Agents driving those tabs comes next
  (#1034).

help: chrome#sharing-a-tab
