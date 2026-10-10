# Materializer

This document owns the public behavior and limits of `icloud-materialize` (`icmat`). [Operations](operations.md) owns installation and removal, [Validation](validation.md) owns test coverage and release readiness, and [Architecture](architecture.md) owns component boundaries.

## Quick start

```text
icloud-materialize check [OPTIONS] -- PATH...
icloud-materialize fetch [OPTIONS] -- PATH...
```

Use `check` when metadata is enough and downloading content would be unwanted:

```sh
icmat check --json -- "$HOME/Library/Mobile Documents/com~apple~CloudDocs/example"
```

Use `fetch` when every regular file in the named scope must be locally readable:

```sh
icmat fetch --verbose -- "$HOME/Library/Mobile Documents/com~apple~CloudDocs/example"
```

Add `--sha256` explicitly for provenance or validation digests. It hashes the same complete verification reads and is off by default.

Success from `check` establishes a stable, complete metadata inventory. It does not establish byte readability. Success from `fetch` establishes stable complete reads of the visible regular files and no remaining observed data-less flag. Neither result proves server synchronization or permanent local retention.

## Scope and safety

Both commands require at least one explicit path after `--`. Explicit root symlinks and Finder aliases are resolved, and overlapping roots are deduplicated. A root that is the iCloud Drive root or an ancestor is rejected unless the operator supplies `--allow-icloud-root`; descendant symlinks are never followed.

Hidden files, packages, nested directories, and empty files are included. Mount boundaries are not crossed and make the inventory incomplete. Symlinks and unsupported special files are counted; payload reads never open FIFOs, devices, sockets, or other special files. A `.filename.icloud` name is reported only as a suspected unsupported legacy placeholder, never treated as proof or modified.

`check` sets the process materialization policy to OFF. All supplied-path resolution, metadata queries, enumeration, and volume queries occur in supervised worker processes. A refusal or timeout reports the exact incomplete root or subtree. Check does not request downloads or read payloads. Success means consecutive metadata inventories stabilized and traversal completed; data-less files can therefore be reported in a successful check.

`fetch` sets the worker policy to ON and requests downloads only for data-less or reported stale/unavailable candidates. It still reads every included regular-file path, including already-local files and hard links.

## Verification mechanics

The parent runs root workers one at a time. On a worker timeout, command deadline, or interrupt, it attempts to stop and reap the active worker with bounded TERM/KILL cleanup intervals.

Fetch requests downloads with Foundation's per-file `startDownloadingUbiquitousItem`, then coordinates each native read with `NSFileCoordinator` and `.withoutChanges`. Within each root, up to `--download-ahead` successfully requested files can await verification. Reads remain serial in deterministic inventory order; after a requested file's read attempt finishes, the window is replenished. A value of `1` keeps request-then-read ordering. This gives iCloud an opportunity to overlap downloads; Apple does not guarantee parallel transfers. Mostly-local trees and single large files may see little or no benefit. The [request-ahead decision](decisions/adr-003-download-ahead.md) explains the alternatives.

The read validates the URL supplied to the accessor and performs the complete fixed-buffer read inside that accessor. With `--sha256`, every digest is computed during that same read.

File descriptors use no-follow opens. Directory traversal and file opens walk relative components from an already opened root descriptor. Identity, type, size, change times, and the exact EOF length are revalidated; a changing file receives one bounded retry. The complete visible inventory is repeated up to three times. Any failed request, read, timeout, unsupported special file, suspected legacy placeholder, unhandled change, unstable inventory, or remaining `SF_DATALESS` flag prevents fetch success.

Neither command deliberately edits, uploads, renames, moves, evicts, or deletes user files. Hydration or refresh can change cached bytes and metadata, and macOS can evict a file later. A timeout can leave up to the selected window of already submitted iCloud download requests continuing within the interrupted root; requests completed earlier may also have triggered daemon work. Results are point-in-time observations of the device-visible tree, not an atomic snapshot, a server inventory, or proof of synchronization.

## Options and statuses

| Option | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Emit one final schema object on stdout, including handled failures |
| `--timeout SECONDS` | `300` | Maximum for each freshly executed root-resolution or root-operation worker |
| `--deadline SECONDS` | `1800` | Maximum for the whole command |
| `--reserve-mib MIB` | `1024` | Immediately free capacity retained on each processed volume |
| `--sha256` | off | Hash all regular files during fetch; rejected by check |
| `--download-ahead N` | `4` | Fetch only; request 1 through 16 candidate files ahead of serial verification reads |
| `--verbose` | off | Inventory, explicit requests, current verification read, and periodic aggregate progress on stderr |
| `--allow-icloud-root` | off | Permit the otherwise guarded broad iCloud/ancestor scope |

Exit 0 means the documented command guarantee completed. Exit 1 means traversal or operation was incomplete. Exit 2 is usage failure. Handled SIGINT and SIGTERM return 130 and 143. A crash or SIGKILL cannot promise final JSON.

Space checks use immediately free filesystem capacity, not estimates that include purgeable storage. Known data-less/stale logical sizes plus the reserve are checked before work and again before requests. These estimates do not reserve capacity: newly revealed children, stale replacements, filesystem overhead, other writers, and later eviction remain possible.

## JSON schema 1.0

The final object includes:

- `toolVersion`, `schemaVersion`, `command`, `success`, `enumerationComplete`, and `verificationScope`;
- resolved `roots`, retaining every supplied spelling and the root volume/device;
- `counts` for requested/resolved roots, directories, regular/unique files, data-less files, skipped symlinks, unsupported special files, suspected legacy placeholders, inventory passes, explicit requests and request failures, verified files, post-request observations, requested/read bytes, and hashes;
- separate `freshness` counts for `current`, `stale`, `unavailable`, `unknown`, and `notApplicable`;
- per-path `datalessFiles`, per-path `hashes` when requested, and structured `failures` with stage, domain, code, and explanation;
- `timedOut`, `interruptedBySignal`, `hydrationAttribution`, and interpretive `observations`.

`downloadRequests` records calls made by this program. `filesMaterializedAfterRequest` records a data-less-before/local-after observation. It does not claim that this process alone caused hydration because iCloud and other processes can work concurrently. Missing download-status keys count as unknown, never current. Fetch success is based on local readability; stale or unknown freshness remains visible and is not described as synchronized success.

## Build and test

Source builds require Apple Swift, Foundation, Darwin, CryptoKit, and a compatible macOS SDK from Command Line Tools. No third-party packages, full Xcode, simulator, Python, or Restic are required for the materializer. The package declares macOS 13 as its deployment target. That is a compilation target, not a claim of runtime testing on macOS 13.

Builds discover the selected macOS SDK through `xcrun`. Run the build and disposable CLI checks from the checkout; the checks additionally require Python 3.11 or later:

```sh
make build
make test-materializer
```

Build products and module caches stay under the configurable `SWIFT_SCRATCH` and `/tmp` paths rather than Git. If the selected SDK and compiler are incompatible, select an installed compatible SDK with `SWIFT_SDK=/absolute/path/to/MacOSX.sdk`; `SWIFT_SCRATCH` also accepts an explicit build directory. [Validation](validation.md#automated-coverage) describes Python selection and the combined toolkit checks. Debug-only environment injection exercises blocking calls and failure paths but is absent from release builds.

For a release build, checksum, and readiness checks, continue with [Validation](validation.md#validate-a-local-release). After validation, follow [Install or remove the materializer](operations.md#install-or-remove-the-materializer). The installer intentionally has no release-download or auto-update behavior.
