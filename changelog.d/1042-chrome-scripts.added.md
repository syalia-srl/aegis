- **Let agents run scripts in a tab you share, when you say so.** Each shared
  tab has a Scripts switch, off by default, in the extension's popup and on
  the tab in the session's side panel. While it is on, an agent can call
  `browser_eval` on the tab. A tab an agent asked to open is now named after
  its site, and the snapshot of a page whose accessibility tree comes back
  empty shows an outline of its elements instead. Extension 0.5.0 (#1042).

help: chrome#letting-agents-run-scripts
