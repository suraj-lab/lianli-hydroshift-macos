// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "HydroShift",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "HydroShift", path: "Sources")
    ]
)
