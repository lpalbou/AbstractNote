"""AbstractNote: one endpoint for your notes; a temporal semantic knowledge graph of your life.

``Notebook.open(dir)`` opens a notebook directory; ``abstractnote.tools.bind(dir)``
returns the note tools for flows and agents.
"""

__version__ = "0.3.0"


def __getattr__(name: str):  # lazy: importing abstractnote stays cheap
    if name == "Notebook":
        from .store.notebook import Notebook

        return Notebook
    raise AttributeError(name)


__all__ = ["Notebook", "__version__"]
