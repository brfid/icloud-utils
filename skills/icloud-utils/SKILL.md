---
name: icloud-utils
description: Check or materialize explicitly named macOS iCloud paths with icmat, or route the same operation through icloud-backup, while interpreting incomplete results conservatively.
---

# iCloud Utils

Use `icmat check --json -- PATH...` for a metadata-only inventory. It disables automatic materialization, makes no download request, and does not read payloads. A successful check can list data-less files; never describe it as byte verification.

Use `icmat fetch --json --sha256 -- PATH...` only when the user wants complete local reads/materialization. It rereads already-local regular files, can consume network, disk, time, and iCloud activity, and requires enough immediately free capacity beyond its reserve. Do not broaden explicit paths, pass `--allow-icloud-root`, evict files, or modify archive contents without direct authorization.

Treat `success: false`, `enumerationComplete: false`, any failure, timeout, unstable inventory, unreadable file, or remaining data-less candidate as incomplete. Keep local readability separate from `freshness`: stale or unknown status is not synchronized success. Hashes record bytes read only; compare them with an independent expected digest to establish a match.

`icloud-backup materialize check|fetch ...` is a thin route to the installed native companion. Do not use the backup-specific `download` command for arbitrary paths; it verifies only the configured Restic repository.
