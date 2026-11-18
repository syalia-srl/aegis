- **The session panel's actions stay on one row in a narrow panel.** With
  Autohandoff the panel has seven action icons, and below about 220 px they
  wrapped onto a second row. They now share one row at any width.
- **Settings no longer draws two sections with one id.** The Model ranking
  switch and the models table were both `set-models`, so a redraw of one could
  replace the other.
