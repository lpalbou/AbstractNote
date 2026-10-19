# @abstractframework/note

The AbstractNote web app: capture notes and files, remember what changed since you last looked,
ask by effort (quick, standard, deep), and explore how people, projects and ideas connect through
time. Tabs: Capture, Remember and Explore, with a Settings panel.

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
