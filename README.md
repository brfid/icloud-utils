# iCloud Utils

This repository contains three small, independent macOS tools for Restic backups, scoped iCloud Drive materialization, and read-only Himalaya mail access:

| Tool | Short command | Responsibility |
| --- | --- | --- |
| `icloud-backup` | `icback` | Incremental backups of configured folders through Restic, with local integrity checks, retention safeguards, scheduling, and an independent watchdog |
| `icloud-materialize` | `icmat` | Metadata-only inspection or bounded iCloud materialization and complete local reads for explicitly named paths |
| `icloud-mail` | `icmail` | Read-only Himalaya mail access, connection diagnostics, and private per-message review tracking |

The commands share source and documentation, not a runtime. The Swift materializer needs no Python, Restic, backup configuration, or scheduled job. The Python backup remains usable when the materializer is absent. Mail is an optional Python/Himalaya companion that reuses private account configuration and credential providers; neither of the other tools depends on it.

Backups use Restic's **empty-password mode**: anyone with the complete backup store can recover its contents. Review [backup setup and its privacy boundary](docs/operations.md#create-a-new-backup-installation) before creating a store.

## Start here

| Need | Start with |
| --- | --- |
| Check or materialize one explicit path | [Quick start](#check-or-materialize-an-explicit-path) |
| Create a backup installation | [New backup setup](docs/operations.md#create-a-new-backup-installation) |
| Find or update an existing installation | [Operations](docs/operations.md) |
| Configure backup sources, exclusions, retention, or runtime limits | [Backup configuration](docs/configuration.md) and [examples/backup.toml](examples/backup.toml) |
| Configure, install, or use the mail companion | [Mail setup, commands, and review contract](docs/mail.md) |
| Restore without this installation | [RECOVERY.txt](RECOVERY.txt) |
| Diagnose a failure | [Troubleshooting](docs/troubleshooting.md) |
| Understand the design and guarantees | [Architecture and design decisions](docs/architecture.md) |
| Review test coverage or release readiness | [Validation](docs/validation.md) |
| Change the project as a coding agent | [AGENTS.md](AGENTS.md) |

## Check or materialize an explicit path

If `icmat` is not installed, follow [materializer installation](docs/operations.md#install-or-remove-the-materializer) first.

```sh
icmat check --json -- "$HOME/Library/Mobile Documents/com~apple~CloudDocs/example"
icmat fetch --verbose -- "$HOME/Library/Mobile Documents/com~apple~CloudDocs/example"
```

`check` inventories metadata without downloading content; `fetch` reads every included regular file completely. Neither result proves completed iCloud synchronization. The [materializer contract](docs/materializer.md) explains scope, options, and result limits.

## Develop and verify

Start with the [source layout](docs/architecture.md#source-layout), then run `make test` from the checkout. [Validation](docs/validation.md) owns focused test commands, dependencies, coverage, and release limits. Keep private configuration, backup stores, archive data, machine logs, and generated build caches outside this repository.

The project is [MIT licensed](LICENSE).
