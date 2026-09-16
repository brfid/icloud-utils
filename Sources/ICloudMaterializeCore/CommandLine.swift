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
  --timeout SECONDS      Limit each supervised resolution/root worker (default: \(Int(defaultWorkerTimeoutSeconds))).
  --deadline SECONDS     Limit the complete command (default: \(Int(defaultCommandDeadlineSeconds))).
  --reserve-mib MIB      Keep this immediately free capacity (default: \(defaultReserveMiB)).
  --sha256               Hash every regular file during fetch; invalid with check.
  --download-ahead N     Request 1...16 candidate files ahead during fetch (default: \(defaultDownloadAhead)).
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
143 SIGTERM. A timeout can leave up to the selected window of submitted iCloud
downloads continuing.
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
        case "--download-ahead":
            let text = try value(after: argument)
            guard let parsed = Int(text), (1...16).contains(parsed) else {
                throw UsageError("--download-ahead must be an integer from 1 through 16")
            }
            options.downloadAhead = parsed
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
    if command == .check && optionArguments.contains("--download-ahead") {
        throw UsageError("--download-ahead is only valid with fetch")
    }
    return options
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
