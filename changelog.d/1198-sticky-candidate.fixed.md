- **The request over the transcript is the one you are reading under.** The
  header added in #1196 only ever held the newest message, so reading history
  between two messages showed no header at all: the newest one was below you.
  It now pins the nearest message whose whole row has passed above the top,
  follows you back up through earlier ones, and leaves a message that arrives
  below the reading area alone until its row takes its turn.
