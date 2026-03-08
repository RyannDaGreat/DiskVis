# DiskVis Concerns Log

## 2026-03-08: FSx Scanning Investigation

### Problem
Local scan (`scan_census.py scan /fsx --one_file_system=False`) reported zero files despite /fsx having thousands of files. Dir count kept incrementing but file count stayed at 0.

### Investigation Steps
- `/fsx` is Lustre mount (`none /fsx lustre rw,...`)
- Mount source is `none` — Titus container hides actual mount source
- `lfs` tools not installed
- `aws fsx describe-file-systems` returns empty — IAM role lacks permissions
- `/fsx/manta/mesa` has 4561 dir entries, known to contain files
- Need to test: does `os.scandir` + `is_file()` work on Lustre with `lazystatfs`?

### Findings
- `/fsx` dirs appear empty at shallow levels (e.g., `/fsx/manta/mesa/dagobah-batch=...` shows 0 files)
- But deeper scanning DOES find files: 2,981 files after ~3,000 dirs in `/fsx/manta/diffusion`
- The "zero files" issue was NOT a bug — just deeply nested data with many empty dir stubs
- Local scanning works on Lustre, it's just slow due to deep nesting

### Lustre Mount Detection (working)
- `/proc/mounts` shows `none` as source on this machine (Titus container)
- `/sys/fs/lustre/llite/<name>-<kernelptr>/kbytestotal` matches `os.statvfs().f_blocks * f_frsize / 1024`
- This statvfs matching reliably maps Lustre short names to mount points
- Server NIDs from `/proc/fs/lustre/mdc/<name>-MDT/import` are IPs only (no DNS)
- 4 Lustre filesystems detected: 3i7nbbmv→/fsx, uvltbbev→/fsx_vfx, yan4fbev→/fsx_scanline, ecfdtb4v→/fsx_genai

### FSx → S3 Detection Chain Status
- Path → mount point: TESTED ✓
- Mount → Lustre name via statvfs: TESTED ✓
- Mount source → FSx ID via DNS regex: TESTED ✓ (pure function, but mount source is 'none' here)
- Lustre name → FSx ID via AWS API: UNTESTED (no fsx:DescribeFileSystems permission)
- FSx ID → S3 URI via API: UNTESTED (no permissions)

### Architecture Decisions
- Extracted shared code into `census/` package (io.py, scan.py, scan_local.py, scan_s3.py, lod.py)
- This is a general-purpose public GitHub tool, NOT Netflix-specific
- FSx auto-detection must work on standard EC2/EKS, gracefully degrade elsewhere
- Cardinal rule added: never commit untested code, mark UNTESTED functions explicitly
