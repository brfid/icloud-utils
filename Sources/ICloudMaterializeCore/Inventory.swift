import Darwin
import Foundation

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

func kind(for info: stat) -> Node.Kind {
    switch info.st_mode & mode_t(S_IFMT) {
    case mode_t(S_IFDIR): return .directory
    case mode_t(S_IFREG): return .regular
    case mode_t(S_IFLNK): return .symlink
    default: return .special
    }
}

func makeNode(root: RootRecord, relative: [String], info: stat) -> Node {
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

func openDirectory(rootFD: Int32, relative: [String]) throws -> Int32 {
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

func inventory(_ root: RootRecord) -> Inventory {
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

