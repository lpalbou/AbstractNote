"""What a bound tool set knows: the notebook, the injected readers and the account."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

ArtifactReader = Callable[[Dict[str, Any]], Tuple[bytes, Optional[str], Optional[str]]]


@dataclass
class ToolContext:
    nb: Any  # abstractnote.store.notebook.Notebook
    artifact_reader: Optional[ArtifactReader] = None
    allow_paths_under: Optional[Path] = None
    account: Optional[str] = None
    default_client: str = "agent"
    extractors: Dict[str, Any] = field(default_factory=dict)
    # backlog 0037: AbstractRuntime's artifact store (note bodies are stored there as artifacts of the capture
    # run) and the reader of the runtime step executing now; both handed to the notebook when it opens
    artifact_store: Any = None
    step_reader: Optional[Callable[[], Optional[Dict[str, Any]]]] = None
