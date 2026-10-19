# Security Policy

AbstractNote keeps personal notes, files and the knowledge drawn from them. Please report
vulnerabilities privately so they can be fixed before public disclosure.

## Reporting a vulnerability

Do **not** open a public issue for a security report. Use GitHub's private **Report a
vulnerability** form on the AbstractNote repository, or write to contact@abstractcore.ai.

Please include:

- a description of the issue and its impact;
- the affected component (core library, flows, gateway integration, web app, watch app, importers)
  and file paths if known;
- steps to reproduce or a minimal proof of concept;
- the version (`abstractnote status` shows it) and your environment;
- a suggested fix, if you have one.

We acknowledge reports as promptly as we can and agree a disclosure timeline with you.

## Supported versions

AbstractNote is in development (0.1.0, not yet published). Security fixes go to the `main`
branch.

## Security model

These are the guarantees the design makes; a way around any of them is a vulnerability.

- **Per-user isolation.** Inside AbstractGateway every request is authenticated by the gateway
  and routed to the caller's own data plane; the notebook lives under that plane. Clients never
  send user, tenant or runtime identifiers, and an id from another notebook answers 404.
- **No filesystem paths inside the gateway.** The note tools read attachments only from files
  uploaded through the gateway (`{"$artifact": id}` from the host's artifact store). A path in a
  capture is refused. Headless use accepts paths only inside a folder the caller allows
  explicitly.
- **Auto-approved tools touch only your notebook.** The note tools are auto-approved because each
  one is bound to the caller's notebook and to nothing else.
- **Accounts.** Clients sign in with a `user` account token or an emailed one-time code; admin
  tokens are not meant for clients. The note tools never print or log secrets.
- **Importers read, never write, their sources.** The agora importer opens the hub database
  read-only; importers copy nothing outside the notebook.
- **Apple clients** keep the sign-in in one protected file in the app container (Data Protection,
  excluded from backup, deleted on sign-out), not in the OS keychain. This storage is proposed and
  awaits a ruling ([ADR 0006](docs/adr/0006-apple-client-credential-storage.md)).

## What you should protect

- The notebook is plain SQLite files and attachment blobs on disk, not encrypted by AbstractNote.
  Protect the gateway's data directory (or your `--notebook` directory) with the operating
  system's file permissions and disk encryption.
- Imports can bring private material (sessions, messages, documents) into a notebook. Import
  only what the notebook's owner may hold.
