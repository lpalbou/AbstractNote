import SwiftUI

/// About, as in every AbstractFramework app: this app's version and build, then the framework's
/// facts (author, copyright, website, contact) read from the framework's identity descriptor
/// bundled with the app (`abstractframework_identity.json`, the file AbstractCore ships), so none
/// of them is typed here. The descriptor has no `abstractnote` entry yet, so no app links show.
struct AboutView: View {
    private let identity = AppIdentity.load()

    var body: some View {
        ScrollView {
            VStack(spacing: 6) {
                NoteMark(size: 40)
                Text("AbstractNote").font(.rounded(.headline, weight: .bold)).foregroundStyle(Theme.text)
                Text("for Apple Watch").font(.rounded(.caption2)).foregroundStyle(Theme.muted)
                Text("Version \(AppIdentity.version) (\(AppIdentity.build))")
                    .font(.rounded(.footnote, weight: .semibold)).foregroundStyle(Theme.cyan).padding(.bottom, 6)
                if let id = identity {
                    VStack(alignment: .leading, spacing: 8) {
                        row("Part of", id.framework.name)
                        row("Author", "\(id.framework.author) (\(id.framework.years))")
                        row("Copyright", id.framework.copyright)
                        row("Website", id.framework.website.replacingOccurrences(of: "https://", with: ""))
                        row("Contact", id.framework.contactEmail)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    ErrorLine(text: "The framework's identity file is missing from this build.")
                }
            }
            .padding(.horizontal, 4)
        }
        .screen("About")
    }

    private func row(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.rounded(.caption2)).foregroundStyle(Theme.muted)
            Text(value).font(.rounded(.footnote)).foregroundStyle(Theme.text)
        }
        .accessibilityElement(children: .combine)
    }
}

/// The framework's identity descriptor (`abstractframework.identity.v1`): the framework's facts.
struct AppIdentity: Decodable {
    struct Framework: Decodable {
        let name, website, author, years, license, copyright, contactEmail: String
        enum CodingKeys: String, CodingKey { case name, website, author, years, license, copyright, contactEmail = "contact_email" }
    }
    let framework: Framework

    static func load() -> AppIdentity? {
        guard let url = Bundle.main.url(forResource: "abstractframework_identity", withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(AppIdentity.self, from: data)
    }

    static var version: String { Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?" }
    static var build: String { Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "?" }
}

/// The app's mark: a page with lines, in the framework's cyan and indigo.
struct NoteMark: View {
    var size: CGFloat = 44
    var body: some View {
        ZStack {
            Circle().fill(LinearGradient(colors: [Theme.indigo, Theme.cyan], startPoint: .topLeading, endPoint: .bottomTrailing))
            Image(systemName: "note.text").font(.system(size: size * 0.48, weight: .semibold)).foregroundStyle(Theme.ground)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
