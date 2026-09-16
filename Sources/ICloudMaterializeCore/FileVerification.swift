import CryptoKit
import Darwin
import Foundation

#if DEBUG
nonisolated(unsafe) var injectedFreeSpaceCalls = 0
#endif

func immediatelyFreeBytes(at path: String) throws -> Int64 {
    #if DEBUG
    if ProcessInfo.processInfo.environment["ICMAT_TEST_SPACE_DURING"] == "1" {
        injectedFreeSpaceCalls += 1
        return injectedFreeSpaceCalls == 1 ? Int64.max : 0
    }
    if let value = ProcessInfo.processInfo.environment["ICMAT_TEST_FREE_BYTES"], let injected = Int64(value) {
        return injected
    }
    #endif
    var info = statfs()
    if statfs(path, &info) != 0 { throw posixError("query immediately free capacity") }
    let blocks = max(Int64(0), Int64(info.f_bavail))
    let size = max(Int64(0), Int64(info.f_bsize))
    let multiplied = blocks.multipliedReportingOverflow(by: size)
    return multiplied.overflow ? Int64.max : multiplied.partialValue
}

func reserveBytes(_ options: Options) -> Int64 {
    let value = options.reserveMiB.multipliedReportingOverflow(by: 1_048_576)
    return value.overflow ? Int64.max : value.partialValue
}

func requiresRequest(_ node: Node) -> Bool {
    node.stat.dataless || node.freshness == .stale || node.freshness == .unavailable
}

func preflightSpace(_ inventory: Inventory, root: RootRecord, options: Options) throws {
    let demand = inventory.regularFiles.filter(requiresRequest).reduce(Int64(0)) { partial, node in
        let result = partial.addingReportingOverflow(max(0, node.stat.size))
        return result.overflow ? Int64.max : result.partialValue
    }
    let required = reserveBytes(options).addingReportingOverflow(demand)
    let threshold = required.overflow ? Int64.max : required.partialValue
    let free = try immediatelyFreeBytes(at: root.resolved)
    if free < threshold {
        throw NSError(
            domain: NSPOSIXErrorDomain,
            code: Int(ENOSPC),
            userInfo: [NSLocalizedDescriptionKey:
                "\(free) immediately free bytes are below \(demand) known download bytes plus the \(reserveBytes(options)) byte reserve"]
        )
    }
}

func checkRequestSpace(_ node: Node, options: Options) throws {
    let free = try immediatelyFreeBytes(at: node.path)
    let threshold = reserveBytes(options).addingReportingOverflow(max(0, node.stat.size))
    if free < (threshold.overflow ? Int64.max : threshold.partialValue) {
        throw NSError(domain: NSPOSIXErrorDomain, code: Int(ENOSPC),
                      userInfo: [NSLocalizedDescriptionKey: "free space fell below this file's known size plus reserve"])
    }
}

func requestDownload(_ node: Node) throws {
    var currentInfo = stat()
    if lstat(node.path, &currentInfo) != 0 { throw posixError("inspect download candidate") }
    let current = StatSignature(currentInfo)
    guard kind(for: currentInfo) == .regular,
          current.device == node.stat.device, current.inode == node.stat.inode else {
        throw FileChanged(description: "download candidate identity or type changed after inventory")
    }
    // A request gives iCloud the opportunity to download; only the later complete
    // coordinated read establishes local readability.
    #if DEBUG
    if ProcessInfo.processInfo.environment["ICMAT_TEST_DOWNLOAD_FAILURE"] == "1" {
        throw NSError(domain: NSCocoaErrorDomain, code: NSFileReadUnknownError,
                      userInfo: [NSLocalizedDescriptionKey: "injected download-request failure"])
    }
    if ProcessInfo.processInfo.environment["ICMAT_TEST_FAKE_DOWNLOAD_REQUEST"] == "1" { return }
    #endif
    try FileManager.default.startDownloadingUbiquitousItem(at: URL(fileURLWithPath: node.path))
}

private func openFile(root: RootRecord, rootFD: Int32?, relative: [String]) throws -> Int32 {
    if root.kind == "regular-file" {
        let descriptor = open(root.resolved, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        if descriptor < 0 { throw posixError("open regular-file root") }
        return descriptor
    }
    guard let rootFD, let final = relative.last else {
        throw NSError(domain: NSPOSIXErrorDomain, code: Int(EINVAL),
                      userInfo: [NSLocalizedDescriptionKey: "invalid relative file path"])
    }
    let parent = try openDirectory(rootFD: rootFD, relative: Array(relative.dropLast()))
    defer { Darwin.close(parent) }
    let descriptor = final.withCString { openat(parent, $0, O_RDONLY | O_NOFOLLOW | O_CLOEXEC) }
    if descriptor < 0 { throw posixError("open regular file") }
    return descriptor
}

private func streamRead(
    root: RootRecord,
    rootFD: Int32?,
    node: Node,
    coordinatedURL: URL,
    requested: Bool,
    hashing: Bool
) throws -> Verification {
    #if DEBUG
    if ProcessInfo.processInfo.environment["ICMAT_TEST_FILE_CHANGE_FAILURE"] == "1" {
        throw FileChanged(description: "injected file replacement during both bounded attempts")
    }
    #endif
    let supplied = coordinatedURL.standardizedFileURL.path
    guard supplied == URL(fileURLWithPath: node.path).standardizedFileURL.path else {
        throw NSError(domain: NSCocoaErrorDomain, code: NSFileReadInvalidFileNameError,
                      userInfo: [NSLocalizedDescriptionKey: "file coordinator supplied a URL outside the requested path"])
    }
    let descriptor = try openFile(root: root, rootFD: rootFD, relative: node.relativeComponents)
    defer { Darwin.close(descriptor) }
    var beforeInfo = stat()
    if fstat(descriptor, &beforeInfo) != 0 { throw posixError("inspect opened file") }
    let before = StatSignature(beforeInfo)
    guard (before.mode & UInt32(S_IFMT)) == UInt32(S_IFREG),
          before.device == node.stat.device, before.inode == node.stat.inode else {
        throw FileChanged(description: "file identity or type changed before reading")
    }
    if !requested && !before.sameContentMetadata(as: node.stat) {
        throw FileChanged(description: "file metadata changed before reading")
    }

    #if DEBUG
    let environment = ProcessInfo.processInfo.environment
    if let marker = environment["ICMAT_TEST_READ_MARKER"],
       let suffix = environment["ICMAT_TEST_READ_MARKER_SUFFIX"],
       node.path.hasSuffix(suffix) {
        FileManager.default.createFile(atPath: marker, contents: Data())
    }
    if let delay = environment["ICMAT_TEST_READ_DELAY_MILLIS"],
       let milliseconds = useconds_t(delay) {
        usleep(milliseconds * 1_000)
    }
    if environment["ICMAT_TEST_BLOCK_STAGE"] == "read" {
        sleep(60)
    }
    #endif

    var hasher = SHA256()
    var buffer = [UInt8](repeating: 0, count: 1_048_576)
    var total: Int64 = 0
    while true {
        let amount = Darwin.read(descriptor, &buffer, buffer.count)
        if amount < 0 {
            if errno == EINTR { continue }
            throw posixError("read file payload")
        }
        if amount == 0 { break }
        total += Int64(amount)
        if hashing {
            hasher.update(data: Data(buffer[0..<Int(amount)]))
        }
    }

    var afterInfo = stat()
    if fstat(descriptor, &afterInfo) != 0 { throw posixError("reinspect opened file") }
    let after = StatSignature(afterInfo)
    guard before.sameContentMetadata(as: after), total == after.size else {
        throw FileChanged(description: "file changed, was truncated, or grew while being read")
    }
    let digest = hashing ? hasher.finalize().map { String(format: "%02x", $0) }.joined() : nil
    return Verification(path: node.path, stat: after, bytes: total, hash: digest)
}

func readCoordinated(
    root: RootRecord,
    rootFD: Int32?,
    node: Node,
    requested: Bool,
    hashing: Bool
) throws -> Verification {
    var lastChange: Error?
    var expected = node
    for attempt in 0..<2 {
        var coordinationError: NSError?
        var outcome: Result<Verification, Error>?
        let coordinator = NSFileCoordinator(filePresenter: nil)
        coordinator.coordinate(
            readingItemAt: URL(fileURLWithPath: expected.path),
            options: .withoutChanges,
            error: &coordinationError
        ) { suppliedURL in
            do {
                outcome = .success(try streamRead(
                    root: root,
                    rootFD: rootFD,
                    node: expected,
                    coordinatedURL: suppliedURL,
                    requested: requested,
                    hashing: hashing
                ))
            } catch {
                outcome = .failure(error)
            }
        }
        if let coordinationError { throw coordinationError }
        guard let outcome else {
            throw NSError(domain: NSCocoaErrorDomain, code: NSFileReadUnknownError,
                          userInfo: [NSLocalizedDescriptionKey: "file coordination completed without an accessor result"])
        }
        do {
            return try outcome.get()
        } catch let change as FileChanged {
            lastChange = change
            if attempt == 0 {
                var refreshed = stat()
                if lstat(expected.path, &refreshed) == 0, kind(for: refreshed) == .regular {
                    expected = makeNode(root: root, relative: expected.relativeComponents, info: refreshed)
                    continue
                }
            }
            throw change
        }
    }
    throw lastChange ?? FileChanged(description: "file remained unstable")
}
