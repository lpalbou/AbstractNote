"""W28 item 4: ``GET /note/status`` reports the version of the AbstractMemory that is IMPORTED, not the installed
metadata of another copy (a checkout on PYTHONPATH ran 0.3.2 while the metadata said 0.3.1)."""

from __future__ import annotations

import importlib
import importlib.metadata as md
import sys

import pytest

from abstractnote.versions import module_version


@pytest.fixture
def stale_install(tmp_path, monkeypatch):
    """``fakemem_w28``: a source tree (pyproject 0.3.2, no ``__version__``) first on the path, and an installed copy's
    metadata (0.3.1, its RECORD naming a file in site-packages) behind it."""
    src = tmp_path / "checkout" / "src" / "fakemem_w28"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "checkout" / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools"]\n\n[project]\nname = "fakemem-w28"\nversion = "0.3.2"\n',
        encoding="utf-8")
    site = tmp_path / "site"
    (site / "fakemem_w28").mkdir(parents=True)
    (site / "fakemem_w28" / "__init__.py").write_text("X = 0\n", encoding="utf-8")
    info = site / "fakemem_w28-0.3.1.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: fakemem-w28\nVersion: 0.3.1\n", encoding="utf-8")
    (info / "RECORD").write_text("fakemem_w28/__init__.py,,\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.syspath_prepend(str(tmp_path / "checkout" / "src"))
    sys.modules.pop("fakemem_w28", None)
    importlib.invalidate_caches()
    yield
    sys.modules.pop("fakemem_w28", None)


def test_the_imported_copy_decides_not_the_installed_metadata(stale_install):
    """MUTATION: module_version returning importlib.metadata.version(name) (what status read before)."""
    assert md.version("fakemem-w28") == "0.3.1"  # the metadata names the installed copy
    assert module_version("fakemem_w28") == "0.3.2"  # the checkout is what is imported


def test_an_installed_copy_that_is_imported_reports_its_metadata(stale_install, monkeypatch, tmp_path):
    """The installed copy first on the path: its RECORD lists the imported file, so its metadata is the answer.
    MUTATION: skip the RECORD match (the pyproject walk finds nothing above site-packages: None)."""
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    sys.modules.pop("fakemem_w28", None)
    importlib.invalidate_caches()
    assert module_version("fakemem_w28") == "0.3.1"


def test_status_reports_the_imported_abstractmemory(tmp_path, monkeypatch):
    """Notebook.status (hence GET /note/status) asks module_version for the imported copy.
    MUTATION: Notebook.status reading importlib.metadata again."""
    import abstractnote.versions as versions
    from abstractnote.store.notebook import Notebook

    seen = []
    monkeypatch.setattr(versions, "module_version", lambda name: seen.append(name) or "0.3.2-imported")
    with Notebook.open(tmp_path / "nb") as nb:
        assert nb.status()["versions"]["abstractmemory"] == "0.3.2-imported"
    assert seen == ["abstractmemory"]
