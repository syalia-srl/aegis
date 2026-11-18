"""Mozilla's PDF viewer (pdf.js), installed on demand rather than shipped.

The viewer is about 20 MB unpacked, so the package does not carry it.
``aegis pdfjs install`` downloads Mozilla's release zip, checks it against the
pinned SHA-256 and unpacks it under the user's data directory. The server
serves it at ``/pdfjs`` and tells each client in its welcome whether it is
there; without it, a PDF opens in the browser's own viewer and has no
annotation tools, since those read pdf.js's page and text layer.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path

VERSION = "5.4.296"
URL = f"https://github.com/mozilla/pdf.js/releases/download/v{VERSION}/pdfjs-{VERSION}-dist.zip"
SHA256 = "c00128ab92dfc73249cdf276ef7b34a0d8403e40ecbfcc7b9344411e33dd43e4"


def root() -> Path:
    """Where this version unpacks. A new pin lands beside the old one.
    ``AEGIS_PDFJS_DIR`` names the directory itself (the tests use it)."""
    if os.environ.get("AEGIS_PDFJS_DIR"):
        return Path(os.environ["AEGIS_PDFJS_DIR"])
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "aegis" / "pdfjs" / VERSION


def installed() -> bool:
    return (root() / "web" / "viewer.html").is_file()


class InstallError(Exception):
    pass


def install(opener=None) -> Path:
    """Download, check and unpack the pinned release. ``opener`` is the test seam."""
    with (opener or urllib.request.urlopen)(URL, timeout=60) as response:
        data = response.read()
    got = hashlib.sha256(data).hexdigest()
    if got != SHA256:
        raise InstallError(f"the download's SHA-256 is {got}, expected {SHA256}")
    dest = root()
    tmp = dest.with_name(dest.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp)
    if not (tmp / "web" / "viewer.html").is_file():
        shutil.rmtree(tmp, ignore_errors=True)
        raise InstallError("the release has no web/viewer.html")
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return dest
