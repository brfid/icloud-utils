import Foundation
import ICloudMaterializeCore

let executable = Bundle.main.executableURL?.path ?? CommandLine.arguments[0]
let status = runMain(arguments: Array(CommandLine.arguments.dropFirst()), executable: executable)
exit(status)
