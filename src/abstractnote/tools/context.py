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
