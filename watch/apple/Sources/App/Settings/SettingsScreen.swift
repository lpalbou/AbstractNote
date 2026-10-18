import SwiftUI

/// Where the notebook is, who is signed in, the place switch, About, and Sign out at the bottom.
struct SettingsScreen: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var location: Location

    var body: some View {
        FitScroll {
            VStack(spacing: 6) {
                Card {
                    VStack(alignment: .leading, spacing: 1) {
                        Text("Server").font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                        Text(model.gatewayHost).font(.rounded(.footnote)).foregroundStyle(Theme.text).lineLimit(1).minimumScaleFactor(0.7)
                    }
                }
                .accessibilityElement(children: .combine)
                Card {
                    VStack(alignment: .leading, spacing: 1) {
                        Text("Account").font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                        Text(model.account ?? "—").font(.rounded(.footnote)).foregroundStyle(Theme.text).lineLimit(1)
                    }
                }
                .accessibilityElement(children: .combine)
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
}
