# iCloud Utils

Small, independent macOS tools for two explicit jobs:

| Tool | Short command | Responsibility |
| --- | --- | --- |
| `icloud-backup` | `icback` | Incremental backups of configured folders through Restic, with local integrity checks, retention safeguards, scheduling, and an independent watchdog |
| `icloud-materialize` | `icmat` | Metadata-only inspection or bounded iCloud materialization and complete local reads for explicitly named paths |

The commands share a repository and documentation, not a runtime. The Swift materializer needs no Python, Restic, backup configuration, or scheduled job. The Python backup remains usable when the materializer is absent.

## Materialize an explicit path

```sh
icmat check --json -- "$HOME/Library/Mobile Documents/com~apple~CloudDocs/example"
icmat fetch --sha256 -- "$HOME/Library/Mobile Documents/com~apple~CloudDocs/example"
```

`check` disables automatic materialization and inventories accessible metadata. Its success does not certify byte readability. `fetch` reads every included regular file to EOF, including already-local files, and hashes those same reads when requested. Freshness is reported separately: a locally readable file can still have stale or unknown iCloud status. Both commands require explicit paths and refuse the whole iCloud Drive root or an ancestor unless `--allow-icloud-root` is supplied deliberately.

See [Materializer](docs/materializer.md) for the complete contract, limits, JSON schema, costs, and build/install instructions.

## Back up configured folders

This project deliberately uses Restic's **empty-password mode**. There is no backup password or separate recovery secret to manage; anyone with access to the complete backup store can recover its contents. It relies on Mac and iCloud access controls. Reported backup success means local creation and checks passed; iCloud upload completion is not monitored.

New backup installations require macOS with iCloud Drive signed in, Python 3.11 or later, and Restic 0.19.1 or later. Homebrew can supply the dependencies with `brew install python restic`.

Copy [example.config.toml](example.config.toml) to `~/.config/icloud_backup/config.toml` and replace its example sources with real folders. Keep that private configuration outside Git. Then, from this checkout:

```sh
python3 install.py --initialize
~/bin/icloud-backup status
```

Installation copies the backup programs to stable locations, installs `icloud-backup` and `icback`, loads the daily job and independent watchdog, and starts the first backup when due. The installed command and jobs use the same explicit configuration path. Deleting or moving the checkout does not disable them.

**Already installed?** Follow [the update procedure](docs/operations.md#update-an-existing-backup-installation), preserving the configuration path and job labels reported by the current installation. Editing this checkout alone does not update installed programs.

The backup exposes the materializer without duplicating it:

```sh
icloud-backup materialize check --json -- /explicit/path
icloud-backup materialize fetch --sha256 -- /explicit/path
```

That dispatch happens before backup configuration or Restic state is opened. It invokes the known installed companion and preserves the materializer's arguments, output, signals, and exit status.

## Documentation

| Need | Owner |
| --- | --- |
| Build, install, use, or interpret `icloud-materialize` / `icmat` | [Materializer](docs/materializer.md) |
| Configure backup sources, exclusions, retention, and runtime limits | [Configuration](docs/configuration.md) and its executable example |
| Find installed copies and jobs; update, operate, or uninstall tools | [Operations](docs/operations.md) |
| Understand component boundaries, completion, and safeguards | [Architecture](docs/architecture.md) |
| Recover after losing the original Mac | [RECOVERY.txt](RECOVERY.txt), also exported beside the backup store |
| Understand the Restic storage tradeoffs | [Storage decision](docs/adr-001-storage.md) |
| Understand the native materializer decision | [Materializer decision](docs/adr-002-materializer.md) |
| Make a change as a coding agent | [AGENTS.md](AGENTS.md) |

`make test` builds the Swift command and runs disposable Swift-CLI, backup, retention, wrapper, alias, and installation tests. No private configuration, backup store, archive data, or machine logs belong in this repository. The project is [MIT licensed](LICENSE).
