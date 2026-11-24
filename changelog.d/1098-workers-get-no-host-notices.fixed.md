- **Queue workers no longer get the disk, memory, cpu or quota notice.** A worker
  read the notice's cleanup steps as its task, ran `aegis gc --apply` and ended
  without starting the work. Workers, workflow nodes and schedule runs now get
  announcements only.
