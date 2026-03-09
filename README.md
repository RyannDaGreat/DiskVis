# DiskVis

[![Website](https://img.shields.io/badge/🌐_Website-DiskVis-green?style=for-the-badge)](https://ryanndagreat.github.io/DiskVis)

<img width="1728" height="947" alt="DiskVis treemap view" src="https://github.com/user-attachments/assets/8174c2c7-7ad7-4ae8-b424-9abd3573df94" />

![Screen Recording 2026-02-06 at 5 52 06 AM](https://github.com/user-attachments/assets/a6513803-b77a-48b8-8e51-539132312f43)
![Screen Recording 2026-02-06 at 6 12 40 AM](https://github.com/user-attachments/assets/9a42e400-ae38-4276-9999-5795bff94083)
<img width="1088" height="924" alt="image" src="https://github.com/user-attachments/assets/05f51c02-0621-4066-b9de-6b2b89e6232d" />


Visualize disk usage with interactive treemaps (like GrandPerspective) and sunburst charts (like DaisyDisk) — directly in your browser, for any filesystem, even remote ones. Zoomable, pannable, 120fps smooth — great on Mac trackpads.

## Quick Start

**1. Install dependencies**
```bash
pip install fire zstandard boto3
```

**2. Scan your disk**
```bash
# Scan a local directory
python scan_census.py scan /

# Scan a specific folder
python scan_census.py scan /home --output=home.jsonl.zst

# Scan an S3 bucket (much faster than scanning a FUSE/FSx mount)
python scan_census.py scan-s3 s3://my-bucket/data/ --output=bucket.jsonl.zst --workers=16
```

Scans are resumable: if interrupted, re-run and it picks up where it left off.

**3. (Optional) Reduce for large scans**

Scans with millions of files can produce large census files. Use LOD to aggregate small files into folder entries:
```bash
python scan_census.py lod scan.jsonl.zst --target=500000
```
This creates `scan.lod.jsonl.zst`. To find a good target:
```bash
python scan_census.py analyze scan.jsonl.zst
```

**4. View in browser**

Open [ryanndagreat.github.io/DiskVis](https://ryanndagreat.github.io/DiskVis) and drag your `.jsonl.zst` file onto the page.

## Examples

### Scan a local drive
```bash
$ python scan_census.py scan /home/alice --output=alice.jsonl.zst
Scanning /home/alice...
  1,000 dirs, 12,481 files, 8.3 GB...
  2,000 dirs, 31,205 files, 24.1 GB...
Done: 45,000 files, 52.30 GB
Saved: alice.jsonl.zst (0.42 MB)
```

### Scan an S3 bucket
```bash
$ python scan_census.py scan-s3 s3://my-data-lake/datasets/ --workers=16
Scanning s3://my-data-lake/datasets/ with 16 workers...
Discovering prefixes...
Found 24 prefixes to scan
    datasets/images/: 10,000 objects, 45.2 GB...
    datasets/images/: 20,000 objects, 91.0 GB...
  [1/24] 128,400 files, 512.3 GB (prefix: datasets/images/)
  [2/24] 245,100 files, 1024.7 GB (prefix: datasets/video/)
  ...
Done: 3,860,000 files, 1098860.34 GB
Saved: census_output.jsonl.zst (33.06 MB)
```

S3 scanning uses `ListObjectsV2` with parallel prefix fan-out — orders of magnitude faster than scanning a FUSE mount.

### Reduce with LOD
```bash
$ python scan_census.py lod census_output.jsonl.zst --target=500000
Loaded 3,859,985 files
Size threshold: 690,693,439 bytes (674505.3 KB)
Large files (kept): 400,001
Small files aggregated into 100,000 folders
Size coverage: 100.0%
Saved: census_output.lod.jsonl.zst (4.31 MB)
```

### Inspect a census file
```bash
$ python scan_census.py load alice.jsonl.zst
Root: /home/alice
Scanned: Mon Mar  9 01:10:05 2026
Files: 45,000
Total: 52.30 GB
```

## When to use `scan` vs `scan-s3`

| Situation | Command |
|-----------|---------|
| Local drive (SSD, HDD) | `scan /path` |
| NFS / network mount | `scan /mnt/nfs` |
| S3 bucket | `scan-s3 s3://bucket/prefix` |
| FSx for Lustre mount | `scan-s3 s3://backing-bucket/prefix` (if you know the S3 URI) |
| FSx for Lustre mount | `scan /fsx` (works but slow on deeply nested trees) |

For FSx for Lustre, scanning the backing S3 bucket directly is much faster because `ListObjectsV2` returns thousands of objects per API call, whereas the Lustre mount requires per-file `stat` calls over the network.

## CLI Reference

| Command | Description |
|---------|-------------|
| `scan <root> [--output=FILE]` | Scan local directory tree |
| `scan-s3 <s3_uri> [--output=FILE] [--workers=16]` | Scan S3 bucket via ListObjectsV2 |
| `lod <file> [--target=500000]` | Reduce entry count by aggregating small files |
| `analyze <file>` | Show size distribution to help choose LOD target |
| `load <file>` | Print stats from a census file |
| `merge <folder> --output=FILE` | Merge checkpoint folder into single file |

### Flags for `scan` (local)
- `--checkpoint_interval=100000` — directories between checkpoint saves
- `--one_file_system=True` — don't cross mount points
- `--disk_usage=True` — actual disk usage (False = apparent file size)
- `--report_errors` — print permission/access errors

### Flags for `scan-s3`
- `--workers=16` — parallel listing threads
- `--checkpoint_interval=100000` — objects between checkpoint saves

## Census Format

Census files are zstd-compressed JSONL (`.jsonl.zst`):
- Line 1: `{"root": "/path", "timestamp": 1234567890, "count": N}`
- Lines 2+: `[inode, size, "/path/to/file"]`

For S3 scans, inode is always 0 and paths are S3 keys.
