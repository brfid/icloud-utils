// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "icloud-utils",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "icloud-materialize", targets: ["ICloudMaterialize"]),
        .library(name: "ICloudMaterializeCore", targets: ["ICloudMaterializeCore"]),
    ],
    targets: [
        .target(name: "ICloudMaterializeCore"),
        .executableTarget(
            name: "ICloudMaterialize",
            dependencies: ["ICloudMaterializeCore"]
        ),
    ]
)
