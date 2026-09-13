import Darwin
import Dispatch
import Foundation

public let helpText = """
Usage:
  icloud-materialize check [OPTIONS] -- PATH...
  icloud-materialize fetch [OPTIONS] -- PATH...

Commands:
  check   Inventory accessible metadata with automatic materialization disabled.
          It never requests downloads or reads file payloads.
  fetch   Inventory the scope, request appropriate iCloud downloads, and read every
          included regular file to EOF. With --sha256, hash those same reads.

Options:
  --json                 Emit one versioned JSON result object on stdout.
  --timeout SECONDS      Limit each supervised resolution/root worker (default: 300).
  --deadline SECONDS     Limit the complete command (default: 1800).
  --reserve-mib MIB      Keep this immediately free capacity (default: 1024).
  --sha256               Hash every regular file during fetch; invalid with check.
  --verbose              Report worker progress on stderr.
  --allow-icloud-root    Permit the iCloud Drive root or one of its ancestors.
  --help                 Show this help.
  --version              Show tool and JSON schema versions.

At least one explicit path and the -- path separator are required. Explicit root
symlinks and Finder aliases are resolved; symlinks found below roots are skipped.
Mount boundaries are reported as incomplete and are not crossed. Success from check
means metadata inventory completed, not that payload bytes are readable. Success from
fetch means all included regular files were read to EOF in a stable visible inventory;
it does not prove server synchronization or permanent local retention.

Exit status: 0 success, 1 incomplete/operational failure, 2 usage error, 130 SIGINT,
143 SIGTERM. A timeout can leave an already submitted iCloud download continuing.
"""

public func parseOptions(_ arguments: [String]) throws -> Options {
    guard let first = arguments.first, let command = CommandMode(rawValue: first) else {
        throw UsageError("first argument must be check or fetch")
    }
    guard let separator = arguments.firstIndex(of: "--") else {
        throw UsageError("use -- before one or more explicit paths")
    }
    let optionArguments = arguments[arguments.index(after: arguments.startIndex)..<separator]
    let paths = Array(arguments[arguments.index(after: separator)..<arguments.endIndex])
    guard !paths.isEmpty else { throw UsageError("at least one explicit path is required") }

    var options = Options(command: command, paths: paths)
    var index = optionArguments.startIndex
    func value(after name: String) throws -> String {
        let next = optionArguments.index(after: index)
        guard next < optionArguments.endIndex else { throw UsageError("\(name) requires a value") }
        index = next
        return optionArguments[next]
    }
    while index < optionArguments.endIndex {
        let argument = optionArguments[index]
        switch argument {
        case "--json": options.json = true
        case "--sha256": options.sha256 = true
        case "--verbose": options.verbose = true
        case "--allow-icloud-root": options.allowICloudRoot = true
        case "--timeout":
            let text = try value(after: argument)
            guard let parsed = Double(text), parsed.isFinite, parsed > 0 else {
                throw UsageError("--timeout must be a finite number greater than zero")
            }
            options.timeoutSeconds = parsed
        case "--deadline":
            let text = try value(after: argument)
            guard let parsed = Double(text), parsed.isFinite, parsed > 0 else {
                throw UsageError("--deadline must be a finite number greater than zero")
            }
            options.deadlineSeconds = parsed
        case "--reserve-mib":
            let text = try value(after: argument)
            guard let parsed = Int64(text), parsed >= 0 else {
                throw UsageError("--reserve-mib must be an integer at least zero")
            }
            options.reserveMiB = parsed
        default:
            throw UsageError("unknown option: \(argument)")
        }
        index = optionArguments.index(after: index)
    }
    if command == .check && options.sha256 {
        throw UsageError("--sha256 is only valid with fetch because check never reads payloads")
    }
    return options
}

private final class SignalState: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: Int32?

    func set(_ signal: Int32) {
        lock.lock()
        if stored == nil { stored = signal }
        lock.unlock()
    }

    func get() -> Int32? {
        lock.lock()
        defer { lock.unlock() }
        return stored
    }
}

private final class OutputReader: @unchecked Sendable {
    private let handle: FileHandle
    private let group = DispatchGroup()
    private let lock = NSLock()
    private var stored = Data()

    init(_ handle: FileHandle) { self.handle = handle }

    func start() {
        group.enter()
        DispatchQueue.global(qos: .utility).async {
            let data = self.handle.readDataToEndOfFile()
            self.lock.lock()
            self.stored = data
            self.lock.unlock()
            self.group.leave()
        }
    }

    func finish(timeout: Double = 2) -> Data? {
        guard group.wait(timeout: .now() + timeout) == .success else { return nil }
        lock.lock()
        defer { lock.unlock() }
        return stored
    }
}

private struct SupervisedReply {
    var reply: WorkerReply?
    var failure: FailureRecord?
    var timedOut: Bool
    var interrupted: Int32?
}

private func terminateAndReap(_ process: Process) -> Bool {
    if process.isRunning { process.terminate() }
    var end = ProcessInfo.processInfo.systemUptime + 2
    while process.isRunning && ProcessInfo.processInfo.systemUptime < end { usleep(50_000) }
    if process.isRunning { _ = kill(process.processIdentifier, SIGKILL) }
    end = ProcessInfo.processInfo.systemUptime + 2
    while process.isRunning && ProcessInfo.processInfo.systemUptime < end { usleep(50_000) }
    if !process.isRunning { process.waitUntilExit() }
    return !process.isRunning
}

private func supervise(
    request: WorkerRequest,
    executable: String,
    seconds: Double,
    signalState: SignalState
) -> SupervisedReply {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = ["--internal-worker"]
    let input = Pipe()
    let output = Pipe()
    process.standardInput = input
    process.standardOutput = output
    process.standardError = FileHandle.standardError
    let reader = OutputReader(output.fileHandleForReading)
    do {
        try process.run()
        reader.start()
        input.fileHandleForWriting.write(try jsonData(request))
        try input.fileHandleForWriting.close()
    } catch {
        if process.isRunning { _ = terminateAndReap(process) }
        return SupervisedReply(
            failure: FailureRecord(path: request.root?.resolved ?? "", stage: "start-worker", error: error),
            timedOut: false,
            interrupted: nil
        )
    }

    let end = ProcessInfo.processInfo.systemUptime + seconds
    var interrupted: Int32?
    while process.isRunning {
        if let signal = signalState.get() {
            interrupted = signal
            break
        }
        if ProcessInfo.processInfo.systemUptime >= end { break }
        usleep(50_000)
    }
    let timedOut = process.isRunning && interrupted == nil
    let reaped = process.isRunning ? terminateAndReap(process) : true
    if !process.isRunning { process.waitUntilExit() }
    guard reaped else {
        return SupervisedReply(
            failure: FailureRecord(
                path: request.root?.resolved ?? "",
                stage: "reap-worker",
                errorDomain: "ICloudMaterialize",
                errorCode: 1,
                explanation: "worker did not exit within the bounded TERM/KILL reaping interval"
            ),
            timedOut: timedOut,
            interrupted: interrupted
        )
    }
    if timedOut || interrupted != nil {
        _ = reader.finish()
        return SupervisedReply(reply: nil, failure: nil, timedOut: timedOut, interrupted: interrupted)
    }
    guard process.terminationStatus == 0 else {
        return SupervisedReply(
            failure: FailureRecord(
                path: request.root?.resolved ?? "",
                stage: "worker-exit",
                errorDomain: NSPOSIXErrorDomain,
                errorCode: Int(process.terminationStatus),
                explanation: "worker exited before returning a result"
            ),
            timedOut: false,
            interrupted: nil
        )
    }
    guard let data = reader.finish() else {
        return SupervisedReply(
            failure: FailureRecord(path: request.root?.resolved ?? "", stage: "worker-output",
                                   errorDomain: "ICloudMaterialize", errorCode: 1,
                                   explanation: "worker output pipe did not close within the bounded interval"),
            timedOut: false,
            interrupted: nil
        )
    }
    do {
        return SupervisedReply(reply: try JSONDecoder().decode(WorkerReply.self, from: data),
                               failure: nil, timedOut: false, interrupted: nil)
    } catch {
        return SupervisedReply(
            failure: FailureRecord(path: request.root?.resolved ?? "", stage: "worker-output", error: error,
                                   explanation: "worker did not return the expected JSON protocol: \((error as NSError).localizedDescription)"),
            timedOut: false,
            interrupted: nil
        )
    }
}

private func resultForUsage(arguments: [String], message: String) -> ResultDocument {
    let command: CommandMode = arguments.first == "fetch" ? .fetch : .check
    var result = ResultDocument(command: command)
    result.failures = [FailureRecord(path: "", stage: "usage", errorDomain: "ICloudMaterialize",
                                     errorCode: 2, explanation: message)]
    return result
}

private func emit(_ result: ResultDocument, json: Bool) {
    if json {
        if let data = try? jsonData(result, pretty: true) {
            FileHandle.standardOutput.write(data)
            FileHandle.standardOutput.write(Data("\n".utf8))
        }
        return
    }
    let stream = FileHandle.standardError
    let status = result.success ? "SUCCESS" : "INCOMPLETE"
    var lines = [
        "\(status): \(result.command.rawValue); \(result.counts.regularFiles) regular file(s), \(result.counts.directories) director(ies), \(result.counts.datalessFiles) data-less file(s).",
        "Enumeration complete: \(result.enumerationComplete ? "yes" : "no"). Verification: \(result.verificationScope).",
    ]
    if result.command == .fetch {
        lines.append("Verified \(result.counts.filesVerified) file(s), read \(result.counts.readBytes) bytes, made \(result.counts.downloadRequests) explicit download request(s).")
    }
    for failure in result.failures {
        lines.append("\(failure.stage): \(failure.path.isEmpty ? "(command)" : failure.path): \(failure.explanation)")
    }
    lines.append(contentsOf: result.observations)
    stream.write(Data((lines.joined(separator: "\n") + "\n").utf8))
}

private func runPublic(_ options: Options, executable: String, signalState: SignalState) -> (ResultDocument, Int32) {
    let started = ProcessInfo.processInfo.systemUptime
    let absoluteDeadline = started + options.deadlineSeconds
    var result = ResultDocument(command: options.command)
    result.enumerationComplete = true

    func remainingWorkerLimit() -> Double {
        max(0, min(options.timeoutSeconds, absoluteDeadline - ProcessInfo.processInfo.systemUptime))
    }

    let resolveLimit = remainingWorkerLimit()
    if resolveLimit == 0 {
        result.timedOut = true
        result.enumerationComplete = false
        result.failures.append(FailureRecord(path: "", stage: "deadline", errorDomain: "ICloudMaterialize",
                                             errorCode: Int(ETIMEDOUT), explanation: "whole-command deadline elapsed before root resolution"))
        return (result, 1)
    }
    let resolved = supervise(
        request: WorkerRequest(kind: .resolve, options: options, root: nil),
        executable: executable,
        seconds: resolveLimit,
        signalState: signalState
    )
    if let signal = resolved.interrupted {
        result.interruptedBySignal = signal
        result.enumerationComplete = false
        result.failures.append(FailureRecord(path: "", stage: "interrupted", errorDomain: NSPOSIXErrorDomain,
                                             errorCode: Int(EINTR), explanation: "interrupted while resolving roots"))
        return (result, 128 + signal)
    }
    if resolved.timedOut {
        result.timedOut = true
        result.enumerationComplete = false
        result.failures.append(FailureRecord(path: "", stage: "resolve-timeout", errorDomain: NSPOSIXErrorDomain,
                                             errorCode: Int(ETIMEDOUT), explanation: "root resolution exceeded its worker time limit"))
        result.observations.append("A timeout cannot cancel an iCloud daemon request that was already submitted; background downloading may continue.")
        return (result, 1)
    }
    if let failure = resolved.failure {
        result.enumerationComplete = false
        result.failures.append(failure)
        return (result, 1)
    }
    guard let resolution = resolved.reply else {
        result.enumerationComplete = false
        result.failures.append(FailureRecord(path: "", stage: "worker-protocol", errorDomain: "ICloudMaterialize",
                                             errorCode: 1, explanation: "resolution worker returned no result"))
        return (result, 1)
    }
    result.failures.append(contentsOf: resolution.failures)
    if !resolution.failures.isEmpty { result.enumerationComplete = false }
    if resolution.roots.isEmpty { result.enumerationComplete = false }

    for (index, root) in resolution.roots.enumerated() {
        if let signal = signalState.get() {
            result.interruptedBySignal = signal
            result.enumerationComplete = false
            result.failures.append(FailureRecord(path: root.resolved, stage: "interrupted", errorDomain: NSPOSIXErrorDomain,
                                                 errorCode: Int(EINTR), explanation: "root and later roots were not processed"))
            result.counts.rootsRequested = options.paths.count
            result.counts.rootsResolved = resolution.roots.count
            return (result, 128 + signal)
        }
        let limit = remainingWorkerLimit()
        if limit == 0 {
            result.timedOut = true
            result.enumerationComplete = false
            for pending in resolution.roots[index...] {
                result.failures.append(FailureRecord(path: pending.resolved, stage: "deadline", errorDomain: NSPOSIXErrorDomain,
                                                     errorCode: Int(ETIMEDOUT), explanation: "whole-command deadline elapsed before this root was completed"))
            }
            break
        }
        if options.verbose {
            FileHandle.standardError.write(Data("Processing \(root.resolved)\n".utf8))
        }
        let operated = supervise(
            request: WorkerRequest(kind: .operate, options: options, root: root),
            executable: executable,
            seconds: limit,
            signalState: signalState
        )
        if let signal = operated.interrupted {
            result.interruptedBySignal = signal
            result.enumerationComplete = false
            result.failures.append(FailureRecord(path: root.resolved, stage: "interrupted", errorDomain: NSPOSIXErrorDomain,
                                                 errorCode: Int(EINTR), explanation: "worker was terminated; this root's partial work is not counted as verified"))
            result.counts.rootsRequested = options.paths.count
            result.counts.rootsResolved = resolution.roots.count
            return (result, 128 + signal)
        }
        if operated.timedOut {
            result.timedOut = true
            result.enumerationComplete = false
            result.roots.append(root)
            result.failures.append(FailureRecord(path: root.resolved, stage: "worker-timeout", errorDomain: NSPOSIXErrorDomain,
                                                 errorCode: Int(ETIMEDOUT), explanation: "root operation exceeded its worker time limit; partial work is not counted as verified"))
            result.observations.append("A timeout cannot cancel an iCloud daemon request that was already submitted; background downloading may continue.")
            continue
        }
        if let failure = operated.failure {
            result.enumerationComplete = false
            result.roots.append(root)
            result.failures.append(failure)
            continue
        }
        if let partial = operated.reply?.result {
            result.merge(partial)
        } else {
            result.enumerationComplete = false
            result.roots.append(root)
            result.failures.append(contentsOf: operated.reply?.failures ?? [])
        }
    }

    result.counts.rootsRequested = options.paths.count
    result.counts.rootsResolved = resolution.roots.count
    result.hashes.sort { $0.path < $1.path }
    if options.command == .check {
        result.success = result.enumerationComplete && result.failures.isEmpty &&
            result.roots.count == resolution.roots.count
    } else {
        result.success = result.enumerationComplete && result.failures.isEmpty &&
            result.counts.filesVerified == result.counts.regularFiles && result.counts.datalessFiles == 0 &&
            result.roots.count == resolution.roots.count
    }
    return (result, result.success ? 0 : 1)
}

public func workerMain() -> Int32 {
    _ = Darwin.signal(SIGINT, SIG_DFL)
    _ = Darwin.signal(SIGTERM, SIG_DFL)
    do {
        let request = try JSONDecoder().decode(WorkerRequest.self, from: FileHandle.standardInput.readDataToEndOfFile())
        FileHandle.standardOutput.write(try jsonData(runWorker(request)))
        return 0
    } catch {
        FileHandle.standardError.write(Data("internal worker failure: \((error as NSError).localizedDescription)\n".utf8))
        return 1
    }
}

public func runMain(arguments: [String], executable: String) -> Int32 {
    if arguments == ["--internal-worker"] { return workerMain() }
    if arguments.contains("--help") || arguments == ["help"] {
        FileHandle.standardOutput.write(Data((helpText + "\n").utf8))
        return 0
    }
    if arguments == ["--version"] || arguments == ["version"] {
        print("icloud-materialize \(toolVersion) (JSON schema \(schemaVersion))")
        return 0
    }

    let wantsJSON = arguments.contains("--json")
    let options: Options
    do {
        options = try parseOptions(arguments)
    } catch {
        let result = resultForUsage(arguments: arguments, message: String(describing: error))
        emit(result, json: wantsJSON)
        if !wantsJSON {
            FileHandle.standardError.write(Data("Run icloud-materialize --help for usage.\n".utf8))
        }
        return 2
    }

    let signalState = SignalState()
    _ = Darwin.signal(SIGINT, SIG_IGN)
    _ = Darwin.signal(SIGTERM, SIG_IGN)
    let interrupt = DispatchSource.makeSignalSource(signal: SIGINT, queue: .global(qos: .userInitiated))
    let terminate = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .global(qos: .userInitiated))
    interrupt.setEventHandler { signalState.set(SIGINT) }
    terminate.setEventHandler { signalState.set(SIGTERM) }
    interrupt.resume()
    terminate.resume()
    defer {
        interrupt.cancel()
        terminate.cancel()
        _ = Darwin.signal(SIGINT, SIG_DFL)
        _ = Darwin.signal(SIGTERM, SIG_DFL)
    }

    let (result, status) = runPublic(options, executable: executable, signalState: signalState)
    emit(result, json: options.json)
    return status
}
