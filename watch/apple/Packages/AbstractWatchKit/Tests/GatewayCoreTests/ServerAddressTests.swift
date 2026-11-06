import Foundation
import Testing
@testable import GatewayCore

@Suite struct ServerAddressTests {
    @Test(arguments: [
        ("http://192.168.1.175:18080", "http://192.168.1.175:18080"),
        ("https://notes.example.org:8443/mount", "notes.example.org:8443/mount"),
        ("https://notes.example.org:443/", "notes.example.org"),
        ("http://[::1]:18080/", "http://[::1]:18080"),
        ("https://user:secret@notes.example.org:8443/mount?token=secret#private", "notes.example.org:8443/mount"),
    ])
    func connectionLabelRetainsAuthorityWithoutSecrets(input: String, expected: String) throws {
        let url = try #require(URL(string: input))
        #expect(ServerAddress.label(url) == expected)
        #expect(Problem.of(URLError(.cannotConnectToHost), host: ServerAddress.label(url)).sentence == "Can't reach \(expected).")
    }
}
