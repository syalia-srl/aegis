`aegis gc` reclaims the disk old sessions hold. For every session archived more than 7 days ago (`--days`), it packs the transcript (base64 images emptied, the rest gzipped; `peer_read`, the archive and the journal still read it) and deletes its uploads, sent files, stderr log, browser folders and the harness's own transcript, and it deletes every browser profile unused for as long. It lists what would go until you pass `--apply`. The disk notice now tells agents about it.

help: commands#the-aegis-program
