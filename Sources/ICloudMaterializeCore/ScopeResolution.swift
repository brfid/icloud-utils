import Darwin
import Foundation

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
