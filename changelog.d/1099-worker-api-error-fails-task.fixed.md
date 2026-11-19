- **A worker stopped by an API error fails its task, and `task_resume` continues
  it.** The task was marked ok with the `API Error:` line as its result and the
  worker archived, so it could not be resumed.
