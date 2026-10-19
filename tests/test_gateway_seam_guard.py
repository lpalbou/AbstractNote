"""``abstractnote.gateway`` refuses to import into a gateway without the AbstractNote seam (review 02 m5).

Runs in a subprocess so the import machinery of this test session is untouched.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

NO_SEAM_GATEWAY = textwrap.dedent("""
    import importlib.util, sys
    real = importlib.util.find_spec
    def find_spec(name, package=None):
        if name == "abstractgateway.abstractnote_seam":
            return None  # a gateway release without the seam
        return real(name, package)
    importlib.util.find_spec = find_spec
""")
BLOCK_SEAM = NO_SEAM_GATEWAY + textwrap.dedent("""
    try:
        import abstractnote.gateway.router
    except ImportError as e:
        print(str(e))
        sys.exit(3)
    print("imported")
""")


def test_without_the_seam_the_import_fails_with_one_sentence():
    out = subprocess.run([sys.executable, "-c", BLOCK_SEAM], capture_output=True, text=True, timeout=120)
    assert out.returncode == 3, (out.stdout, out.stderr)
    sentence = out.stdout.strip()
    assert sentence.startswith("This AbstractGateway does not carry the AbstractNote seam")
    assert sentence.count(". ") == 0 and sentence.endswith(".")


def test_the_core_package_never_needs_the_gateway():
    code = NO_SEAM_GATEWAY + textwrap.dedent("""
        import abstractnote, abstractnote.store.notebook, abstractnote.cli, abstractnote.flows.build
        print("ok")
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "ok", (out.stdout, out.stderr)
