- **Review rebuildable repository files before deleting them.** `/gc` and the
  `gc_propose` MCP tool show the same selection card with activity, space estimates,
  rebuild instructions and exclusions. Only the person can approve deletion;
  execution skips files that changed or started being used. The CLI adds an
  explicit `aegis gc --repos` scope with interactive selection and confirmation.
help: gc
