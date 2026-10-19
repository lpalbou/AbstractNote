import AbstractWatchKit
import SwiftUI

/// Where the notebook is (tap: change the server), who is signed in (tap: sign in as someone
/// else), the place switch, About, and Sign out at the bottom.
struct SettingsScreen: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var location: Location

    var body: some View {
        FitScroll {
            VStack(spacing: 6) {
                row("Server", model.gatewayHost, route: .server, hint: "Change the server", id: "settings.server")
                row("Account", model.account ?? (model.checkingAccount ? "Checking…" : "Not known"), route: .signIn,
                    hint: "Sign in as someone else", id: "settings.account")
                Card {
                    VStack(alignment: .leading, spacing: 2) {
                        // The switch is labelled by its feature and shows its state.
                        Toggle("Place", isOn: $location.enabled)
                            .font(.rounded(.footnote, weight: .semibold)).foregroundStyle(Theme.text).tint(Theme.indigoButton)
                            .accessibilityHint("Adds where you were to the notes you send")
                        Text(location.status).font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                    }
                }
                NavigationLink(value: Route.about) {
                    Card {
                        HStack {
                            Text("About").font(.rounded(.footnote, weight: .semibold)).foregroundStyle(Theme.text)
                            Spacer(minLength: 4)
                            Text(AppIdentity.version).font(.rounded(.footnote)).foregroundStyle(Theme.muted)
                            Image(systemName: "chevron.right").font(.caption2).foregroundStyle(Theme.muted)
                        }
                    }
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("settings.about")
                Spacer(minLength: 10)
                Button("Sign out", role: .destructive) { model.signOut() }
                    .buttonStyle(FilledButtonStyle(fill: Theme.neutralButton, foreground: Theme.red))
                    .accessibilityHint("Forgets this account on the watch")
                    .accessibilityIdentifier("settings.signout")
            }
        }
        .screen("Settings")
    }

    private func row(_ label: String, _ value: String, route: Route, hint: String, id: String) -> some View {
        NavigationLink(value: route) {
            Card {
                HStack {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(label).font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                        Text(value).font(.rounded(.footnote)).foregroundStyle(Theme.text).lineLimit(1).minimumScaleFactor(0.7)
                    }
                    Spacer(minLength: 4)
                    Image(systemName: "chevron.right").font(.caption2).foregroundStyle(Theme.muted)
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint(hint)
        .accessibilityIdentifier(id)
    }
}
