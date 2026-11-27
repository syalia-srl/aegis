- **A workflow recap reads as a paragraph again.** Codex and OpenCode deliver
  the model's answer in their own `text` field, so a recap that obeyed the JSON
  request was saved and shown with its `{"text": "…"}` wrapper. The one-shot
  parse now prefers the object that field serializes, and recaps saved before
  the fix lose the wrapper when they load.
