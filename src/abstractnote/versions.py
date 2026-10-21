"""The version of a package AS IMPORTED (W28 item 4).

``importlib.metadata.version(name)`` reads the installed distribution's metadata, which can name another copy than
the one Python imports: a source checkout on ``PYTHONPATH`` (or an editable install whose version was bumped after
it was installed) runs 0.3.2 while the metadata still says 0.3.1. ``GET /note/status`` must say what runs, so the
version is read from the imported module: its ``__version__``; else the distribution whose RECORD lists the very file
that was imported; else the ``pyproject.toml`` of the source tree the module was imported from. None when none of
those says (never the metadata of a copy that is not the one imported).
"""

from __future__ import annotations

import importlib
import importlib.metadata as md
import re
import sys
from pathlib import Path
from typing import Optional

_PROJECT = re.compile(r"^\s*\[project\]\s*$")
_TABLE = re.compile(r"^\s*\[")
_NAME = re.compile(r"""^\s*name\s*=\s*["']([^"']+)["']""")
_VERSION = re.compile(r"""^\s*version\s*=\s*["']([^"']+)["']""")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pyproject_version(path: Path, name: str) -> Optional[str]:
    """``[project] version`` of a pyproject.toml whose ``[project] name`` is ``name`` (a TOML reader when the
    interpreter has one, else the two lines of the ``[project]`` table)."""
    text = path.read_text(encoding="utf-8")
    if sys.version_info >= (3, 11):
        import tomllib

        project = (tomllib.loads(text).get("project") or {})
        if _norm(str(project.get("name") or "")) != _norm(name):
            return None
        v = project.get("version")
        return str(v) if v else None
    inside, found_name, version = False, None, None
    for line in text.splitlines():
        if _PROJECT.match(line):
            inside = True
            continue
        if inside and _TABLE.match(line):
            break
        if inside and (m := _NAME.match(line)):
            found_name = m.group(1)
        if inside and (m := _VERSION.match(line)):
            version = m.group(1)
    return version if found_name and _norm(found_name) == _norm(name) else None


def module_version(name: str) -> Optional[str]:
    """The version of the package ``name`` as imported (see the module docstring); None when it cannot be told."""
    try:
        mod = importlib.import_module(name)
    except ImportError:
        return None
    v = getattr(mod, "__version__", None)
    if isinstance(v, str) and v.strip():
        return v.strip()
    origin = getattr(mod, "__file__", None)
    if not origin:
        return None
    here = Path(origin).resolve()
    for dist in md.distributions():
        if _norm(str(dist.metadata.get("Name") or "")) != _norm(name):
            continue
        for f in dist.files or []:
            try:
                if Path(dist.locate_file(f)).resolve() == here:
                    return dist.version
            except (OSError, ValueError):
                continue
    for parent in here.parents:
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            return _pyproject_version(candidate, name)
    return None
