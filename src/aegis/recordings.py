"""Audio a person records in the composer by holding the microphone (#932).

The browser records Opus at a low bitrate (js/record.js) and uploads it as an
attachment marked as a recording (attachments.py). When the message is sent,
the server rewrites it as Ogg Opus, mono, 16 kHz, 24 kb/s, about 180 KB a
minute: browsers do not reliably honor a requested sample rate or channel
count, so the server is the one place that can promise the format. Nothing is
transcribed; the agent gets the path and the duration.

The rewrite needs ``ffmpeg`` on the PATH. Without it, or when it fails, the
browser's file is kept as it came and its duration is the browser's own
measure of the take: a send never fails because of the rewrite.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import launch

RATE = 16_000
BITRATE = "24k"
# Minutes of narration take a second or two to rewrite; a stuck ffmpeg must
# not hold the send for ever.
TIMEOUT_S = 120


def normalize(path: Path) -> tuple[Path, float | None]:
    """``path`` rewritten beside it as ``<stem>.ogg`` (Opus, mono, 16 kHz),
    the original removed, and the duration in seconds; or ``path`` itself and
    None when ffmpeg is missing or fails."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return path, None
    out = path.with_name(f"{path.stem}.norm.ogg")
    try:
        r = launch.run(
            [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(path), "-vn",
             "-ac", "1", "-ar", str(RATE), "-c:a", "libopus", "-b:a", BITRATE,
             "-application", "voip", str(out)],
            user=None, timeout=TIMEOUT_S,
        )  # fmt: skip
        if r.returncode != 0:
            raise subprocess.CalledProcessError(r.returncode, ffmpeg)
    except (OSError, subprocess.SubprocessError):
        out.unlink(missing_ok=True)
        return path, None
    final = path.with_name(f"{path.stem}.ogg")
    path.unlink()
    out.replace(final)
    return final, duration(final)


def duration(path: Path) -> float | None:
    """Seconds of audio in ``path``, by ffprobe; None when it cannot tell."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    try:
        r = launch.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            user=None, text=True, timeout=30,
        )  # fmt: skip
        return round(float(r.stdout.strip()), 1)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def clock(seconds: float) -> str:
    """``m:ss``, the way a recorder shows a take."""
    s = round(seconds)
    return f"{s // 60}:{s % 60:02d}"
