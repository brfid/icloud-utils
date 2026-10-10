# ADR-004: Optional Himalaya mail tooling

**Status:** Proposed

**Date:** 2026-10-09

**Decider:** Project owner

## Context

The project owner wants iCloud Utils to include the existing Himalaya mail workflow as part of an integrated toolkit. The repository currently packages backup and materialization as independently installed tools. A mail companion would give portable setup, operations, and review instructions an authoritative project home while preserving that independence.

## Proposed decision

Add an independently installable `icloud-mail` companion that delegates mail protocol operations to an external, released Himalaya binary. Keep Himalaya responsible for IMAP, MIME, attachments, and account authentication. Reuse its selected private configuration and existing credential command; do not create another account configuration or copy credentials into toolkit state.

Integrate component discovery, installation instructions, diagnostics, and agent guidance at the toolkit level. Mail installation is optional. Missing Himalaya, failed authentication, or a mail-review failure must not prevent backup or materialization from running. Start with a small companion and the existing commands; a new umbrella dispatcher is a later interface choice, not a prerequisite.

The first mail interface should support account diagnostics, bounded mailbox listing/search, message reads that preserve seen flags, and explicitly requested attachment downloads. Use structured Himalaya output and an allowlist of supported operations and arguments. Do not expose arbitrary protocol commands or unrestricted argument forwarding through a supposedly read-only interface. Sending, moving, deleting, or changing flags would require a separately designed and authorized capability.

Keep message retrieval distinct from review. Local code can collect and track messages without an AI runtime. The existing actionable-alert workflow can use those results through an optional agent, with its policy and prompt owned in project source. Fetching a body or listing headers does not establish that a message was reviewed. Persist review coverage only after successful review acknowledgment, retain incomplete work for retry, and distinguish connection failure from a quiet successful review. Specify stable account/mailbox/message identity and mailbox-reset handling before implementing incremental coverage.

Account identities, credentials, bodies, attachments, review state, logs, and machine-specific schedules remain private and outside Git. Keep mail state and scheduling independent of backup state and jobs. Portable mail setup and operating instructions should have one project owner document; installed agent guidance should link to or be deployed from that source rather than becoming a second editable manual.

## Options considered

| Option | Benefit | Cost or limitation |
| --- | --- | --- |
| Retain a separate Himalaya skill and workflow | Least new code | Setup and review instructions remain outside the toolkit's source and release process |
| Add an optional thin Himalaya companion | Integrated discovery and reproducible workflow while retaining independent tools | Maintain a small adapter, private review-state contract, and compatibility tests |
| Bundle or fork Himalaya, or merge mail into the backup runner | One larger implementation surface | Adds upstream maintenance and couples unrelated dependencies and failure paths |

The thin companion is recommended. Its value should come from workflow guarantees, diagnostics, and reproducible setup rather than merely renaming every Himalaya command.

## Consequences and implementation boundary

Himalaya upgrades require compatibility checks for command syntax, JSON results, account-check error handling, sorting, and unread-state preservation. The initially inspected release is 2.1.0; this proposal does not claim compatibility with every 2.x version. Test supported behavior before widening the version range.

This is a proposed design only. It does not implement or deploy mail tooling, change account configuration, or create or migrate a schedule.

## Next step and acceptance

Agree the first companion interface and the owner of review acknowledgment, then implement the smallest useful command set, opt-in installation, and canonical mail documentation/agent guidance. Verify read-state preservation, partial retrieval/review recovery, backend failures represented in JSON, and independence from the other toolkit commands. Use disposable fixtures for failure tests and bounded, non-mutating live acceptance where needed.

## Evidence

- [Current toolkit boundaries](architecture.md) and [independent companion precedent](adr-002-materializer.md).
- [Himalaya 2.1.0 documentation](https://github.com/pimalaya/himalaya/blob/v2.1.0/README.md) documents JSON output and shared private account configuration.
- [Himalaya 2.1.0 iCloud test report](https://github.com/pimalaya/himalaya/blob/v2.1.0/cairn/spec/testing/imap-icloud.md) records non-mutating message reads and the iCloud sorting workaround. Installed `message read --help` independently confirms that seen state is unchanged when `--seen` is omitted; this is documentation evidence, not a new live mailbox acceptance run.
- [Apple app-specific passwords](https://support.apple.com/en-us/102654) owns the account authentication requirements; Himalaya consumes the configured credential command.
