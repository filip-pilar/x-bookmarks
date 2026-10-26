import Foundation
import FluidAudio
import CoreML

struct Recognition: Codable {
    let text: String
    let tokenTimings: [TokenTiming]
    let words: [WordTiming]
    let duration: Double
}
struct SpeakerSegment: Codable {
    let speaker: String
    let start: Float
    let end: Float
}
@main struct TranscribeAudio {
    static func main() async throws {
        let args = CommandLine.arguments
        guard args.count == 5 else {
            throw NSError(domain: "TranscribeAudio", code: 2, userInfo: [NSLocalizedDescriptionKey: "Usage: TranscribeAudio asr|speakers input.wav output.json model-cache"])
        }
        let input = URL(fileURLWithPath: args[2])
        let output = URL(fileURLWithPath: args[3])
        let cache = URL(fileURLWithPath: args[4])
        let configuration = MLModelConfiguration()
        configuration.computeUnits = .cpuAndNeuralEngine
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data: Data
        if args[1] == "asr" {
            let manager = UnifiedAsrManager(configuration: configuration, encoderPrecision: .int8)
            try await manager.loadModels(to: cache)
            let samples = try AudioConverter().resampleAudioFile(input)
            let result = try await manager.transcribeWithTimings(samples)
            data = try encoder.encode(Recognition(text: result.text, tokenTimings: result.tokenTimings,
                words: buildWordTimings(from: result.tokenTimings), duration: Double(samples.count) / 16000))
        } else if args[1] == "speakers" {
            let manager = OfflineDiarizerManager()
            try await manager.prepareModels(directory: cache.appendingPathComponent("diarizer"), configuration: configuration)
            let result = try await manager.process(input)
            data = try encoder.encode(result.segments.map { SpeakerSegment(speaker: $0.speakerId, start: $0.startTimeSeconds, end: $0.endTimeSeconds) })
        } else {
            throw NSError(domain: "TranscribeAudio", code: 2, userInfo: [NSLocalizedDescriptionKey: "Unknown mode"])
        }
        try data.write(to: output, options: .atomic)
    }
}
