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

### Architecture Decisions
- Extracted shared code into `census/` package (io.py, scan.py, scan_local.py, scan_s3.py, lod.py)
- This is a general-purpose public GitHub tool, NOT Netflix-specific
- FSx auto-detection must work on standard EC2/EKS, gracefully degrade elsewhere
