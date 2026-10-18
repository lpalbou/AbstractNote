# AbstractNote

One endpoint for everything you want to remember. Send a sentence, a document, a photo, a
recording, a log. AbstractNote files it with its time, place and provenance, links it to what
you already know, and grows a **temporal semantic knowledge graph** of your ideas, projects,
people and decisions that you can search by effort (quick, standard, deep) and revisit through
time: what you knew, when you learned it, and how your view changed.

AbstractNote is part of the AbstractFramework and runs on **AbstractGateway** (auth, one runtime
per user, apps), **AbstractRuntime** and **AbstractFlow** (the capture and understanding
workflows), **AbstractMemory** (temporal facts) and **AbstractCore** (models, structured output,
embeddings).

Status: in development (0.1.0). See [docs/README.md](docs/README.md).

## What you get

- **Capture anywhere**: web app, Apple Watch, CLI, agents; attachments de-duplicated by content.
- **Automatic filing**: kind, topics, urgency, language, with the time and place the note was made.
- **Self-organising knowledge**: entities and relationships resolved and de-duplicated by a small
  fast model from deterministic candidates; every change is a revision you can replay.
- **Remember by cue and effort**: quick (no model), standard (one synthesis with citations),
  deep (a background dossier). Then-versus-now for any person, project or idea.
- **Reflection**: open loops, contradictions, things that went quiet, connections you missed.

## Documentation

- [Getting started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Data model](docs/data-model.md) and [Cognition](docs/cognition.md)
- [Remembering](docs/remembering.md)
- [API](docs/api.md) and [Clients](docs/clients.md)
- [Importers](docs/importers.md)

## License

MIT. See [LICENSE](LICENSE).
