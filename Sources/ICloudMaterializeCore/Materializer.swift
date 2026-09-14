import CryptoKit
import Darwin
import Foundation

private enum FreshnessState: String, Sendable {
    case current
    case stale
    case unavailable
    case unknown
    case notApplicable
}

private struct StatSignature: Equatable, Hashable, Sendable {
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

private struct Node: Sendable {
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

private struct Inventory: Sendable {
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

private struct Verification: Sendable {
    var path: String
    var stat: StatSignature
    var bytes: Int64
    var hash: String?
}

private struct FileChanged: LocalizedError, CustomStringConvertible {
    var description: String
    var errorDescription: String? { description }
}

#if DEBUG
nonisolated(unsafe) private var injectedFreeSpaceCalls = 0
#endif

private func posixError(_ operation: String) -> NSError {
    let code = errno
    return NSError(
        domain: NSPOSIXErrorDomain,
        code: Int(code),
        userInfo: [NSLocalizedDescriptionKey: "\(operation): \(String(cString: strerror(code)))"]
    )
}

private func withMaterializationPolicy<T>(_ mode: CommandMode, _ body: () throws -> T) throws -> T {
    #if DEBUG
    if ProcessInfo.processInfo.environment["ICMAT_TEST_POLICY_FAILURE"] == "1" {
        throw NSError(domain: NSPOSIXErrorDomain, code: Int(EPERM),
                      userInfo: [NSLocalizedDescriptionKey: "injected materialization-policy failure"])
    }
    #endif
    let policyType = IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES
    let scope = IOPOL_SCOPE_PROCESS
    let previous = getiopolicy_np(policyType, scope)
    if previous == -1 {
        throw posixError("get materialization policy")
    }
    let requested = mode == .fetch
        ? IOPOL_MATERIALIZE_DATALESS_FILES_ON
        : IOPOL_MATERIALIZE_DATALESS_FILES_OFF
    if setiopolicy_np(policyType, scope, requested) == -1 {
        throw posixError("set materialization policy")
    }
    do {
        let result = try body()
        if setiopolicy_np(policyType, scope, previous) == -1 {
            throw posixError("restore materialization policy")
        }
        return result
    } catch {
        _ = setiopolicy_np(policyType, scope, previous)
        throw error
    }
}

private func canonicalComponents(_ path: String) -> [String] {
    URL(fileURLWithPath: path).standardizedFileURL.pathComponents.map {
        $0.precomposedStringWithCanonicalMapping.lowercased()
    }
}

private func containsPath(_ parent: String, _ child: String) -> Bool {
    let a = canonicalComponents(parent)
    let b = canonicalComponents(child)
    return a.count <= b.count && Array(b.prefix(a.count)) == a
}

private func rootKind(_ info: stat) -> String {
    switch info.st_mode & mode_t(S_IFMT) {
    case mode_t(S_IFDIR): return "directory"
    case mode_t(S_IFREG): return "regular-file"
    default: return "unsupported-special"
    }
}

private func resolveAliasIfNeeded(_ url: URL) throws -> URL {
    let values = try url.resourceValues(forKeys: [.isAliasFileKey])
    if values.isAliasFile == true {
        return try URL(resolvingAliasFileAt: url, options: [.withoutUI, .withoutMounting])
    }
    return url
}

func resolveRoots(_ options: Options) -> WorkerReply {
    var reply = WorkerReply()
    do {
        reply = try withMaterializationPolicy(options.command) {
            #if DEBUG
            if ProcessInfo.processInfo.environment["ICMAT_TEST_BLOCK_STAGE"] == "resolution" {
                sleep(60)
            }
            #endif
            var records: [RootRecord] = []
            let cloudURL = URL(fileURLWithPath: NSHomeDirectory())
                .appendingPathComponent("Library/Mobile Documents/com~apple~CloudDocs", isDirectory: true)
                .resolvingSymlinksInPath().standardizedFileURL
            for supplied in options.paths {
                do {
                    if supplied.isEmpty || supplied.utf8.contains(0) {
                        throw UsageError("paths must be nonempty and cannot contain NUL")
                    }
                    var url = URL(fileURLWithPath: supplied)
                    url = try resolveAliasIfNeeded(url)
                    url = url.resolvingSymlinksInPath().standardizedFileURL
                    var info = stat()
                    if lstat(url.path, &info) != 0 {
                        throw posixError("resolve root")
                    }
                    let kind = rootKind(info)
                    if kind == "unsupported-special" {
                        throw UsageError("explicit roots must be regular files or directories")
                    }
                    if !options.allowICloudRoot && containsPath(url.path, cloudURL.path) {
                        throw UsageError("root is the iCloud Drive root or one of its ancestors; pass --allow-icloud-root only when that scope is deliberate")
                    }
                    let record = RootRecord(
                        supplied: [supplied],
                        resolved: url.path,
                        kind: kind,
                        volumeDevice: UInt64(info.st_dev)
                    )

                    if let parentIndex = records.firstIndex(where: { containsPath($0.resolved, record.resolved) }) {
                        records[parentIndex].supplied.append(supplied)
                    } else {
                        let descendants = records.indices.filter { containsPath(record.resolved, records[$0].resolved) }
                        var merged = record
                        for index in descendants.reversed() {
                            merged.supplied.append(contentsOf: records[index].supplied)
                            records.remove(at: index)
                        }
                        records.append(merged)
                    }
                } catch {
                    reply.failures.append(FailureRecord(path: supplied, stage: "resolve-root", error: error))
                }
            }
            records.sort { $0.resolved < $1.resolved }
            reply.roots = records
            return reply
        }
    } catch {
        reply.failures.append(FailureRecord(path: "", stage: "io-policy", error: error))
    }
    return reply
}

private func freshness(at path: String) -> (FreshnessState, Bool) {
    do {
        let values = try URL(fileURLWithPath: path).resourceValues(
            forKeys: [.isUbiquitousItemKey, .ubiquitousItemDownloadingStatusKey]
        )
        guard values.isUbiquitousItem == true else {
            return (.notApplicable, false)
        }
        guard let status = values.ubiquitousItemDownloadingStatus else {
            return (.unknown, true)
        }
        if status == .current { return (.current, true) }
        if status == .downloaded { return (.stale, true) }
        if status == .notDownloaded { return (.unavailable, true) }
        return (.unknown, true)
    } catch {
        return (.unknown, false)
    }
}

private func kind(for info: stat) -> Node.Kind {
    switch info.st_mode & mode_t(S_IFMT) {
    case mode_t(S_IFDIR): return .directory
    case mode_t(S_IFREG): return .regular
    case mode_t(S_IFLNK): return .symlink
    default: return .special
    }
}

private func makeNode(root: RootRecord, relative: [String], info: stat) -> Node {
    let path = relative.isEmpty
        ? root.resolved
        : (root.resolved as NSString).appendingPathComponent(relative.joined(separator: "/"))
    let nodeKind = kind(for: info)
    var state = nodeKind == .regular ? freshness(at: path) : (.notApplicable, false)
    var signature = StatSignature(info)
    #if DEBUG
    let environment = ProcessInfo.processInfo.environment
    if nodeKind == .regular,
       let suffix = environment["ICMAT_TEST_DATALESS_SUFFIX"],
       path.hasSuffix(suffix) {
        signature.dataless = true
        state = (.unavailable, true)
    }
    if nodeKind == .regular, let forced = environment["ICMAT_TEST_FRESHNESS"] {
        switch forced {
        case "current": state = (.current, true)
        case "stale": state = (.stale, true)
        case "unavailable": state = (.unavailable, true)
        case "unknown": state = (.unknown, true)
        case "not-applicable": state = (.notApplicable, false)
        default: break
        }
    }
    #endif
    let name = relative.last ?? URL(fileURLWithPath: path).lastPathComponent
    return Node(
        relativeComponents: relative,
        path: path,
        kind: nodeKind,
        stat: signature,
        freshness: state.0,
        ubiquitous: state.1,
        legacyPlaceholder: name.hasPrefix(".") && name.hasSuffix(".icloud")
    )
}

private func openDirectory(rootFD: Int32, relative: [String]) throws -> Int32 {
    var current = dup(rootFD)
    if current < 0 { throw posixError("duplicate root directory descriptor") }
    do {
        for component in relative {
            let next = component.withCString {
                openat(current, $0, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            }
            if next < 0 { throw posixError("open directory \(component)") }
            Darwin.close(current)
            current = next
        }
        return current
    } catch {
        Darwin.close(current)
        throw error
    }
}

private func childStat(directoryFD: Int32, name: String) throws -> stat {
    var info = stat()
    let result = name.withCString { fstatat(directoryFD, $0, &info, AT_SYMLINK_NOFOLLOW) }
    if result != 0 { throw posixError("inspect directory entry \(name)") }
    return info
}

private func inventory(_ root: RootRecord) -> Inventory {
    #if DEBUG
    if ProcessInfo.processInfo.environment["ICMAT_TEST_BLOCK_STAGE"] == "enumeration" {
        sleep(60)
    }
    #endif
    var inventory = Inventory(nodes: [], failures: [], complete: true)
    var rootInfo = stat()
    if lstat(root.resolved, &rootInfo) != 0 {
        inventory.failures.append(FailureRecord(path: root.resolved, stage: "root-metadata", error: posixError("inspect root")))
        inventory.complete = false
        return inventory
    }
    let rootNode = makeNode(root: root, relative: [], info: rootInfo)
    inventory.nodes.append(rootNode)
    guard rootNode.kind == .directory else { return inventory }

    let rootFD = open(root.resolved, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
    if rootFD < 0 {
        inventory.failures.append(FailureRecord(path: root.resolved, stage: "enumerate-directory", error: posixError("open root directory")))
        inventory.complete = false
        return inventory
    }
    defer { Darwin.close(rootFD) }

    var queue: [[String]] = [[]]
    while !queue.isEmpty {
        let relative = queue.removeFirst()
        let directoryPath = relative.isEmpty
            ? root.resolved
            : (root.resolved as NSString).appendingPathComponent(relative.joined(separator: "/"))
        do {
            #if DEBUG
            if let suffix = ProcessInfo.processInfo.environment["ICMAT_TEST_ENUMERATION_FAILURE_SUFFIX"],
               directoryPath.hasSuffix(suffix) {
                throw NSError(domain: NSPOSIXErrorDomain, code: Int(EACCES),
                              userInfo: [NSLocalizedDescriptionKey: "injected subtree metadata refusal"])
            }
            #endif
            let descriptor = try openDirectory(rootFD: rootFD, relative: relative)
            guard let directory = fdopendir(descriptor) else {
                Darwin.close(descriptor)
                throw posixError("open directory stream")
            }
            defer { closedir(directory) }
            var children: [(String, stat)] = []
            errno = 0
            while let entry = readdir(directory) {
                let name: String? = withUnsafePointer(to: &entry.pointee.d_name) {
                    $0.withMemoryRebound(to: CChar.self, capacity: Int(MAXNAMLEN) + 1) {
                        String(validatingCString: $0)
                    }
                }
                guard let name else {
                    inventory.failures.append(FailureRecord(
                        path: directoryPath,
                        stage: "enumerate-directory",
                        errorDomain: NSPOSIXErrorDomain,
                        errorCode: Int(EILSEQ),
                        explanation: "entry name is not valid UTF-8"
                    ))
                    inventory.complete = false
                    continue
                }
                if name == "." || name == ".." { continue }
                do {
                    children.append((name, try childStat(directoryFD: dirfd(directory), name: name)))
                } catch {
                    let childPath = (directoryPath as NSString).appendingPathComponent(name)
                    inventory.failures.append(FailureRecord(path: childPath, stage: "entry-metadata", error: error))
                    inventory.complete = false
                }
            }
            if errno != 0 {
                throw posixError("read directory entries")
            }
            for (name, info) in children.sorted(by: { $0.0 < $1.0 }) {
                let childRelative = relative + [name]
                let node = makeNode(root: root, relative: childRelative, info: info)
                inventory.nodes.append(node)
                if node.kind == .directory {
                    if node.stat.device != root.volumeDevice {
                        inventory.failures.append(FailureRecord(
                            path: node.path,
                            stage: "mount-boundary",
                            errorDomain: NSPOSIXErrorDomain,
                            errorCode: Int(EXDEV),
                            explanation: "directory is on another mounted filesystem; mount boundaries are not crossed"
                        ))
                        inventory.complete = false
                    } else {
                        queue.append(childRelative)
                    }
                }
            }
        } catch {
            inventory.failures.append(FailureRecord(path: directoryPath, stage: "enumerate-directory", error: error,
                                                   explanation: "subtree could not be inventoried: \((error as NSError).localizedDescription)"))
            inventory.complete = false
        }
    }
    return inventory
}

private func immediatelyFreeBytes(at path: String) throws -> Int64 {
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

private func reserveBytes(_ options: Options) -> Int64 {
    let value = options.reserveMiB.multipliedReportingOverflow(by: 1_048_576)
    return value.overflow ? Int64.max : value.partialValue
}

private func requiresRequest(_ node: Node) -> Bool {
    node.stat.dataless || node.freshness == .stale || node.freshness == .unavailable
}

private func preflightSpace(_ inventory: Inventory, root: RootRecord, options: Options) throws {
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

private func readCoordinated(
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

private func summarize(_ inventory: Inventory, into result: inout ResultDocument) {
    var counts = result.counts
    counts.directories = inventory.nodes.filter { $0.kind == .directory }.count
    counts.regularFiles = inventory.nodes.filter { $0.kind == .regular }.count
    counts.symlinksSkipped = inventory.nodes.filter { $0.kind == .symlink }.count
    counts.specialFilesUnsupported = inventory.nodes.filter { $0.kind == .special }.count
    counts.legacyPlaceholders = inventory.nodes.filter(\.legacyPlaceholder).count
    counts.datalessFiles = inventory.regularFiles.filter { $0.stat.dataless }.count
    counts.uniqueFileObjects = Set(inventory.regularFiles.map { "\($0.stat.device):\($0.stat.inode)" }).count
    result.counts = counts

    var freshnessCounts = FreshnessCounts()
    for node in inventory.regularFiles {
        switch node.freshness {
        case .current: freshnessCounts.current += 1
        case .stale: freshnessCounts.stale += 1
        case .unavailable: freshnessCounts.unavailable += 1
        case .unknown: freshnessCounts.unknown += 1
        case .notApplicable: freshnessCounts.notApplicable += 1
        }
    }
    result.freshness = freshnessCounts
    result.datalessFiles = inventory.regularFiles.filter { $0.stat.dataless }.map {
        DatalessRecord(path: $0.path, logicalBytes: $0.stat.size, freshness: $0.freshness.rawValue)
    }.sorted { $0.path < $1.path }
}

private func operateCheck(root: RootRecord, options: Options) -> ResultDocument {
    var result = ResultDocument(command: .check)
    result.roots = [root]
    result.counts.rootsRequested = root.supplied.count
    result.counts.rootsResolved = 1
    var previous: Inventory?
    var final = Inventory(nodes: [], failures: [], complete: false)
    for _ in 0..<3 {
        let current = inventory(root)
        result.counts.inventoryPasses += 1
        final = current
        if !current.complete { break }
        let forcedUnstable: Bool
        #if DEBUG
        forcedUnstable = ProcessInfo.processInfo.environment["ICMAT_TEST_FORCE_UNSTABLE"] == "1"
        #else
        forcedUnstable = false
        #endif
        if let previous, !forcedUnstable, previous.stableKey == current.stableKey {
            result.enumerationComplete = true
            break
        }
        previous = current
    }
    if final.complete && !result.enumerationComplete {
        result.failures.append(FailureRecord(
            path: root.resolved,
            stage: "inventory-stability",
            errorDomain: "ICloudMaterialize",
            errorCode: 1,
            explanation: "the metadata inventory changed across all bounded passes"
        ))
    }
    result.failures.append(contentsOf: final.failures)
    summarize(final, into: &result)
    result.success = result.enumerationComplete && result.failures.isEmpty
    if result.counts.datalessFiles > 0 {
        result.observations.append("Data-less files were observed. Check did not request downloads or verify payload readability.")
    } else {
        result.observations.append("Check did not read file payloads; zero observed data-less flags is not a readability guarantee.")
    }
    return result
}

private func operateFetch(root: RootRecord, options: Options) -> ResultDocument {
    var result = ResultDocument(command: .fetch)
    result.roots = [root]
    result.counts.rootsRequested = root.supplied.count
    result.counts.rootsResolved = 1
    var requested = Set<String>()
    var initiallyDataless = Set<String>()
    var verifications: [String: Verification] = [:]
    var final = Inventory(nodes: [], failures: [], complete: false)
    var stable = false
    var operationalFailures: [FailureRecord] = []

    let rootFD: Int32?
    if root.kind == "directory" {
        let descriptor = open(root.resolved, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        rootFD = descriptor >= 0 ? descriptor : nil
        if descriptor < 0 {
            operationalFailures.append(FailureRecord(path: root.resolved, stage: "open-root", error: posixError("open root directory")))
        }
    } else {
        rootFD = nil
    }
    defer { if let rootFD { Darwin.close(rootFD) } }

    for _ in 0..<3 where operationalFailures.isEmpty {
        let before = inventory(root)
        result.counts.inventoryPasses += 1
        final = before
        if !before.complete {
            operationalFailures.append(contentsOf: before.failures)
            break
        }
        initiallyDataless.formUnion(before.regularFiles.filter { $0.stat.dataless }.map(\.path))
        do {
            try preflightSpace(before, root: root, options: options)
        } catch {
            operationalFailures.append(FailureRecord(path: root.resolved, stage: "space-preflight", error: error))
            break
        }

        verifications.removeAll(keepingCapacity: true)
        for node in before.regularFiles {
            let needsRequest = requiresRequest(node)
            if needsRequest && !requested.contains(node.path) {
                do {
                    let free = try immediatelyFreeBytes(at: node.path)
                    let threshold = reserveBytes(options).addingReportingOverflow(max(0, node.stat.size))
                    if free < (threshold.overflow ? Int64.max : threshold.partialValue) {
                        throw NSError(domain: NSPOSIXErrorDomain, code: Int(ENOSPC),
                                      userInfo: [NSLocalizedDescriptionKey: "free space fell below this file's known size plus reserve"])
                    }
                    result.counts.downloadRequests += 1
                    result.counts.requestedBytes += max(0, node.stat.size)
                    // This is a public iCloud request. The subsequent coordinated full read
                    // supplies the concrete local-readability guarantee.
                    #if DEBUG
                    if ProcessInfo.processInfo.environment["ICMAT_TEST_DOWNLOAD_FAILURE"] == "1" {
                        throw NSError(domain: NSCocoaErrorDomain, code: NSFileReadUnknownError,
                                      userInfo: [NSLocalizedDescriptionKey: "injected download-request failure"])
                    }
                    #endif
                    try FileManager.default.startDownloadingUbiquitousItem(at: URL(fileURLWithPath: node.path))
                    requested.insert(node.path)
                } catch {
                    result.counts.downloadRequestFailures += 1
                    operationalFailures.append(FailureRecord(path: node.path, stage: "download-request", error: error))
                }
            }
            do {
                let verification = try readCoordinated(
                    root: root,
                    rootFD: rootFD,
                    node: node,
                    requested: requested.contains(node.path),
                    hashing: options.sha256
                )
                result.counts.readBytes += verification.bytes
                verifications[node.path] = verification
            } catch {
                operationalFailures.append(FailureRecord(path: node.path, stage: "coordinated-read", error: error))
            }
        }

        let after = inventory(root)
        result.counts.inventoryPasses += 1
        final = after
        if !after.complete {
            operationalFailures.append(contentsOf: after.failures)
            break
        }
        let forcedUnstable: Bool
        #if DEBUG
        forcedUnstable = ProcessInfo.processInfo.environment["ICMAT_TEST_FORCE_UNSTABLE"] == "1"
        #else
        forcedUnstable = false
        #endif
        if !forcedUnstable, before.stableKey == after.stableKey,
           after.regularFiles.allSatisfy({ node in
               verifications[node.path]?.stat.sameContentMetadata(as: node.stat) == true
           }) {
            stable = true
            result.enumerationComplete = true
            break
        }
    }

    if !stable && operationalFailures.isEmpty {
        operationalFailures.append(FailureRecord(
            path: root.resolved,
            stage: "inventory-stability",
            errorDomain: "ICloudMaterialize",
            errorCode: 1,
            explanation: "the visible tree or file metadata did not stabilize within three bounded fetch passes"
        ))
    }
    for node in final.nodes where node.kind == .special {
        operationalFailures.append(FailureRecord(
            path: node.path,
            stage: "unsupported-special-file",
            errorDomain: NSPOSIXErrorDomain,
            errorCode: Int(ENOTSUP),
            explanation: "FIFO, device, socket, or other special file was inventoried but never read as payload"
        ))
    }
    for node in final.nodes where node.legacyPlaceholder {
        operationalFailures.append(FailureRecord(
            path: node.path,
            stage: "legacy-placeholder",
            errorDomain: "ICloudMaterialize",
            errorCode: 1,
            explanation: "name resembles a legacy .filename.icloud placeholder; this pattern is only a suspicion and is not modified"
        ))
    }
    for node in final.regularFiles where node.stat.dataless {
        operationalFailures.append(FailureRecord(
            path: node.path,
            stage: "remaining-dataless",
            errorDomain: NSPOSIXErrorDomain,
            errorCode: Int(EIO),
            explanation: "file still had SF_DATALESS after the bounded fetch and read attempts"
        ))
    }

    result.failures = operationalFailures
    summarize(final, into: &result)
    let finalVerified = final.regularFiles.compactMap { node -> Verification? in
        guard let verification = verifications[node.path],
              verification.stat.sameContentMetadata(as: node.stat) else { return nil }
        return verification
    }
    result.counts.filesVerified = finalVerified.count
    result.counts.filesVerifiedAfterRequest = finalVerified.filter { requested.contains($0.path) }.count
    result.counts.filesMaterializedAfterRequest = final.regularFiles.filter {
        requested.contains($0.path) && initiallyDataless.contains($0.path) && !$0.stat.dataless
    }.count
    if options.sha256 {
        result.hashes = finalVerified.compactMap { verification in
            verification.hash.map { HashRecord(path: verification.path, sha256: $0) }
        }.sorted { $0.path < $1.path }
        result.counts.hashedFiles = result.hashes.count
    }
    result.success = stable && result.enumerationComplete && result.failures.isEmpty &&
        result.counts.filesVerified == result.counts.regularFiles && result.counts.datalessFiles == 0
    if result.freshness.stale + result.freshness.unavailable + result.freshness.unknown > 0 {
        result.observations.append("Local readability was evaluated separately from iCloud freshness; stale, unavailable, or unknown status remains visible in freshness counts.")
    }
    return result
}

func operateRoot(_ root: RootRecord, options: Options) -> ResultDocument {
    do {
        return try withMaterializationPolicy(options.command) {
            options.command == .check
                ? operateCheck(root: root, options: options)
                : operateFetch(root: root, options: options)
        }
    } catch {
        var result = ResultDocument(command: options.command)
        result.roots = [root]
        result.counts.rootsRequested = root.supplied.count
        result.counts.rootsResolved = 1
        result.failures = [FailureRecord(path: root.resolved, stage: "io-policy", error: error)]
        return result
    }
}

func runWorker(_ request: WorkerRequest) -> WorkerReply {
    switch request.kind {
    case .resolve:
        return resolveRoots(request.options)
    case .operate:
        guard let root = request.root else {
            return WorkerReply(failures: [FailureRecord(
                path: "",
                stage: "worker-protocol",
                errorDomain: "ICloudMaterialize",
                errorCode: 1,
                explanation: "operate worker request did not include a root"
            )])
        }
        return WorkerReply(result: operateRoot(root, options: request.options))
    }
}
