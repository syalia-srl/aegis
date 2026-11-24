---
title: Repository cleanup
kind: concept
when: disk space is low; finding rebuildable files in inactive repositories; proposing or approving repository cleanup through /gc, MCP or the CLI
hints:
  - /gc shows a selection card; nothing is deleted until you click approval.
  - gc_propose scans and records evidence; agents cannot approve their own proposal.
---

Repository gc finds rebuildable artifacts in inactive projects and asks the
person to approve an exact selection before deleting anything.

## Propose a cleanup

Type `/gc` in a session to scan repositories under the config root (its repos/
when present). `/gc 60` changes the default 30 days of inactivity to 60.
The command runs on the server without asking the model to investigate.

An agent calls `gc_propose` with `days=30` and optionally `path="repos/project"`
to get the same selection card plus a structured inventory. It calls `gc_read`
with the returned id to read the proposal and its outcome. These tools do not
delete anything. End the turn after proposing; the person's decision wakes the
session with a message headed `> from gc:<id>`.

## Review and approve

The card lists each eligible path, its artifact type, allocated bytes,
estimated reclaimable bytes, source and artifact activity dates, and how to
rebuild it. Expand the exclusions to see why another path was kept. Select or
deselect candidates, then click **Delete selected directories**, or **Cancel**.
Every proposal needs this explicit approval of its exact selection. A disk
notice, MCP call, or an earlier approval never approves another proposal.
The approval operation is not available to agents. Cards survive a restart.

Only recognized Git-ignored artifacts with rebuild evidence are eligible:
Python virtual environments, locked JavaScript dependencies, Rust build output,
recognized JavaScript build directories, and local Python/tool caches. A name
or ignore rule alone is insufficient. Tracked changes, tracked files inside an
artifact, recent source or artifact writes, active session directories, running
process references, and installed command references exclude candidates.
Unclassified data, shared caches and symlinked directories are kept. Scanning
process references requires Linux /proc. Inspection failures keep data.

Execution rechecks the inventory, source metadata, artifact identity, activity,
and references. Changed candidates are skipped, and no newly discovered path is
added to the selection. The result reports removed paths, skipped paths and
reasons, errors, free space before and after, actual recovered bytes and tracked
file state. A cancelled card deletes nothing. A card interrupted while executing
is not executable again; scan again to propose the remaining files.

Estimates use allocated bytes and discount files whose hard links also exist
outside that candidate. Actual free space can differ because of filesystem
accounting and concurrent activity. Source inactivity is based on filesystem
write times, excluding generated directories, rather than the last commit date.
Read-only analysis does not refresh Git's index or run build commands.

## Command line

`aegis gc --repos` prints a read-only proposal. Add `--json` for the structured
inventory, `--days 60` for a different threshold, or `--path repos/project` to
limit its scope. Paths must be under the config root selected with `--root`.

`aegis gc --repos --apply` requires an interactive terminal. It asks for candidate
numbers, displays that selection, and asks for confirmation, defaulting to no.
Piped input and combining apply with JSON output are refused. Agents should use
the MCP proposal and the UI's approval card instead of the interactive CLI.

Without `--repos`, `aegis gc` retains its existing scope: sessions archived more
than seven days ago. `aegis gc --apply` packs their transcripts and deletes their
uploads and browser data. Repository artifacts are never silently added to that
existing command.
