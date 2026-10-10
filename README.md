# iCloud Utils

This repository contains three small, independent macOS tools for Restic backups, scoped iCloud Drive materialization, and read-only Himalaya mail access:

| Tool | Short command | Responsibility |
| --- | --- | --- |
| `icloud-backup` | `icback` | Incremental backups of configured folders through Restic, with local integrity checks, retention safeguards, scheduling, and an independent watchdog |
| `icloud-materialize` | `icmat` | Metadata-only inspection or bounded iCloud materialization and complete local reads for explicitly named paths |
| `icloud-mail` | `icmail` | Read-only Himalaya mail access, connection diagnostics, and private per-message review tracking |

The commands share source and documentation, not a runtime. The Swift materializer needs no Python, Restic, backup configuration, or scheduled job. The Python backup remains usable when the materializer is absent. Mail is an optional Python/Himalaya companion that reuses private account configuration and credential providers; neither of the other tools depends on it.

## Start here

| Need | Start with |
| --- | --- |
| Check or materialize one explicit path | [The examples below](#check-or-materialize-an-explicit-path), then the [materializer contract](docs/materializer.md) |
| Create a backup installation | [Create a new backup installation](#create-a-new-backup-installation) |
| Find or update an existing installation | [Operations](docs/operations.md) |
| Configure backup sources, exclusions, retention, or runtime limits | [Backup configuration](docs/configuration.md) and [examples/backup.toml](examples/backup.toml) |
| Configure, install, or use the mail companion | [Mail setup, commands, and review contract](docs/mail.md) |
| Restore without this installation | [RECOVERY.txt](RECOVERY.txt) |
| Diagnose a failure | [Troubleshooting](docs/troubleshooting.md) |
| Understand the design and guarantees | [Architecture](docs/architecture.md), [storage decision](docs/decisions/adr-001-storage.md), [materializer decision](docs/decisions/adr-002-materializer.md), [request-ahead decision](docs/decisions/adr-003-download-ahead.md), and [mail decision](docs/decisions/adr-004-mail.md) |
| Review test coverage or release readiness | [Validation](docs/validation.md) |
| Change the project as a coding agent | [AGENTS.md](AGENTS.md) |

## Check or materialize an explicit path

```sh
icmat check --json -- "$HOME/Library/Mobile Documents/com~apple~CloudDocs/example"
icmat fetch --verbose -- "$HOME/Library/Mobile Documents/com~apple~CloudDocs/example"
```

`check` inventories accessible metadata without requesting downloads or reading payloads. `fetch` reads every included regular file to EOF, including already-local files. Add `--sha256` explicitly when a digest is needed; ordinary fetch leaves hashing off. Both commands require explicit paths and refuse the whole iCloud Drive root or an ancestor unless `--allow-icloud-root` is supplied deliberately.

The same operation is available through the backup command when both tools are installed:

```sh
icback materialize check --json -- /explicit/path
icback materialize fetch --verbose -- /explicit/path
```

Read the [materializer contract](docs/materializer.md) before treating a result as evidence of readability or synchronization.

## Use the mail companion

For a first account, follow [Mail setup](docs/mail.md#setup-and-credentials), then [Install and update](docs/mail.md#install-and-update) for stable commands and optional agent skills. You can also run the adapter directly from the checkout. For an existing installation, start with [Migration and rollback](docs/mail.md#migration-and-rollback). Account identity and credentials remain in Himalaya's private configuration and credential provider.

## Create a new backup installation

This project uses Restic's **empty-password mode**. There is no separate recovery secret; anyone with the complete backup store can recover its contents. Privacy therefore depends on Mac and iCloud access controls. Backup success establishes local creation and checking, not completed iCloud upload.

New installations require macOS with iCloud Drive signed in, Python 3.11 or later, and Restic 0.19.1 or later. Homebrew can supply the dependencies with `brew install python restic`.

Copy [examples/backup.toml](examples/backup.toml) to `~/.config/icloud_backup/config.toml`, replace its example sources, and keep that private file outside Git. Then run from this checkout:

```sh
scripts/install-backup --initialize
~/bin/icloud-backup status
```

Initialization creates and registers a new backup store. Do not use it to update or reconnect an existing installation. For those workflows, first discover the active paths and job labels through [Operations](docs/operations.md).

Installation copies the backup programs to stable locations, installs `icloud-backup` and `icback`, and loads the daily job and independent watchdog. The installed commands do not depend on this checkout.

## Develop and verify

Start with the [source layout](docs/architecture.md#source-layout), then run `make test` from the checkout. [Validation](docs/validation.md) owns focused test commands, dependencies, coverage, and release limits. Keep private configuration, backup stores, archive data, machine logs, and generated build caches outside this repository.

The project is [MIT licensed](LICENSE).
