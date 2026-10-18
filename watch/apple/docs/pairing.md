# Pairing a physical Apple Watch

Pairing a watch for development is the same for every AbstractFramework watch app. Follow the
AbstractAssistant guide, written and tested with watchOS 27, Xcode 27.1 and a Series 11:
[abstractassistant/watch/apple/docs/pairing.md](../../../../abstractassistant/watch/apple/docs/pairing.md)
(in the AbstractAssistant repository: `watch/apple/docs/pairing.md`).

Then install AbstractNote with `scripts/deploy-watch.sh --gateway <url> --token <token>`
([BUILD.md](../BUILD.md)). The bundle id is `ai.abstractframework.note.watch`; signing uses the
team in `project.yml` and `-allowProvisioningUpdates`.
