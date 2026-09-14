import Darwin
import Foundation

func posixError(_ operation: String) -> NSError {
    let code = errno
    return NSError(
        domain: NSPOSIXErrorDomain,
        code: Int(code),
        userInfo: [NSLocalizedDescriptionKey: "\(operation): \(String(cString: strerror(code)))"]
    )
}

func withMaterializationPolicy<T>(_ mode: CommandMode, _ body: () throws -> T) throws -> T {
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
