import Darwin
import Foundation

enum FreshnessState: String, Sendable {
    case current
    case stale
    case unavailable
    case unknown
    case notApplicable
}

struct StatSignature: Equatable, Hashable, Sendable {
    var device: UInt64
    var inode: UInt64
    var mode: UInt32
    var size: Int64
    var modificationSeconds: Int64
    var modificationNanoseconds: Int64
    var changeSeconds: Int64
    var changeNanoseconds: Int64
    var dataless: Bool

    init(_ info: stat) {
        device = UInt64(info.st_dev)
        inode = UInt64(info.st_ino)
        mode = UInt32(info.st_mode)
        size = Int64(info.st_size)
        modificationSeconds = Int64(info.st_mtimespec.tv_sec)
        modificationNanoseconds = Int64(info.st_mtimespec.tv_nsec)
        changeSeconds = Int64(info.st_ctimespec.tv_sec)
        changeNanoseconds = Int64(info.st_ctimespec.tv_nsec)
        dataless = (UInt32(info.st_flags) & UInt32(SF_DATALESS)) != 0
    }

    var stableKey: String {
        [device, inode, UInt64(mode), UInt64(bitPattern: size),
         UInt64(bitPattern: modificationSeconds), UInt64(bitPattern: modificationNanoseconds),
         UInt64(bitPattern: changeSeconds), UInt64(bitPattern: changeNanoseconds),
         dataless ? 1 : 0].map(String.init).joined(separator: ":")
    }

    func sameContentMetadata(as other: StatSignature) -> Bool {
        device == other.device && inode == other.inode &&
            (mode & UInt32(S_IFMT)) == (other.mode & UInt32(S_IFMT)) &&
            size == other.size && modificationSeconds == other.modificationSeconds &&
            modificationNanoseconds == other.modificationNanoseconds &&
            changeSeconds == other.changeSeconds && changeNanoseconds == other.changeNanoseconds
    }
}

struct Node: Sendable {
    enum Kind: String, Sendable {
        case directory
        case regular
        case symlink
        case special
    }

    var relativeComponents: [String]
    var path: String
    var kind: Kind
    var stat: StatSignature
    var freshness: FreshnessState
    var ubiquitous: Bool
    var legacyPlaceholder: Bool

    var stableKey: String {
        "\(relativeComponents.joined(separator: "/"))\u{0}\(kind.rawValue)\u{0}\(stat.stableKey)"
    }
}

struct Inventory: Sendable {
    var nodes: [Node]
    var failures: [FailureRecord]
    var complete: Bool

    var stableKey: [String] {
        nodes.map(\.stableKey).sorted()
    }

    var regularFiles: [Node] {
        nodes.filter { $0.kind == .regular }.sorted { $0.path < $1.path }
    }
}

struct Verification: Sendable {
    var path: String
    var stat: StatSignature
    var bytes: Int64
    var hash: String?
}

struct FileChanged: LocalizedError, CustomStringConvertible {
    var description: String
    var errorDescription: String? { description }
}

