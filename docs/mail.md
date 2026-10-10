# Himalaya mail companion

`icloud-mail` (`icmail`) is an optional companion that requires Python 3.11 or later. It supports Himalaya **2.1.0 and 2.2.1** for IMAP, message parsing, and authentication. Other releases require contract verification before use. It needs neither Restic nor the Swift materializer and creates no scheduled jobs. Terminal users and agents use the same command contract.

## Setup and credentials

There are three separate owners:

| Artifact | Owner |
| --- | --- |
| This repository | Portable adapter, installation code, contracts, agent instructions, and examples |
| `~/.config/icloud-utils/mail.toml` | Private routing: account key, backend executable/configuration path, state directory, and timeout |
| Selected Himalaya configuration and credential provider | Account identity, preferred sending alias, IMAP settings, and authentication |

Keep the real configuration, identities, passwords, Keychain exports, bodies, attachments, review notes, and machine state outside Git. The adapter does not copy the Himalaya configuration or execute its password command itself. Himalaya consumes that command internally. Repository publication does not publish local configuration.

If Himalaya already works, use [migration](#migration-and-rollback) before installing; keep the working account and credential command. For a new account, complete the following steps from this checkout. The examples use placeholder identities and home-relative paths; they contain no personal configuration. The toolkit currently supports IMAP accounts using `imap.sasl.plain.username`.

### Install and check prerequisites

1. Install Python 3.11 or later and a [supported Himalaya release](#himalaya-mail-companion) with IMAP support. Homebrew is one installation option:

   ```sh
   brew install python himalaya
   ```

2. Check the commands that your shell selects:

   ```sh
   python3 --version
   himalaya --version
   command -v himalaya
   ```

   Confirm that `python3` is version 3.11 or later and Himalaya reports a supported version. If a package manager supplies an unsupported release, select a tested binary such as [the upstream 2.2.1 release](https://github.com/pimalaya/himalaya/releases/tag/v2.2.1). Package upgrades do not widen the adapter's support. Use the discovered executable path when installing, so Apple silicon and Intel Homebrew prefixes both work.

For other installation methods, see [Himalaya's versioned guide](https://github.com/pimalaya/himalaya/blob/v2.2.1/README.md).

### Create the private account configuration

1. For a first installation, prepare dedicated private directories:

   ```sh
   umask 077
   mkdir -p "$HOME/.config/himalaya" "$HOME/.config/icloud-utils"
   chmod 700 "$HOME/.config/himalaya" "$HOME/.config/icloud-utils"
   ```

2. Copy the [Himalaya example](../example.himalaya.toml) without overwriting an existing file:

   ```sh
   (set -C; cat example.himalaya.toml > "$HOME/.config/himalaya/config.toml")
   chmod 600 "$HOME/.config/himalaya/config.toml"
   ```

   If the copy refuses an existing file, stop and follow [Migration and rollback](#migration-and-rollback).

3. Edit the private copy. Replace `primary@example.invalid` with the mailbox's primary login and Keychain account label, and `preferred-alias@example.invalid` with your preferred sender identity. The account key `icloud` is a local routing name, not a login address. Keep it consistent with toolkit `mail.account`.

Apple specifies TLS IMAP on `imap.mail.me.com:993`; its [server settings](https://support.apple.com/en-us/102525) describe using the address's local part as the username and trying the full address if needed. Preserve a working login; the example uses the full-address form. The `email` sender alias may differ from that login. Inbox is explicit. Add other aliases only after enumerating actual mailbox names. Local sort fallback avoids iCloud's advertised but ineffective server sorting; see [the upstream iCloud report](https://github.com/pimalaya/himalaya/blob/v2.1.0/cairn/spec/testing/imap-icloud.md). The command-array credential syntax is documented in [Himalaya's versioned configuration sample](https://github.com/pimalaya/himalaya/blob/v2.2.1/config.sample.toml).

### Store an app-specific password in a credential provider

If you already have a working credential provider, retain it. For a new iCloud credential using macOS Keychain:

1. Enable Apple Account two-factor authentication. Sign in at [account.apple.com](https://account.apple.com/), then choose **Sign-In and Security** > **App-Specific Passwords** > **Generate an app-specific password**. Follow [Apple's instructions](https://support.apple.com/en-us/102654); use that password instead of the primary Apple Account password.

2. Create one generic-password item with the same account label and service as the example. Replace the placeholder label before running:

   ```sh
   /usr/bin/security add-generic-password -a 'primary@example.invalid' -s himalaya-icloud -w
   ```

   Leave `-w` last, with no password argument, to enter the secret at the hidden prompt. This behavior is documented by the local `man security`. Omit `-U` for creation so an existing item is not silently replaced. Never put the password in shell arguments, history, TOML, environment variables, logs, or Git. Let Himalaya consume the credential command's output; do not run its `find-generic-password ... -w` command for display.

For an already configured password manager, replace the Keychain command array as shown in the account example. The provider must return only the secret on `stdout` and work under the user running Himalaya. Himalaya owns invocation; the adapter and installer do not execute that command themselves.

Keep credential access limited to the intended local user. Review any Keychain permission prompt for the expected `/usr/bin/security` helper; do not grant all applications access with `-A`. A locked login keychain or a prompt that cannot be answered in a background session needs an interactive check before automation. This installer does not unlock keychains or change access policies.

### Verify Himalaya

Check the selected account directly once, with logging disabled:

```sh
himalaya --json --log-level off --config "$HOME/.config/himalaya/config.toml" \
  --account icloud --backend imap account check
```

Require exactly the selected IMAP backend with `ok: true`; a zero exit status alone is insufficient. Keep diagnostics private and stop after rejected authentication to inspect the login, credential label/service, and Keychain access. Do not repeatedly retry an invalid password.

Then follow [Install and update](#install-and-update) to create toolkit routing and stable commands.

### Verify the installed commands and private permissions

1. Check the selected routing, authentication, and one mailbox page:

   ```sh
   ~/bin/icmail --json status
   ~/bin/icmail --json doctor
   ~/bin/icmail --json mailboxes
   ~/bin/icmail --json list --mailbox INBOX --page 1 --page-size 5
   ```

   `status` should show the intended `configuration`, `himalayaConfig`, account, state directory, and an existing documentation file, with `networkChecked: false`. It checks the backend version locally without invoking its credential provider. `doctor` must return `success: true` after an actual IMAP authentication check. A mailbox page verifies access, not complete coverage.

2. Inspect private configuration permissions. For custom paths, substitute those paths:

   ```sh
   stat -f '%Sp %N' "$HOME/.config/himalaya" "$HOME/.config/himalaya/config.toml" \
     "$HOME/.config/icloud-utils" "$HOME/.config/icloud-utils/mail.toml"
   ```

   Private configuration directories should be `0700`, and files `0600`. New review state is created privately on the first state-writing operation; an existing state directory or ledger with wider permissions is refused without a silent chmod.

3. Optional: Select one positive UID from that mailbox page and run `~/bin/icmail --json read --mailbox INBOX MESSAGE_UID`, replacing the placeholder. It returns the full raw message and an unreviewed local receipt while preserving unread state. Keep bodies and receipts private. Do not acknowledge a receipt merely to test setup; acknowledgment belongs to an actual completed review. Authentication and listing create no receipt.

## Toolkit routing reference

The toolkit routing settings are:

| `[mail]` key | Meaning/default |
| --- | --- |
| `account` | Required existing Himalaya account key; not an email address |
| `himalaya_config` | Required absolute or `~/` path to one private Himalaya configuration |
| `himalaya` | Executable path or command name; default `himalaya` |
| `state_dir` | Absolute or `~/` path; default `~/Library/Application Support/icloud-utils/mail/state` |
| `timeout_seconds` | Per-child deadline from 1 through 300; default 30 |

The portable [routing example](../example.mail.toml) contains no identity or credentials. Unknown toolkit settings, invalid table shapes, control characters in routing, and malformed aliases are rejected rather than becoming an alternate password store. The adapter explicitly selects this account/configuration and the IMAP backend for every operation.

## Install and update

Installation provides stable `~/bin/` commands and optional agent skills that work after the checkout moves. To run without installation, create private routing from the [routing example](../example.mail.toml), then use `python3 -B icloud_mail.py --config /absolute/path/to/mail.toml --json status` from the checkout.

For an existing installation, follow [Migration and rollback](#migration-and-rollback) to preserve its selected paths. For a new installation, run from the source checkout:

```sh
scripts/install-mail --config "$HOME/.config/icloud-utils/mail.toml" --account icloud \
  --himalaya "$(command -v himalaya)" --himalaya-config "$HOME/.config/himalaya/config.toml" \
  --skills-dir "$HOME/.codex/skills"
```

Then [verify the installed commands and private permissions](#verify-the-installed-commands-and-private-permissions).

`--skills-dir` installs or updates the source-owned `icloud-mail` and `icloud-utils` skills. Omit it to install only commands and their documentation. The installer retains existing matching skills in the private migration directory and refuses unrelated skills. Both skills reference shared toolkit documentation outside the mail runtime, including the portable examples and recovery guide. Removing the mail runtime leaves that guidance usable.

The installer creates a private toolkit configuration only if it is absent. For that first creation, `--account`, `--himalaya`, and `--himalaya-config` select existing account routing. An existing toolkit configuration is validated and preserved, so edit that private file deliberately when changing routing. An explicit `--config` selects a toolkit file; otherwise an update preserves the path embedded in the managed launcher, including a custom path. Only a fresh installation defaults to `~/.config/icloud-utils/mail.toml`. A missing discovered file or unrecognized launcher format fails before replacement and requires restoration or an explicit `--config`. The installer never reads or rewrites the Himalaya account file, exports a credential, changes backup configuration, reloads jobs, or creates a scheduler.

| Installed artifact | Purpose |
| --- | --- |
| `~/bin/icloud-mail` and relative alias `~/bin/icmail` | Stable launcher and shorthand |
| `~/Library/Application Support/icloud-utils/mail/runtime/` | Generated mail adapter only |
| `~/Library/Application Support/icloud-utils/documentation/` | Toolkit documentation, linked examples, and recovery guide; independent of mail runtime |
| `~/Library/Application Support/icloud-utils/migration/` | Previous toolkit documentation trees |
| `~/Library/Application Support/icloud-utils/mail/state/` | Default private review ledger and lock |
| `~/Library/Application Support/icloud-utils/mail/migration/` | Previous managed runtime, launchers, toolkit routing, and replaced skills |

### Remove the mail companion

First stop any separately configured mail automation. Remove only the identified generated mail launcher/alias, runtime, and installed `icloud-mail` instructions. Retain the `icloud-utils` skill and toolkit documentation for the other components. Keep credentials, private routing, and review history unless you intend to delete them. Backup and materializer installations are independent.

## Troubleshooting

| Symptom or error stage | Check and next step |
| --- | --- |
| `dependency` or `compatibility` | Check the configured executable's `--version`; select a [supported release](#himalaya-mail-companion). Creation flags do not change an existing toolkit file; edit its `himalaya` routing deliberately. |
| `configuration`, or unexpected selected paths | Inspect `status` and the generated launcher's `--config` argument if status fails. Toolkit routing has only `[mail]`; Himalaya has `[accounts.KEY]`. Check table shapes, account key, absolute/`~/` paths, `imap.sasl.plain.username`, aliases, and `imap.sort.fallback = true`. |
| Installer cannot discover routing | Restore the missing selected file or rerun with its explicit `--config`. Do not accept a default file as evidence of the previous selection. |
| `authentication`, including account check exit 0 | Inspect `backends[].ok`; confirm the primary login and matching credential account/service. Check app-specific password validity once. Primary Apple Account password changes revoke app-specific passwords; see [Apple's app-specific password instructions](https://support.apple.com/en-us/102654). Rotate only the intended item when necessary. |
| Keychain item missing or access denied | Check item metadata with `security find-generic-password -a 'primary@example.invalid' -s himalaya-icloud >/dev/null`, without `-w` or `-g`. Verify the current user's unlocked keychain and an interactive helper permission prompt. Do not export secrets or grant blanket access. |
| `timeout` or `backend` | Check network/TLS reachability and the explicit account directly. Backend streams are withheld because they may contain private data. Do not paste trace logs into Git or assume a partial response completed review. |
| `state` with wide permissions | Inspect the selected state directory and `reviews.json`; after confirming ownership, deliberately restrict the directory to `0700` and ledger to `0600`. The adapter does not repair existing permissions. |
| Corrupt or incompatible ledger | Preserve the original file and its private recovery copies. Inspect schema/identity/outcome inconsistencies offline; do not delete/reset state to make the error disappear. Recover a known-good copy only after reconciling pending actions. |
| `identity` after a mailbox change | Retrieve a fresh bounded page. UIDVALIDITY changes create distinct identities; never carry a token or UID across mailbox epochs or login/server changes. |
| Search parse error or surprising results | Use a single quoted Himalaya DSL expression such as `subject invoice`, not bare free text. Dates use author headers; pages can shift during deliveries and are not a complete review watermark. |
| Installed documentation link missing | Update from a complete checkout with the same routing and optional `--skills-dir`. Do not point toolkit references back into `mail/runtime`; the documentation tree is toolkit-owned. |

## Migration and rollback

For an existing account on a supported Himalaya release, retain the actual configuration path, account key, login, preferred `email` alias, and working credential command. Inspect locally rather than copying identity or secrets into the checkout. Merge missing inbox alias/sort-fallback settings into that selected account only, keeping all other accounts and any SMTP settings. An old pre-2.x schema needs deliberate conversion against [the versioned sample](https://github.com/pimalaya/himalaya/blob/v2.2.1/config.sample.toml); do not run a wizard over the working file or assume old keys still apply. Keep a private `0600` recovery copy before a manual edit, then [verify Himalaya](#verify-himalaya).

For an installed companion, discover `configuration` and `stateDirectory` through `icmail --json status`. If it cannot run, inspect the generated launcher to find its selected toolkit path. Retain that routing file and review state, then run:

```sh
scripts/install-mail --config /absolute/path/to/existing/mail.toml --skills-dir "$HOME/.codex/skills"
~/bin/icmail --json review status
```

Replace the placeholder with the discovered path. Then [verify the installed commands and private permissions](#verify-the-installed-commands-and-private-permissions). Subsequent updates may omit `--config` because the managed launcher preserves it. If an earlier installer already changed the selection, explicitly choose the intended retained file; discovery cannot reconstruct a lost choice. Existing credentials and review history need no conversion when account namespace and state directory stay the same. Renaming an account key alone retains the server/login namespace, but changing the login spelling or server changes receipt scope; do not rewrite old receipts to force a match.

Updating an older companion moves its managed runtime/docs into private migration storage and creates the toolkit documentation tree. Supplying `--skills-dir` regenerates both skills against that tree and retains previous matching skills. Omit it only if no instruction update is intended. The installer does not read the Himalaya account file, modify credentials, or load backup jobs.

For rollback, install a retained known-good source revision with the same explicit toolkit path and state directory. Inspect migration copies if installation failed partway; command, runtime, documentation, and skill replacement are individually atomic, not one transaction. Do not restore an older ledger over newer pending actions without reconciliation.

Existing schedules and earlier review/coverage records are independently owned. This installer does not create, disable, or migrate a schedule, and it does not import historical coverage as acknowledged receipts. If a scheduler is deliberately migrated later, inspect its current command and policy, preserve pending obligations, verify the new read-only path, and switch the existing schedule without creating a duplicate. Changing credentials or schedules is a separate explicit operation.

## Commands

Global `--json` and `--config PATH` go **before** the subcommand. JSON output is one document with `schemaVersion`, adapter `version`, `success`, and either `operation`/`data` or `error.stage`/`error.message`. Exit 0 means that operation succeeded, 1 means failure, 2 means usage error, and interruption returns the conventional signal status. Success is not an assertion that an inbox is clear.

| Command | Effect |
| --- | --- |
| `icmail --json status` | Inspect selected toolkit configuration path, backend compatibility, deployed documentation path, and review state; no network |
| `icmail --json doctor` | Check IMAP connectivity/authentication; inspect the backend's `ok`, not just its exit code |
| `icmail --json mailboxes` | List accessible mailboxes |
| `icmail --json list --mailbox INBOX --page 1 --page-size 20` | One bounded envelope page |
| `icmail --json search --mailbox INBOX --query 'subject invoice'` | One bounded page using Himalaya's search DSL |
| `icmail --json read --mailbox INBOX UID` | Full raw-message string, stable IMAP identity, and a local fetch receipt; no seen-state mutation |
| `icmail --json attachments --mailbox INBOX UID` | Attachment metadata; no disk download |
| `icmail --json download --mailbox INBOX UID --part PART_ID --target /new/directory` | Download explicitly selected attachment parts to a new directory |
| `icmail --json review status` | Local unreviewed receipts and unresolved actions; no network |
| `icmail --json review ack TOKEN --outcome clear` | Explicitly acknowledge that this fetched message was reviewed |
| `icmail --json review ack TOKEN --outcome needs-attention --note 'action' --due 2026-12-01` | Acknowledge review and retain a private action/deadline |
| `icmail --json review resolve TOKEN` | Resolve a previously acknowledged action locally |

Mailboxes are explicit; configured aliases resolve to native names. Message IDs are positive IMAP UIDs, not table positions or sequence numbers. Attachment IDs are MIME part positions returned by Himalaya. Repeat `--part` to select additional parts; omitting it does not silently download everything. Destinations must be new, their parent must exist, and partial files can remain after a failed download. Results are checked for requested IDs, contained regular paths, and byte sizes. This does not authenticate the content or make an attachment trustworthy.

Pages contain at most 100 envelopes. `mayHaveMore` is conservative; a full page may have a successor. Pagination is not an atomic server snapshot and concurrent deliveries/moves can shift pages. Date queries use the author's `Date:` header, not a guaranteed arrival watermark. Keep queries bounded to the task and use relevant mailbox/thread context; do not infer exhaustive coverage from one page or from header-only screening.

The adapter constructs a fixed set of backend commands with argument arrays and no shell. It does not expose `--seen`, flag changes, moves, deletion, sending, arbitrary IMAP commands, or generic argument passthrough. This boundary applies to the adapter; the independently installed Himalaya program remains capable of mutation. Failed or malformed backend results are errors, and raw backend streams are withheld from wrapper errors because they may contain private data. Diagnose locally through Himalaya when more detail is necessary.

## Review receipts and actionable checks

Fetching and review are distinct. A read stores metadata and a SHA-256 of the JSON raw-message string; it does not store the body or mark the message reviewed. That digest is an integrity check for the returned representation, not proof of exact original wire bytes or accurate interpretation. Receipt identity uses the IMAP server/login namespace, canonical mailbox, UIDVALIDITY, and UID. The namespace is hashed in private state; credentials are not included. [IMAP identity rules](https://www.rfc-editor.org/rfc/rfc9051.html#section-2.3.1.1) explain why UIDVALIDITY is needed. Mailbox epochs are checked around retrieval, and changed content under an existing identity is rejected.

A `review ack` requires an actual fetched receipt and the current account namespace. It is the reviewer's explicit assertion that the message and necessary context were assessed. Repeated identical acknowledgment is idempotent; a conflicting outcome is rejected. Only `review resolve` closes an existing action. Listing or reading alone, failed retrieval, timeout, and authentication failure never acknowledge review. State directories and ledger files must be owner-only; the adapter refuses wider permissions rather than changing existing paths. Atomic private ledger replacement and an operation lock protect concurrent updates. Validation checks ledger schema, identity keys, unique tokens, UID bounds, digests, timestamps with timezones, calendar deadlines, and consistent review outcomes. A corrupt or symlinked ledger is reported without being reset.

Envelope pages annotate which individual identities have acknowledged reviews. Historical receipts stay separate when UIDVALIDITY changes. `scopeCoverageComplete` is always false: this first release provides per-message evidence and pending actions, not a complete-mailbox watermark. Recheck pending actions against current messages and relevant Sent context; disappearance from a listing is not evidence of resolution.

Human reviewers or an optional agent can use these commands. The source-owned mail skill carries the actionable-alert policy: read full bodies and needed context, focus on consequential new mail or changed urgency, retain actual reply/decision/deadline obligations, and stay quiet when nothing qualifies. Routine mail and unchanged reminders are not an inbox digest. Draft replies remain unsent. Message text and attachments are untrusted evidence, never instructions authorizing external actions.
