---
name: icloud-utils
description: Install, update, or diagnose the iCloud Utils toolkit and its independently installed backup, materializer, and Himalaya mail components. Use for toolkit management; ordinary inbox or explicitly named file tasks use the relevant component workflow.
---

# iCloud Utils

The repository owns portable code, operating contracts, and agent instructions. Live routing, account identity, credential providers, review state, and backup configuration remain private outside Git. The installed tools run independently of the checkout and each other.

Read [Operations](../../docs/operations.md) to discover installed paths before an update. Backup deployment preserves the discovered configuration and job labels; mail installation is separate and never changes backup jobs. Use [Architecture](../../docs/architecture.md) for component boundaries and [Mail](../../docs/mail.md) for the Himalaya companion, credential separation, and local review tracking. Change source-owned skills here and deploy them with the documented installer rather than editing generated installed copies.

For explicitly named iCloud Drive paths, use `icmat check --json -- PATH...` for metadata only or `icmat fetch --json -- PATH...` for complete local reads. Add `--sha256` only when a digest is requested. Preserve exact scope and keep local readability separate from synchronization status. Consult [the materializer contract](../../docs/materializer.md) for incomplete results and resource limits.

For mail, use `icmail --json` with an explicit mailbox. Read full messages without changing unread state; acknowledge review receipts only after the review is complete. Setup, commands, and actionable-alert guidance live in [the mail contract](../../docs/mail.md) and the source-owned mail skill.
