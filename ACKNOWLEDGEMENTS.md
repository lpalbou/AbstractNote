# Acknowledgements

AbstractNote is built on the AbstractFramework and on open-source work from many people.

## AbstractFramework packages

- **AbstractGateway**: sign-in, one runtime and data plane per user, the runs API, automations,
  attachments and app hosting.
- **AbstractRuntime**: durable runs, the VisualFlow compiler and executor, the tool executor.
- **AbstractFlow**: the workflow format, node catalog and editor the AbstractNote flows use.
- **AbstractMemory**: the temporal triple store (`SQLiteTripleStore`, `TripleAssertion`,
  `TripleQuery`), the journal and the embedding clients.
- **AbstractSemantics**: the predicate and entity-type registry for claims.
- **AbstractCore**: model calls with structured output, providers (including MLX), embeddings and
  document text extraction.
- **AbstractUIC**: `@abstractframework/ui-kit`, `@abstractframework/panel-chat` and
  `@abstractframework/app-server` for the web app.
- The **AbstractAssistant** watch app, whose gateway client the AbstractNote watch package
  started from.

## Models and tools

- The **Qwen** team for the Qwen3.6 models recommended for filing and processing and the Qwen3
  embedding models, and the **MLX** and **mlx-community** projects that run them on Apple silicon.
- **LM Studio**, a convenient local OpenAI-compatible server for embedding and text models.

## Libraries

- Python: **pydantic**, **FastAPI** and **SQLite** (FTS5 full-text search).
- Web: **React**, **Vite**, **TypeScript**, **d3-force** (the graph layout), **Vitest** and
  **Playwright**.
- Apple: **SwiftUI** and **XcodeGen**.

## Community

The Code of Conduct is adapted from the [Contributor Covenant](https://www.contributor-covenant.org).
