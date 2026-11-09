- **The client is built from mounts, and every string is labelled.** The
  standalone page is one mount adopting the page's empty root, and a host's
  chat is another; each builds its own parts, and every part takes its words
  from a label table rather than carrying English in its markup or code. aegis
  ships `en` and `es`: `lang` picks one, `labels` replaces single keys, a
  missing key falls back to English and is logged once, and the standalone
  page takes `?lang=es`. The full layout, a chat, the session panel, the tab
  strip, the Fleet, the New session form, the command menu, dictation and the
  terminal and browser views read Spanish from the same tables. Counts and
  dates stay in code; their nouns come from the table.
help: library#the-chat-in-your-page
