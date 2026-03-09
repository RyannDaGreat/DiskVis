# DiskVis — Manifest

## Purpose
General-purpose disk usage visualization tool. Scans directory trees (local filesystems, AWS FSx, S3 buckets) into a compact census format, then visualizes them in an interactive HTML treemap.

**Problem being solved:** Understanding where disk space is used across large, distributed storage — local drives, NFS mounts, FSx for Lustre, and S3 buckets. Existing tools (du, ncdu) are slow on network filesystems and can't handle S3. FUSE mounts for S3/FSx are painfully slow for recursive listing and often miss files.

**Solution:** A scanner that auto-detects the storage backend and uses the fastest available method (os.scandir for local, ListObjectsV2 for S3, etc.), producing a unified census format that the HTML frontend can visualize.

## Glossary
- **census** — compressed JSONL file (.census.zst) containing a list of [inode, size, path] entries representing every file in a scanned tree. The canonical data format of this project.
- **LOD (Level of Detail)** — reduced census where small files are aggregated into parent-folder entries to hit a target entry count, for faster visualization.
- **checkpoint** — intermediate census file saved during a long scan, enabling resume after interruption.
- **FSx for Lustre** — AWS managed Lustre filesystem, often backed by an S3 bucket. Can be mounted on EC2/EKS instances.
- **FUSE mount** — userspace filesystem mount (e.g., s3fs, goofys, mountpoint-s3) that exposes S3 as a local directory. Slow for recursive listing.

## Cardinal Rules
1. **This is a general-purpose public tool.** No Netflix-specific code, no hardcoded paths, no internal dependencies. Everything must work on any AWS account, any EC2 instance, any developer's laptop.
2. **Read-only scanning.** The scanner NEVER writes to the filesystem being scanned. Only reads. Outputs go to the local working directory or an explicit output path.
3. **No files modified outside this repository.** Treat this as a portable dump — all outputs, checkpoints, and temp files stay within the repo or system temp dirs.
4. **No silent failures.** Errors fail loudly. If a scan finds zero files, that's a bug, not a success.
5. **Graceful degradation.** Auto-detection features (FSx→S3, mount detection) must work when possible and clearly report why they can't when they fail. Never silently fall back.
6. **Never commit untested code.** Every function must be tested before committing. If you can't test it, don't write it. If you write it and it fails, don't commit it. If you can only test a subset, only commit that subset. Untested functions must be clearly marked `# UNTESTED` in comments and docstrings — never claim something works unless you verified it.

## File Structure
```
DiskVis/
├── census/
│   ├── __init__.py      — CLI routing (Fire dict), load/merge commands
│   ├── io.py            — Census file I/O (save, load, merge)
│   ├── scan.py          — Shared scan infrastructure (checkpoints, progress, resume)
│   ├── scan_local.py    — Local filesystem DFS scanner
│   ├── scan_s3.py       — S3 ListObjectsV2 parallel scanner
│   ├── aws.py           — AWS utilities (FSx detection, S3 URI discovery)
│   └── lod.py           — LOD reducer and analyzer
├── scan_census.py       — Thin CLI entry point
├── index.html           — Visualization frontend (treemap)
├── assets/              — Frontend assets
├── requirements.txt     — Python dependencies
├── claude_instructions.md — This file
└── concerns.md          — Historical progress/issues log
```

## Census Format
Zstd-compressed JSONL:
- Line 1: `{"root": "/path", "timestamp": 1234567890, "count": N}`
- Lines 2+: `[inode, size, "/path/to/file"]`
  - inode: filesystem inode (0 for S3/aggregated entries)
  - size: bytes (disk usage for local, apparent size for S3)
  - path: absolute path or S3 key

## Coding Conventions
- **CQS labels**: Every function labeled Pure/Query/Command in docstring
- **DRY**: Shared scan logic in scan.py, shared I/O in io.py
- **No magic numbers**: All constants named at top of file
- **Doctests**: All pure functions have `>>>` examples
- **Fire CLI**: Not argparse
- **No silent failures**: Errors reported, never swallowed

## Current State
- Local scanner: working (tested on local dirs)
- S3 scanner: written, untested (needs boto3)
- FSx auto-detection: IN PROGRESS
- LOD reducer: working
- HTML frontend: existing (index.html)

## FSx Auto-Detection Design
`detect_s3_backing(path) → s3://bucket/prefix | None`

Chain: mount path → mount point + source + fs_type → FSx filesystem ID → S3 data repository association → S3 URI

Must handle:
- Standard EC2 mounts (source contains FSx DNS with filesystem ID)
- EKS/container mounts (source may be `none` — try lfs tools)
- No AWS permissions (return None, don't crash)
- Non-FSx paths (return None immediately)
