import Darwin
import Foundation

private let usage = "Usage: icloud-fixture-helper wait-upload|evict|status PATH... | find-dataless ROOT"

private func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

private func values(_ path: String) throws -> URLResourceValues {
    try URL(fileURLWithPath: path).resourceValues(forKeys: [
        .isUbiquitousItemKey,
        .ubiquitousItemIsUploadedKey,
        .ubiquitousItemUploadingErrorKey,
        .ubiquitousItemDownloadingStatusKey,
    ])
}

private func status(_ path: String) throws -> String {
    var info = stat()
    if lstat(path, &info) != 0 {
        throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno),
                      userInfo: [NSLocalizedDescriptionKey: String(cString: strerror(errno))])
    }
    let resource = try values(path)
    let dataless = (UInt32(info.st_flags) & UInt32(SF_DATALESS)) != 0
    let download: String
    switch resource.ubiquitousItemDownloadingStatus {
    case .current?: download = "current"
    case .downloaded?: download = "downloaded"
    case .notDownloaded?: download = "not-downloaded"
    default: download = "unknown"
    }
    return "\(path)\tdataless=\(dataless)\tubiquitous=\(resource.isUbiquitousItem == true)\tuploaded=\(resource.ubiquitousItemIsUploaded == true)\tdownload=\(download)"
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard arguments.count >= 2 else { fail(usage) }
let mode = arguments[0]
let paths = Array(arguments.dropFirst())
let previous = getiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES, IOPOL_SCOPE_PROCESS)
guard previous != -1,
      setiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES, IOPOL_SCOPE_PROCESS,
                     IOPOL_MATERIALIZE_DATALESS_FILES_OFF) == 0 else {
    fail("could not disable automatic materialization for fixture metadata operations")
}
defer { _ = setiopolicy_np(IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES, IOPOL_SCOPE_PROCESS, previous) }

do {
    switch mode {
    case "find-dataless":
        guard paths.count == 1 else { fail(usage) }
        let root = URL(fileURLWithPath: paths[0], isDirectory: true)
        var inspected = 0
        var found: String?
        let deadline = ProcessInfo.processInfo.systemUptime + 15
        let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: nil,
            options: [.skipsPackageDescendants],
            errorHandler: { url, error in
                FileHandle.standardError.write(Data("metadata refusal at \(url.path): \(error.localizedDescription)\n".utf8))
                return false
            }
        )
        while let url = enumerator?.nextObject() as? URL {
            inspected += 1
            var info = stat()
            if lstat(url.path, &info) == 0,
               info.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG),
               (UInt32(info.st_flags) & UInt32(SF_DATALESS)) != 0 {
                found = url.path
                break
            }
            if inspected >= 1_000 || ProcessInfo.processInfo.systemUptime >= deadline { break }
        }
        guard let found else {
            fail("no data-less regular file found within \(inspected) metadata entries and the 15-second bound")
        }
        print(found)
    case "status":
        for path in paths { print(try status(path)) }
    case "wait-upload":
        let deadline = ProcessInfo.processInfo.systemUptime + 120
        while true {
            var ready = true
            for path in paths {
                let resource = try values(path)
                if let error = resource.ubiquitousItemUploadingError { throw error }
                ready = ready && resource.isUbiquitousItem == true && resource.ubiquitousItemIsUploaded == true
            }
            if ready { break }
            if ProcessInfo.processInfo.systemUptime >= deadline {
                fail("fixture did not report uploaded within 120 seconds")
            }
            usleep(500_000)
        }
        for path in paths { print(try status(path)) }
    case "evict":
        for path in paths {
            let resource = try values(path)
            guard resource.isUbiquitousItem == true, resource.ubiquitousItemIsUploaded == true else {
                fail("refusing to evict before upload readiness: \(path)")
            }
            try FileManager.default.evictUbiquitousItem(at: URL(fileURLWithPath: path))
        }
        for path in paths { print(try status(path)) }
    default:
        fail(usage)
    }
} catch {
    fail("fixture helper failed: \((error as NSError).localizedDescription)")
}
