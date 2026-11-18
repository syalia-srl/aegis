import hashlib
import io
import zipfile

import pytest

from aegis import pdfjs


def _release(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


class _Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(data: bytes):
    return lambda url, timeout: _Answer(data)


def test_without_an_install_nothing_is_there(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_PDFJS_DIR", str(tmp_path / "none"))
    assert not pdfjs.installed()


def test_install_unpacks_a_release_that_matches_its_hash(tmp_path, monkeypatch):
    data = _release({"web/viewer.html": "<html>", "build/pdf.mjs": "//"})
    monkeypatch.setenv("AEGIS_PDFJS_DIR", str(tmp_path / "pdfjs"))
    monkeypatch.setattr(pdfjs, "SHA256", hashlib.sha256(data).hexdigest())
    where = pdfjs.install(opener=_opener(data))
    assert pdfjs.installed()
    assert (where / "build" / "pdf.mjs").read_text() == "//"
    assert not where.with_name("pdfjs.part").exists()


def test_install_refuses_a_download_with_another_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_PDFJS_DIR", str(tmp_path / "pdfjs"))
    with pytest.raises(pdfjs.InstallError, match="SHA-256"):
        pdfjs.install(opener=_opener(_release({"web/viewer.html": "<html>"})))
    assert not pdfjs.installed()


def test_install_refuses_a_release_without_the_viewer(tmp_path, monkeypatch):
    data = _release({"build/pdf.mjs": "//"})
    monkeypatch.setenv("AEGIS_PDFJS_DIR", str(tmp_path / "pdfjs"))
    monkeypatch.setattr(pdfjs, "SHA256", hashlib.sha256(data).hexdigest())
    with pytest.raises(pdfjs.InstallError, match="viewer.html"):
        pdfjs.install(opener=_opener(data))
    assert not (tmp_path / "pdfjs").exists()
    assert not (tmp_path / "pdfjs.part").exists()
