// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "AbstractGatewayClient",
    platforms: [.watchOS(.v26), .iOS(.v26), .macOS(.v26)],
    products: [
        .library(name: "AbstractGatewayClient", targets: ["AbstractGatewayClient"]),
    ],
    dependencies: [
        .package(path: "../AbstractWatchKit"),
    ],
    targets: [
        .target(name: "AbstractGatewayClient", dependencies: [.product(name: "GatewayCore", package: "AbstractWatchKit")]),
        .testTarget(name: "AbstractGatewayClientTests", dependencies: ["AbstractGatewayClient"],
                    resources: [.copy("Fixtures")]),
    ]
)
