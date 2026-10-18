"""Pack the generated flows into ``abstractnote@<version>.flow`` (AbstractRuntime bundle format)."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from .build import AUTOMATION_DEFAULTS, BUNDLE_ID, ENTRYPOINTS, JSON_DIR, stale


def bundle_version() -> str:
    from abstractnote import __version__

    return str(__version__)


def pack(out_dir: Path, *, flows_dir: Path = JSON_DIR, version: Optional[str] = None, check_fresh: bool = False) -> Path:
    """Write ``<out_dir>/abstractnote@<version>.flow`` and return its path.

    The JSON documents on disk are the shipped artifact. ``check_fresh`` (the ``abstractnote
    bundle`` command and the tests) refuses flows that are stale relative to the generator, so a
    bundle someone publishes always matches the code; the gateway packs what is installed.
    """
    from abstractruntime.workflow_bundle import pack_workflow_bundle

    if check_fresh and flows_dir == JSON_DIR:
        bad = stale(flows_dir)
        if bad:
            raise RuntimeError("the generated flows are stale (" + ", ".join(bad) + "); run python -m abstractnote.flows.build")
    ver = version or bundle_version()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{BUNDLE_ID}@{ver}.flow"
    pack_workflow_bundle(
        root_flow_json=flows_dir / f"{ENTRYPOINTS[0]}.json",
        out_path=out_path,
        bundle_id=BUNDLE_ID,
        bundle_version=ver,
        flows_dir=flows_dir,
        entrypoints=list(ENTRYPOINTS),
        default_entrypoint=ENTRYPOINTS[0],
        metadata={
            "family": "abstractnote",
            "purpose": "notes",
            "min_runtime": "0.10.2",
            "automation_defaults": {k: v for k, v in AUTOMATION_DEFAULTS.items()},
        },
    )
    return out_path


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m abstractnote.flows.bundle", description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("."), help="directory for the .flow file (default: .)")
    args = ap.parse_args(argv)
    print(pack(args.out, check_fresh=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
