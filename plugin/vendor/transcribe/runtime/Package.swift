// swift-tools-version: 6.2
import PackageDescription
let package = Package(
    name: "TranscribeAudio", platforms: [.macOS(.v14)],
    dependencies: [.package(url: "https://github.com/FluidInference/FluidAudio.git", revision: "b68f484789d81fda21efbf81e2ca9fcfd9dc22aa", traits: [])],
    targets: [.executableTarget(name: "TranscribeAudio", dependencies: [.product(name: "FluidAudio", package: "FluidAudio")])]
)
