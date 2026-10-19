# AbstractNote for Apple Watch

Write a note from the wrist, ask your notes a question, and see what your notebook learned since
you last looked. The watch app is a thin client of your AbstractGateway: the gateway signs you in,
runs the AbstractNote flows and keeps the notebook; the watch only sends and shows.

It runs on its own (watch-only, no iPhone app needed), on watchOS 26 or later.

## Screens

Swipe up and down (or turn the Digital Crown) between four pages:

| Page | What it does |
|---|---|
| **Note** | Dictate, scribble or type a note, or tap the microphone and speak (a waveform and the time show while it records; 3 seconds of silence or Stop ends it). Your words fill the field, where you can correct them. **Send** sends the words; **Send as recording** also keeps the recording with the note. Then one state at a time: Sending, Received (a tap on the wrist), Organizing, Organized; the result card shows the note's kind, its title and the people and things it was linked to (tap one for its page). Where you were is added when you allowed it. |
| **Ask** | **Quick** (the default) lists the notes that match, at once; **Standard** gives one answer in sentences with its sources. Sources are short rows (title · date) that open the note. **Dig deeper** starts a longer search whose answer shows in Today. |
| **Today** | **Since you last looked** (counts, one line each), **Worth a thought** (one prompt at a time: Answer writes a note about it, Skip shows the next; tap it for why), **Themes** (the five largest groups your notes keep together; tap one for its main people and things), and your **Open loops** with Close. |
| **A person or thing** | Its name, kind and theme, a summary, when it was first and last noted, **How it evolved** (what was noted, learned, and no longer held, in order) and its notes. |
| **Settings** | **Server** (tap to change the address), **Account** (tap to sign in as someone else), the Place switch, About, and **Sign out** at the bottom (the server is kept for the next sign-in). |

Screenshots of every screen, made with the built-in sample notebook, come from `scripts/build.sh`
(see [BUILD.md](BUILD.md)).

## Signing in

With no saved sign-in the app opens on the sign-in screen. 
Sign in with your user name and an account token, or with your registered email and the 8-digit
code the gateway emails you. Use a `user` account of your own, never an admin token. The sign-in
is kept in one protected file inside the app (see [docs/design.md](docs/design.md)); Sign out
deletes it.

When something fails, the screen says what and offers the one useful action: "Sign in to your
notebook." (Sign in), "AbstractNote isn't set up on this server yet.", "Can't reach <server>." (Try
again), or the server's own sentence.

## More

- [BUILD.md](BUILD.md): build, test, screenshots, launch arguments, install on a watch.
- [docs/design.md](docs/design.md): copy and layout rules, what the app sends, how it stores the sign-in.
- [docs/pairing.md](docs/pairing.md): pairing a physical watch for development.
- The gateway contract: [../../docs/clients.md](../../docs/clients.md) and [../../docs/api.md](../../docs/api.md).
