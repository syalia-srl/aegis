- **A linked server's docked images and browsers draw on the home server.**
  A docked file pane took the far server's `/files/…` URL as it was, so an
  image the far session docked showed a broken icon on this server; it now
  goes through `/via/<server>` like the file's card. A docked browser was
  looked up by its bare id among this server's sessions, so the pane never
  mounted; it is now found on the shown session's server and streams
  through the link.
