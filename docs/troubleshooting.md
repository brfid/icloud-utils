# Troubleshooting

Start with the installed command rather than the checkout:

```sh
~/bin/icloud-backup status --json
```

That result identifies the active configuration, installed runner, Python interpreter, job labels, and current problems. Follow [Operations](operations.md#identify-the-active-installation) if status itself cannot run. Preserve the private configuration and Restic store while diagnosing; do not initialize a replacement store.

## Cloud-only reads fail

A `resource deadlock avoided` error can mean the installed runner lacks the explicit download-on-read policy described in [Architecture](architecture.md#icloud-and-space). Confirm the installed version, then inspect iCloud availability and the exact failing path. Finder's Download Now on the backup store can recover an immediate run.

Verify a repair through the scheduled job, not only a terminal command:

```sh
launchctl kickstart gui/$(id -u)/BASE_LABEL
```

Wait for fresh success for every configured source. A terminal and a LaunchAgent can have different macOS access.

## The scheduled job lacks access

macOS attributes the job's permissions to its installed Python interpreter. In System Settings > Privacy & Security > Files & Folders, check that interpreter's iCloud Drive access and its Desktop access when the warning file is stored there. A Python upgrade can create a new interpreter identity and require renewed approval.

A Desktop prompt after a failure may come from writing the alert rather than from reading a backup source. Compare log times before diagnosing it. The runner still needs ordinary authorization for protected files; do not grant Full Disk Access merely to change download-on-read policy.

## JSON parsing fails after a retry

An `Expecting value` error after otherwise successful Restic retries can identify an older runner that mixed diagnostic stderr with JSON stdout. Version 2.0.1 and later separate those streams. Update through the [existing-installation procedure](operations.md#update-an-existing-backup-installation), retaining the discovered configuration and labels. Do not initialize a new store or remove repository keys.

## Restic reports an active lock

The runner verifies the registered store without taking a Restic lock, asks Restic to remove only locks that Restic considers stale, and waits up to three minutes for an active external operation. A lock failure after that bound protects the other operation. Let it finish and retry later.

Do not use `restic unlock --remove-all` or delete files from the repository's `locks` directory. If no operation appears active, inspect the exact processes and timestamps before deciding that a lock is abandoned.

## Collect evidence safely

Backup logs are under `~/Library/Logs/`; recent engine-command logs are under the configured state directory. Status and logs can contain private paths and filenames. Keep them outside Git and redact them before sharing.

Record the observed error, installed version, selected job label, whether the failure occurred in a terminal or scheduled job, and the last known successful snapshot time. Keep observed state separate from a proposed cause until the evidence connects them.
