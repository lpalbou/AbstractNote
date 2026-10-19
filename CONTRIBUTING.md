# Contributing to AbstractNote

Thank you for helping. This page covers the development setup, the tests, the generated flows and
the rules that keep the product consistent. Read [docs/architecture.md](docs/architecture.md)
first; the HTTP, tool and CLI contracts are in [docs/api.md](docs/api.md), and the design
decisions in [docs/adr/](docs/adr/README.md).

## Development environment

Clone the framework packages next to this repository (the AbstractFramework `scripts/clone.sh`
does this) and install them editable into one virtual environment, so changes in a sibling
package are picked up without a release:

```sh
python3 -m venv .venv
.venv/bin/pip install -e ../abstractsemantics -e ../abstractcore -e ../abstractruntime \
                      -e ../abstractmemory
.venv/bin/pip install -e ../abstractgateway      # for gateway work: 0.15.11 or later
.venv/bin/pip install -e ".[dev]"
```

Prefer the sibling checkouts over released packages, so a change in a sibling is tested before it
is released. The checkouts must meet the floors in `pyproject.toml` and the gateway's own: AbstractGateway
0.15.11 needs AbstractRuntime 0.10.6 or later, so an older `abstractruntime` checkout cannot host the
gateway tests; install the released `abstractruntime` instead until it moves.

## Tests

```sh
.venv/bin/pytest -q                                # the Python suite (scripted models, no weights)
.venv/bin/python -m abstractnote.flows.build --check
cd web && npm install && npm test && npm run build # web app: vitest, type check, build
cd watch/apple && scripts/build.sh                 # watch app: package tests, simulator build, screenshots
```

- Python tests use scripted models and the built-in hashing embedder; they never load a model.
  The gateway tests run when `abstractgateway` is installed and are skipped otherwise.
- A test that spawns processes must give them a scratch `HOME`, `XDG_CONFIG_HOME` and `HF_HOME`.
- A scratch gateway for manual checks: `scripts/hermetic_gateway.sh start|status|stop` (ports
  18800-18899, scratch data directory, scripted model); see
  [docs/getting-started.md](docs/getting-started.md#try-it-on-a-private-scratch-gateway).
- Every check you add must fail when the thing it checks is removed. Delete it once and watch the
  test go red.

## Flows

The flow JSON in `src/abstractnote/flows/json/` is generated. Edit the generator
(`src/abstractnote/flows/build.py`, `graph.py`), the prompts (`src/abstractnote/process/prompts/`)
or the schemas (`src/abstractnote/process/schemas.py`), then regenerate and commit the JSON:

```sh
.venv/bin/python -m abstractnote.flows.build          # write
.venv/bin/python -m abstractnote.flows.build --check  # exit 1 when the JSON is stale
.venv/bin/abstractnote bundle --out dist/             # pack abstractnote@<version>.flow
```

Flows use catalog node types only. Every tool call is visible as its own nodes, and a tool's
failure ends the run with the tool's own sentence.

## Rules

- **No text heuristics for meaning.** Regular expressions or keyword lists never decide identity,
  intent or meaning. Deterministic code produces exact hashes and candidates; models decide
  ([ADR 0004](docs/adr/0004-models-decide-identity-no-text-heuristics.md)).
- **Tools stay bookkeeping.** Judgement lives in `llm_call` nodes with structured output; note
  tools hash, store, look up and apply ([ADR 0002](docs/adr/0002-flows-carry-cognition-tools-carry-bookkeeping.md)).
  Tools raise `ValueError` with one plain sentence on bad input and never log secrets.
- **Isolation.** Clients never send user, tenant or runtime identifiers; inside a gateway, tools
  never accept filesystem paths.
- **End-user copy.** In the web and watch apps: plain verbs; one-sentence errors; no system words
  such as ledger, run, gateway, workflow, record, and no ids; switches are labelled by the feature
  they control; actions sit at the bottom; nothing consequential happens without a tap. The web
  app's `test/copy.test.ts` checks the view strings.
- **Docs follow the code.** When you change behaviour, update the page that owns it (see
  [docs/README.md](docs/README.md)) and regenerate `llms.txt` and `llms-full.txt` in the same
  change. User-visible changes go in [CHANGELOG.md](CHANGELOG.md).

## Pull requests

Keep a pull request to one concern, include the tests that prove it, and say in the description
which documents changed. Report security issues privately as described in
[SECURITY.md](SECURITY.md), not in a pull request or issue.
