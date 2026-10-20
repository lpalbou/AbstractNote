# @abstractframework/note

The AbstractNote web app: capture notes and files, remember what changed since you last looked,
ask by effort (quick, standard, deep), and explore how people, projects and ideas connect through
time. Tabs: Capture, Remember, Explore and Processing (what the notebook is doing with your notes, live:
the note being understood and its steps, the pace and the time left, the waiting notes, what was just
understood, what needs attention, and the consolidation), with a Settings panel. Explore opens on an Overview of what
the notebook holds (numbers, the activity line, themes, what went quiet) and shows it as a Picture or a List.

It talks to an AbstractGateway that has the AbstractNote integration: install
`pip install "abstractnote[gateway]"` (AbstractGateway 0.15.11 or later) in the gateway's
environment. The gateway's app catalog installs this package and serves it at `/apps/note/`.

To run it yourself:

```sh
npx @abstractframework/note --gateway-url http://127.0.0.1:8080
```

It serves on `http://127.0.0.1:3006/` (`--port` and `--host` change that). Sign in with a user
account of that gateway. Without `--gateway-url` it uses the gateway installed on this computer.

Documentation: [AbstractNote on GitHub](https://github.com/lpalbou/AbstractNote) (getting started,
clients, troubleshooting). License: MIT.
