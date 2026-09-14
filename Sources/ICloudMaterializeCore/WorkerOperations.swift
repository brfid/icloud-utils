import Darwin
import Foundation

private let maxInventoryPasses = 3

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

private func rootResult(_ root: RootRecord, command: CommandMode) -> ResultDocument {
    var result = ResultDocument(command: command)
    result.roots = [root]
    result.counts.rootsRequested = root.supplied.count
    result.counts.rootsResolved = 1
    return result
}

private func forceUnstableInventory() -> Bool {
    #if DEBUG
    return ProcessInfo.processInfo.environment["ICMAT_TEST_FORCE_UNSTABLE"] == "1"
    #else
    return false
    #endif
}

private func operateCheck(root: RootRecord, options: Options) -> ResultDocument {
    var result = rootResult(root, command: .check)
    var previous: Inventory?
    var final = Inventory(nodes: [], failures: [], complete: false)
    for _ in 0..<maxInventoryPasses {
        let current = inventory(root)
        result.counts.inventoryPasses += 1
        final = current
        if !current.complete { break }
        if let previous, !forceUnstableInventory(), previous.stableKey == current.stableKey {
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
    var result = rootResult(root, command: .fetch)
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

    for _ in 0..<maxInventoryPasses where operationalFailures.isEmpty {
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
        if !forceUnstableInventory(), before.stableKey == after.stableKey,
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
            explanation: "the visible tree or file metadata did not stabilize within \(maxInventoryPasses) bounded fetch passes"
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
        var result = rootResult(root, command: options.command)
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
