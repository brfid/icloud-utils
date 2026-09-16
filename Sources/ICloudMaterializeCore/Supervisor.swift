import Darwin
import Dispatch
import Foundation

final class SignalState: @unchecked Sendable {
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

func runPublic(_ options: Options, executable: String, signalState: SignalState) -> (ResultDocument, Int32) {
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
            result.observations.append("A timeout cannot cancel submitted iCloud daemon requests. Up to \(options.downloadAhead) successful requests in the interrupted root may still await verification; earlier requests may also continue background daemon work.")
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
    result.datalessFiles.sort { $0.path < $1.path }
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
