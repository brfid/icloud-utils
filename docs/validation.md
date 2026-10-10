# Validation and release status

Validation establishes specific local properties. It does not convert local backup completion or local file readability into evidence of completed iCloud synchronization.

This document owns automated coverage, real-iCloud acceptance conditions, the tested platform matrix, and public-release limits. Behavioral guarantees belong to [Architecture](architecture.md) and the [materializer contract](materializer.md).

## Automated coverage

The combined suite requires Python 3.11 or later, Restic, and Apple Swift with a compatible macOS SDK. It uses disposable files and stores, not production configuration or credentials. Run it from the checkout:

```sh
make test
```

Select focused checks as needed:

| Command | Scope |
| --- | --- |
| `make test-mail` | Mail commands, configuration, review state, documentation, and installation; no Swift or Restic required |
| `make test-backup` | Backup, backup installation, and retention; requires Restic |
| `make test-installers` | Shared deployment helpers and disposable backup and mail installations; requires Restic |
| `make test-materializer` | Builds and checks the native CLI using disposable local files; requires Swift and Python, with no Restic or Himalaya dependency |

Each target accepts `PYTHON=/absolute/path/to/python3` and fails early for older interpreters. The installers use [the same selection override](operations.md#select-python-for-installation). For SDK selection and build directories, use [Build and test](materializer.md#build-and-test).

### Coverage and limits

Mail, backup, and retention tests share disposable fixtures from `tests/support/` without importing another test module. Shared installer tests cover invoking-interpreter preference, candidate failure/timeout fallback, old-version rejection, exact entry-point argument/exit forwarding, collision refusal, and atomic-publication failures.

Mail checks have been exercised on Python 3.11 and 3.14. A process sandbox can deny Foundation coordinated reads even for disposable local files; a native test failure in that environment needs comparison with the same tests in an ordinary authorized macOS environment. Do not weaken native-read checks to accommodate that restriction.

The backup tests use temporary data and real empty-password Restic repositories. They cover version and deletion recovery, hidden files, symlinks, exclusions, independent source groups, retention across year and leap-day boundaries, interrupted children, and injected read, check, and space failures. They verify that earlier completed recovery points survive failures and that later runs can recover.

Installation tests cover configuration selection, schedule wiring, command aliases, the watchdog's missing-runner path, and an installed runtime after its fixture checkout is removed. Materializer tests cover scope resolution, metadata inventory, complete reads with hashing off by default, optional hashing, bounded request-ahead order and limits, stability, timeouts, interruptions, special files, legacy placeholders, installers, and injected failures. Unsafe failure injection and request-order tracing are compiled only into debug builds.

Disposable Himalaya processes enforce exact global routing/backend arguments and allowlisted command shapes, rejecting unexpected flags rather than silently ignoring them. Temporary account/routing files exercise explicit review acknowledgments, pending-action resolution, account and UIDVALIDITY isolation, mid-read identity changes, malformed configuration/table shapes, semantically corrupt state, failed authentication despite exit zero, withheld private error streams, timeout/signal child cleanup, attachment destinations/partial results, and owner-only state handling. Disposable installations cover custom configuration discovery across updates and deliberate overrides, missing or ambiguous routing, preservation of private files, and commands/documentation after moving the fixture checkout. Toolkit documentation links remain usable after removing the mail runtime.

The source-owned mail and toolkit skills also pass the skill validator. Regression tests parse both portable configuration examples through the adapter without invoking credentials, and check local links and anchors in source and deployed documentation. Strict subprocess fixtures cover all exposed read operations on Himalaya 2.1.0 and 2.2.1, including the latter's additional mailbox-role and queued-count fields. They verify that changing the backend version preserves an existing unreviewed receipt. Unsupported releases are rejected before network operations; fake-process tests alone do not establish server behavior.

These tests use disposable paths. They do not operate on the configured production backup store.

## Real iCloud acceptance

Native materialization is established only when a disposable fixture first reaches `SF_DATALESS`. Record expected hashes before eviction, evict only the fixture through Foundation's public API, and confirm the native flag before running the release binary. A successful acceptance run must clear the flag, complete stable reads, and match the independent hashes. A second run must still read every file while making no unnecessary download request.

Initial acceptance met those conditions for a nested fixture. It also materialized one metadata-selected, pre-existing data-less archive file without evicting or changing that archive content. A disposable background LaunchAgent completed the same installed-binary read path, which establishes operation in that tested noninteractive environment. Fixture names, hashes, and machine logs remain private and outside Git.

For request-ahead acceptance, compare release-binary `--download-ahead 1` and `4` on equivalent cold multi-file disposable fixtures. Use repeated runs where iCloud upload and eviction state permits. Select `4` as the released default only if the median wall time improves repeatably by at least 20% with no correctness or reliability regression. Otherwise retain `1` and report whether the measurement was inconclusive or below threshold. Never use backup sources, an archive, or the Restic store as benchmark fixtures. See the [request-ahead decision](decisions/adr-003-download-ahead.md) for the design rationale.

On 2026-09-16, three pairs of disposable fixtures each had 12 uploaded files evicted and confirmed `SF_DATALESS` before the release-binary run. Window `1` took 11.550, 11.155, and 11.112 seconds; window `4` took 2.979, 3.177, and 3.479 seconds. The medians were 11.155 and 3.177 seconds, a 71.5% reduction. All six runs verified all 12 files with hashing off, cleared the data-less flags, matched independent pre-eviction hashes afterward, and made no new requests on rerun. These measurements support default `4` for this tested fixture and machine; they do not guarantee the same improvement for other trees or iCloud conditions.

Backup acceptance has exercised native eviction, incremental capture, deleted-file recovery, full checking, prune, cloud-only source reads, a scheduled capture, and separate verified restores. Those observations establish the tested local paths, not a remote transactional guarantee.

## Bounded live mail acceptance

On 2026-10-09, bounded live acceptance with Himalaya 2.1.0 and 2.2.1 verified IMAP authentication, mailbox enumeration, one inbox envelope page, a full raw-message read, and attachment metadata. Message flags before and after each read were unchanged. Fetched receipts remained unreviewed; no review acknowledgment or mail mutation was issued. The checks used isolated private review state rather than advancing an operational review ledger. The 2.2.1 check also exercised a bounded search and confirmed that routing, account configuration, operational review state, and local schedules were unchanged.

Before enabling 2.2.1, review of its tagged command implementations and the installed binary's generated JSON schemas confirmed the fields consumed by the adapter. Additional envelope queued-count and mailbox-role fields are compatible with the existing result checks. Raw IMAP reads still use `BODY.PEEK[]` when `--seen` is absent; see [the versioned IMAP implementation](https://github.com/pimalaya/himalaya/blob/v2.2.1/src/imap/backend.rs).

This establishes the exercised account and command paths, not exhaustive mailbox coverage, every provider behavior, unsupported Himalaya releases, or an AI review judgment. Attachment downloads and failure injection are covered with disposable fixtures; the live check did not download attachments or change credentials. Private account details, message data, and acceptance output remain outside Git.

## Tested platform boundary

| Check | Platform | Toolchain |
| --- | --- | --- |
| Initial materializer | Apple silicon (`arm64`), macOS 26.6.2 | Apple Swift 6.4, macOS 26.5 SDK |
| Materializer 1.1.0 | Apple silicon, macOS 27.0 | Apple Swift 6.4, macOS 26.5 SDK |
| Disposable CLI regression suite | Apple silicon, macOS 27.0.1 | Apple Swift 6.4, discovered macOS 27.0 SDK |

The disposable CLI checks establish build and local-file behavior; the real-iCloud acceptance above remains the evidence for hydration. The package declares macOS 13 as its deployment target, but macOS 13–15 runtime behavior and Intel hardware remain untested.

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
