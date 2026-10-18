# AbstractNote for Apple Watch

Write a note from the wrist, ask your notes a question, and see what your notebook learned since
you last looked. The watch app is a thin client of your AbstractGateway: the gateway signs you in,
runs the AbstractNote flows and keeps the notebook; the watch only sends and shows.

It runs on its own (watch-only, no iPhone app needed), on watchOS 26 or later.

## Screens

Swipe up and down (or turn the Digital Crown) between four pages:

| Page | What it does |
|---|---|
| **Note** | Dictate, scribble or type a note and tap **Send**. The answer says how it was filed ("Saved as idea 'LUT budget'. Related: Dana, LUT."). Where you were is added when you allowed it. |
| **Ask** | Ask a question about your notes. The answer comes with the notes it rests on; tap one to read it. **Dig deeper** starts a longer search whose answer shows in Today. |
| **Today** | What changed since you last looked (new notes, new people and things, beliefs that changed, loops closed), what the notebook noticed for you, your open loops, a few prompts worth a note, and what you wrote on this day. Tap an item to acknowledge it, close it, or add a note about it. |
| **Settings** | The server and account you use, the Place switch, About, Sign out. |

Screenshots of every screen, made with the built-in sample notebook, come from `scripts/build.sh`
(see [BUILD.md](BUILD.md)).

## Signing in

Sign in with your user name and an account token, or with your registered email and the 8-digit
code the gateway emails you. Use a `user` account of your own, never an admin token. The sign-in
is kept in one protected file inside the app (see [docs/design.md](docs/design.md)); Sign out
deletes it.

## More

- [BUILD.md](BUILD.md): build, test, screenshots, launch arguments, install on a watch.
- [docs/design.md](docs/design.md): copy and layout rules, what the app sends, how it stores the sign-in.
- [docs/pairing.md](docs/pairing.md): pairing a physical watch for development.
- The gateway contract: [../../docs/clients.md](../../docs/clients.md) and [../../docs/api.md](../../docs/api.md).
