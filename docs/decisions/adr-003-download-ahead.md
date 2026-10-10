# ADR-003: Bounded iCloud download requests ahead of verification

**Status:** Accepted

**Date:** 2026-09-16

## Context

Serial request-then-read fetch can wait for each cloud-only file before asking iCloud for the next one. A tree with many such files may spend substantial time waiting, although a complete read of every file remains necessary for the local-readability guarantee.

## Decision

Use a configurable per-root window of 1 through 16 explicit per-file requests. Request candidates from a complete inventory, keep at most that many successful requests awaiting verification, and replenish after each requested read attempt. Keep reads in inventory order and root workers serial. Retain whole-root and per-request free-space checks and require a stable final inventory. The released default is `4`: three cold real-iCloud fixture pairs showed a 71.5% median wall-time reduction from `1`, with all contents verified. [Validation](../validation.md#real-icloud-acceptance) owns the measurements.

## Alternatives and consequences

Requesting the entire tree at once would leave an unbounded number of downloads after interruption. Concurrent coordinated reads add mutable-state and cancellation risks without being needed to offer iCloud overlapping requests. Concurrent root workers could each approve overlapping demand against the same free capacity. The bounded window limits ahead-of-read requests, but Apple does not guarantee parallel transfers and a timeout cannot revoke submitted daemon work. Mostly-local trees and single large files may not improve.
