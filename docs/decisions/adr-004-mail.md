# ADR-004: Optional Himalaya mail tooling

**Status:** Accepted

**Date:** 2026-10-09

## Context

The project owner wants the existing Himalaya mail workflow included in iCloud Utils, with portable tooling and agent instructions published in the public repository and working private configuration updated locally. The toolkit already packages independently installed backup and materialization tools. Credentials and account identity must remain separate from the repository.

## Decision

Add `icloud-mail` (`icmail`) as an optional Python companion that delegates protocols, MIME, attachment retrieval, and authentication to external Himalaya. Initially support the inspected 2.1.0 command/JSON contract explicitly. Reuse the selected private Himalaya account and its credential provider. Toolkit configuration stores routing and limits only.

The adapter exposes bounded listing/search, full reads preserving seen state, selected attachment downloads to new directories, connection diagnostics, and local review acknowledgments. Construct allowlisted commands without shell execution or unrestricted argument forwarding. Sending, moves, deletion, and flag changes are outside this interface.

Keep retrieval separate from review. Store per-message metadata receipts using an account namespace, canonical mailbox, UIDVALIDITY, and UID. Acknowledge only through an explicit reviewer operation; retain unreviewed receipts and unresolved actions. Do not claim complete mailbox coverage or introduce an arrival watermark based on author-supplied dates. This conservative per-message contract avoids falsely completing a partial review.

Own portable mail instructions in [Mail](../mail.md) and the repository's mail/toolkit skills. Deploy generated skills and documentation through the independent installer. Keep installed programs usable without the checkout, and keep mail configuration/state/dependencies independent of backup and materializer jobs. Existing schedulers remain private and are not recreated by installation. AI review is optional.

## Options and consequences

A separate global skill requires less project code but leaves workflow ownership outside the toolkit. A thin companion provides consistent discovery, setup, diagnostics, and review evidence with a small adapter to maintain. Bundling/forking Himalaya or merging mail into the backup runner adds upstream maintenance and couples unrelated failure paths. Use the thin companion.

Himalaya upgrades require contract testing before widening the supported version. Private routing, bodies, attachments, review notes, credential material, and machine inventories stay outside Git. Installation preserves existing routing and working authentication rather than creating another credential store. [Validation](../validation.md) owns tested behavior and acceptance limits.

## Evidence

- [Existing independent companion precedent](adr-002-materializer.md).
- [Himalaya 2.1.0 documentation](https://github.com/pimalaya/himalaya/blob/v2.1.0/README.md) and [iCloud test report](https://github.com/pimalaya/himalaya/blob/v2.1.0/cairn/spec/testing/imap-icloud.md).
- [IMAP identity rules](https://www.rfc-editor.org/rfc/rfc9051.html#section-2.3.1.1).
- [Apple app-specific password requirements](https://support.apple.com/en-us/102654).
