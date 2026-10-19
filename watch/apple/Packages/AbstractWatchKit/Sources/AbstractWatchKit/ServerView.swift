#if os(watchOS)
import GatewayCore
import SwiftUI

/// Settings → Server: the address of the server this watch talks to, editable. The sign-in rules
/// (`SavedSignIn.normalize`): `https://` is added when no scheme is typed; plain `http://` only when
/// typed, and the watch allows it on the local network only. A credential belongs to its server, so
/// saving a different server signs out and opens the sign-in for it (`onChange`).
public struct ServerView: View {
    let current: URL?
    let onChange: (URL) -> Void
    @State private var text: String
    @State private var error: String?
    @Environment(\.dismiss) private var dismiss

    public init(current: URL?, onChange: @escaping (URL) -> Void) {
        self.current = current; self.onChange = onChange
        _text = State(initialValue: current?.absoluteString
                      ?? UserDefaults.standard.string(forKey: SignInDefaults.lastGateway) ?? SavedSignIn.defaultGateway)
    }

    public var body: some View {
        FitScroll {
            VStack(alignment: .leading, spacing: 8) {
                Text("Server address").font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                TextField("gateway.example.org", text: $text)
                    .textContentType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .font(.rounded(.footnote))
                    .accessibilityLabel("Server address")
                    .accessibilityIdentifier("server.address")
                Text("https is used unless you type http://, which works on your local network only.")
                    .font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                if changes {
                    Text("Changing the server signs you out.").font(.rounded(.caption2)).foregroundStyle(Theme.amber)
                }
                if let error { ErrorLine(text: error) }
                Spacer(minLength: 6)
                Button("Save") { save() }
                    .buttonStyle(FilledButtonStyle(fill: Theme.indigoButton))
                    .disabled(text.trimmingCharacters(in: .whitespaces).isEmpty)
                    .accessibilityIdentifier("server.save")
            }
            .padding(.horizontal, 2)
        }
        .screen("Server")
    }

    private var changes: Bool { SavedSignIn.normalize(text).map { $0 != current } ?? false }

    private func save() {
        guard let url = SavedSignIn.normalize(text) else {
            error = "Type an address like gateway.example.org."
            return
        }
        if url == current { dismiss(); return }
        UserDefaults.standard.set(url.absoluteString, forKey: SignInDefaults.lastGateway)
        onChange(url)
    }
}
#endif
