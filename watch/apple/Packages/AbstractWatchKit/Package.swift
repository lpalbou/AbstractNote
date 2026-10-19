// swift-tools-version: 6.2
import PackageDescription

// The parts of an AbstractFramework Apple Watch app that do not depend on the app: the gateway
// client core (`GatewayCore`, Foundation only, tested on macOS) and the shared watch UI
// (`AbstractWatchKit`: sign-in, server screen, microphone, theme, error sentences). Candidate for
// abstractuic/swift/AbstractWatchKit, consumed by the AbstractNote and AbstractAssistant watch apps.
let package = Package(
    name: "AbstractWatchKit",
    platforms: [.watchOS(.v26), .iOS(.v26), .macOS(.v26)],
    products: [
        .library(name: "GatewayCore", targets: ["GatewayCore"]),
        .library(name: "AbstractWatchKit", targets: ["AbstractWatchKit"]),
    ],
    targets: [
        .target(name: "GatewayCore"),
        // watchOS only (each file is `#if os(watchOS)`), so `swift test` on a Mac builds it empty.
        .target(name: "AbstractWatchKit", dependencies: ["GatewayCore"]),
        .testTarget(name: "GatewayCoreTests", dependencies: ["GatewayCore"]),
    ]
)
