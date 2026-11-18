- **A session resumed by a restart keeps aegis's tools.** aegis bound its port
  only after boot, and boot is where it resumes the turns the restart cut, so
  a resumed Claude Code session reached `/mcp` while the port refused
  connections and lost every aegis tool for the rest of its process. The
  server now listens before it boots: that first connection waits until boot
  ends and is then served.
