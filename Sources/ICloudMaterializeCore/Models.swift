import Foundation

public let toolVersion = "1.1.0"
public let schemaVersion = "1.0"
public let defaultWorkerTimeoutSeconds: Double = 300
public let defaultCommandDeadlineSeconds: Double = 1_800
public let defaultReserveMiB: Int64 = 1_024
public let defaultDownloadAhead = 4

public enum CommandMode: String, Codable, Sendable {
    case check
    case fetch
}

public struct Options: Codable, Sendable {
    public var command: CommandMode
    public var json: Bool
    public var timeoutSeconds: Double
    public var deadlineSeconds: Double
    public var reserveMiB: Int64
    public var sha256: Bool
    public var downloadAhead: Int
    public var verbose: Bool
    public var allowICloudRoot: Bool
    public var paths: [String]

    public init(
        command: CommandMode,
        json: Bool = false,
        timeoutSeconds: Double = defaultWorkerTimeoutSeconds,
        deadlineSeconds: Double = defaultCommandDeadlineSeconds,
        reserveMiB: Int64 = defaultReserveMiB,
        sha256: Bool = false,
        downloadAhead: Int = defaultDownloadAhead,
        verbose: Bool = false,
        allowICloudRoot: Bool = false,
        paths: [String]
    ) {
        self.command = command
        self.json = json
        self.timeoutSeconds = timeoutSeconds
        self.deadlineSeconds = deadlineSeconds
        self.reserveMiB = reserveMiB
        self.sha256 = sha256
        self.downloadAhead = downloadAhead
        self.verbose = verbose
        self.allowICloudRoot = allowICloudRoot
        self.paths = paths
    }
}

public struct RootRecord: Codable, Hashable, Sendable {
    public var supplied: [String]
    public var resolved: String
    public var kind: String
    public var volumeDevice: UInt64

    public init(supplied: [String], resolved: String, kind: String, volumeDevice: UInt64) {
        self.supplied = supplied
        self.resolved = resolved
        self.kind = kind
        self.volumeDevice = volumeDevice
    }
}

public struct FailureRecord: Codable, Hashable, Sendable {
    public var path: String
    public var stage: String
    public var errorDomain: String
    public var errorCode: Int
    public var explanation: String

    public init(path: String, stage: String, errorDomain: String, errorCode: Int, explanation: String) {
        self.path = path
        self.stage = stage
        self.errorDomain = errorDomain
        self.errorCode = errorCode
        self.explanation = explanation
    }

    public init(path: String, stage: String, error: Error, explanation: String? = nil) {
        let ns = error as NSError
        let localized = (error as? LocalizedError)?.errorDescription
        self.init(
            path: path,
            stage: stage,
            errorDomain: ns.domain,
            errorCode: ns.code,
            explanation: explanation ?? localized ?? ns.localizedDescription
        )
    }
}

public struct ResultCounts: Codable, Equatable, Sendable {
    public var rootsRequested = 0
    public var rootsResolved = 0
    public var directories = 0
    public var regularFiles = 0
    public var uniqueFileObjects = 0
    public var datalessFiles = 0
    public var symlinksSkipped = 0
    public var specialFilesUnsupported = 0
    public var legacyPlaceholders = 0
    public var downloadRequests = 0
    public var downloadRequestFailures = 0
    public var filesVerified = 0
    public var filesVerifiedAfterRequest = 0
    public var filesMaterializedAfterRequest = 0
    public var requestedBytes: Int64 = 0
    public var readBytes: Int64 = 0
    public var hashedFiles = 0
    public var inventoryPasses = 0

    public init() {}

    public mutating func add(_ other: ResultCounts) {
        rootsRequested += other.rootsRequested
        rootsResolved += other.rootsResolved
        directories += other.directories
        regularFiles += other.regularFiles
        uniqueFileObjects += other.uniqueFileObjects
        datalessFiles += other.datalessFiles
        symlinksSkipped += other.symlinksSkipped
        specialFilesUnsupported += other.specialFilesUnsupported
        legacyPlaceholders += other.legacyPlaceholders
        downloadRequests += other.downloadRequests
        downloadRequestFailures += other.downloadRequestFailures
        filesVerified += other.filesVerified
        filesVerifiedAfterRequest += other.filesVerifiedAfterRequest
        filesMaterializedAfterRequest += other.filesMaterializedAfterRequest
        requestedBytes += other.requestedBytes
        readBytes += other.readBytes
        hashedFiles += other.hashedFiles
        inventoryPasses += other.inventoryPasses
    }
}

public struct FreshnessCounts: Codable, Equatable, Sendable {
    public var current = 0
    public var stale = 0
    public var unavailable = 0
    public var unknown = 0
    public var notApplicable = 0

    public init() {}

    public mutating func add(_ other: FreshnessCounts) {
        current += other.current
        stale += other.stale
        unavailable += other.unavailable
        unknown += other.unknown
        notApplicable += other.notApplicable
    }
}

public struct HashRecord: Codable, Hashable, Sendable {
    public var path: String
    public var sha256: String

    public init(path: String, sha256: String) {
        self.path = path
        self.sha256 = sha256
    }
}

public struct DatalessRecord: Codable, Hashable, Sendable {
    public var path: String
    public var logicalBytes: Int64
    public var freshness: String

    public init(path: String, logicalBytes: Int64, freshness: String) {
        self.path = path
        self.logicalBytes = logicalBytes
        self.freshness = freshness
    }
}

public struct ResultDocument: Codable, Sendable {
    public var toolVersion = ICloudMaterializeCore.toolVersion
    public var schemaVersion = ICloudMaterializeCore.schemaVersion
    public var command: CommandMode
    public var success: Bool
    public var enumerationComplete: Bool
    public var verificationScope: String
    public var roots: [RootRecord]
    public var counts: ResultCounts
    public var freshness: FreshnessCounts
    public var datalessFiles: [DatalessRecord]
    public var hashes: [HashRecord]
    public var failures: [FailureRecord]
    public var timedOut: Bool
    public var interruptedBySignal: Int32?
    public var hydrationAttribution: String
    public var observations: [String]

    public init(command: CommandMode) {
        self.command = command
        self.success = false
        self.enumerationComplete = false
        self.verificationScope = command == .check
            ? "metadata inventory only; payload readability not verified"
            : "every included regular file read to EOF"
        self.roots = []
        self.counts = ResultCounts()
        self.freshness = FreshnessCounts()
        self.datalessFiles = []
        self.hashes = []
        self.failures = []
        self.timedOut = false
        self.interruptedBySignal = nil
        self.hydrationAttribution = "Explicit download requests and observed post-request materialization are reported separately; causation is not asserted."
        self.observations = [
            "Results describe the device-visible tree at the completed inventory passes, not an atomic snapshot or proof of server synchronization."
        ]
    }

    public mutating func merge(_ other: ResultDocument) {
        roots.append(contentsOf: other.roots)
        counts.add(other.counts)
        freshness.add(other.freshness)
        datalessFiles.append(contentsOf: other.datalessFiles)
        hashes.append(contentsOf: other.hashes)
        failures.append(contentsOf: other.failures)
        timedOut = timedOut || other.timedOut
        interruptedBySignal = interruptedBySignal ?? other.interruptedBySignal
        enumerationComplete = enumerationComplete && other.enumerationComplete
        for observation in other.observations where !observations.contains(observation) {
            observations.append(observation)
        }
    }
}

enum WorkerKind: String, Codable {
    case resolve
    case operate
}

struct WorkerRequest: Codable {
    var kind: WorkerKind
    var options: Options
    var root: RootRecord?
}

struct WorkerReply: Codable {
    var roots: [RootRecord] = []
    var result: ResultDocument?
    var failures: [FailureRecord] = []
}

struct UsageError: LocalizedError, CustomStringConvertible {
    var description: String
    var errorDescription: String? { description }
    init(_ description: String) { self.description = description }
}

func jsonData<T: Encodable>(_ value: T, pretty: Bool = false) throws -> Data {
    let encoder = JSONEncoder()
    encoder.outputFormatting = pretty ? [.prettyPrinted, .sortedKeys] : [.sortedKeys]
    return try encoder.encode(value)
}
