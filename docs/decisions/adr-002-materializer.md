# ADR-002: Native explicit-path iCloud materializer

**Status:** Accepted

**Date:** 2026-09-13

## Context

The backup runner can let a child Restic process download data-less files while reading, but that process-local policy cannot prepare arbitrary paths for another sibling process. The toolkit needs an independently usable metadata check and complete-read materializer without turning backup storage, scheduling, or retention into a general filesystem tool.

## Decision

Keep backup in Python with Restic and add `icloud-materialize` as a Swift executable using Foundation, Darwin, and CryptoKit with no third-party packages. Place both sources in the history-preserving `icloud-utils` repository while installing and operating them independently. Integrate backup only through an early, thin `exec` of the known companion.

Workers contain all potentially blocking operations on supplied paths. The parent bounds and reaps them. Check disables download-on-read and never reads payloads. Fetch enables the policy, uses public iCloud download requests and coordinated complete reads, and reports local readability separately from freshness. Restic continues to own snapshot storage and integrity; the backup runner retains its small child-policy helper, and the watchdog remains import-independent.

## Consequences

Contributors touching both tools need Python and Swift toolchains, while users of a prebuilt materializer need neither. Complete verification can consume substantial I/O, time, network, and local capacity even on a rerun because already-local files are reread. A stable local inventory is not an atomic or remote snapshot. Binary signing, notarization, cross-version builds, and per-architecture testing remain release work rather than implicit consequences of a local build.
