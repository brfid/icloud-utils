# Validation and release status

Validation establishes specific local properties. It does not convert local backup completion or local file readability into evidence of completed iCloud synchronization.

This document owns automated coverage, real-iCloud acceptance conditions, the tested platform matrix, and public-release limits. Behavioral guarantees belong to [Architecture](architecture.md) and the [materializer contract](materializer.md).

## Automated coverage

Run the combined suite from the checkout:

```sh
make test
```

The backup tests use temporary data and real empty-password Restic repositories. They cover version and deletion recovery, hidden files, symlinks, exclusions, independent source groups, retention across year and leap-day boundaries, interrupted children, and injected read, check, and space failures. They verify that earlier completed recovery points survive failures and that later runs can recover.

Installation tests cover configuration selection, schedule wiring, command aliases, the watchdog's missing-runner path, and an installed runtime after its fixture checkout is removed. Materializer tests cover scope resolution, metadata inventory, complete reads, hashing, stability, timeouts, interruptions, special files, legacy placeholders, installers, and injected failures. Unsafe failure injection is compiled only into debug builds.

These tests use disposable paths. They do not operate on the configured production backup store.

## Real iCloud acceptance

Native materialization is established only when a disposable fixture first reaches `SF_DATALESS`. Record expected hashes before eviction, evict only the fixture through Foundation's public API, and confirm the native flag before running the release binary. A successful acceptance run must clear the flag, complete stable reads, and match the independent hashes. A second run must still read every file while making no unnecessary download request.

Initial acceptance met those conditions for a nested fixture. It also materialized one metadata-selected, pre-existing data-less archive file without evicting or changing that archive content. A disposable background LaunchAgent completed the same installed-binary read path, which establishes operation in that tested noninteractive environment. Fixture names, hashes, and machine logs remain private and outside Git.

Backup acceptance has exercised native eviction, incremental capture, deleted-file recovery, full checking, prune, cloud-only source reads, a scheduled capture, and separate verified restores. Those observations establish the tested local paths, not a remote transactional guarantee.

## Tested platform boundary

The initial materializer implementation was compiled and run only on Apple silicon (`arm64`) macOS 26.6.2 with Apple Swift 6.4 and the installed macOS 26.5 SDK. The package declares macOS 13 as its deployment target, but macOS 13–15 runtime behavior, macOS 27 runtime behavior, and Intel hardware remain untested.

A fixture that never reaches uploaded/current state or never becomes data-less is inconclusive. In particular, local package traversal is covered automatically, but a package-contained real iCloud file did not become eligible for the initial native-eviction test within its bounded wait.

## Validate a local release

Build products and caches remain outside Git. Run:

```sh
make test
make release
make checksum
file /tmp/icloud-utils-swift-build/release/icloud-materialize
otool -L /tmp/icloud-utils-swift-build/release/icloud-materialize
```

Smoke-test the release executable on a disposable local fixture. When real iCloud behavior changed, repeat the bounded native acceptance above. Install that exact binary through [Operations](operations.md#install-or-remove-the-materializer), then compare its checksum with the tested artifact.

The local build is ad hoc and not notarized. Public binary distribution requires an identified Developer ID signature, notarization, stapling where applicable, dependency inspection, and runtime testing on every claimed OS and architecture. These procedures do not publish a release.

## Claims these checks do not support

The checks do not establish physical power-loss behavior, application-consistent capture of live databases, completed iCloud upload, permanent local retention, server inventory, multi-writer safety, or compatibility beyond the tested platform. State those limits directly when reporting a successful run.
