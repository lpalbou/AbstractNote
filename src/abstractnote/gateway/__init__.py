"""AbstractNote inside AbstractGateway: the note read API router, the plane-bound note tools,
and the app catalog constants. Imported by the gateway only when ``abstractnote`` is installed
(ADR 0001: an explicit optional integration, no plugin loader).

The gateway side of that integration is its ``abstractgateway.abstractnote_seam`` module. A
gateway without it never mounts the note API, binds the note tools or loads the workflows, so
importing this package there fails at once with one sentence instead of half-working."""

import importlib.util

SEAM_MODULE = "abstractgateway.abstractnote_seam"
NO_SEAM = ("This AbstractGateway does not carry the AbstractNote seam (abstractgateway.abstractnote_seam), "
           "so the note API, tools and workflows are unavailable; install a gateway release that includes it.")


def _seam_installed() -> bool:
    try:
        return importlib.util.find_spec(SEAM_MODULE) is not None
    except ModuleNotFoundError:  # no abstractgateway at all
        return False


if not _seam_installed():
    raise ImportError(NO_SEAM)
