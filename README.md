# DiskVis

**[ryanndagreat.github.io/DiskVis](https://ryanndagreat.github.io/DiskVis)**

<img width="1728" height="947" alt="DiskVis treemap view" src="https://github.com/user-attachments/assets/8174c2c7-7ad7-4ae8-b424-9abd3573df94" />

![Screen Recording 2026-02-06 at 5 52 06 AM](https://github.com/user-attachments/assets/a6513803-b77a-48b8-8e51-539132312f43)
![Screen Recording 2026-02-06 at 6 12 40 AM](https://github.com/user-attachments/assets/9a42e400-ae38-4276-9999-5795bff94083)

Visualize disk usage with interactive treemaps (like GrandPerspective) and sunburst charts (like DaisyDisk) — directly in your browser, for any filesystem, even remote ones. Zoomable, pannable, 120fps smooth — great on Mac trackpads.

## Quick Start

**1. Scan your disk**
```bash
pip install fire zstandard
python scan_census.py scan /
```
This creates `census_output.census.zst` — a compressed list of every file and its size. Scans are resumable: if interrupted, just re-run and it picks up where it left off.

You can scan a subset instead:
```bash
python scan_census.py scan /Users --output=users.census.zst
```

**2. (Optional) Reduce for large drives**

Full scans of large drives can produce 50MB+ files that slow down the browser. Use LOD (Level of Detail) to prune small files:
```bash
python lod_census.py lod census_output.census.zst --target=500000
```
This creates a smaller `census_output.lod.census.zst`. Adjust `--target` up/down to control detail level (higher = more files kept, larger file).

To find a good target value for your scan:
```bash
python lod_census.py analyze census_output.census.zst
```

**3. View in browser**

Open [ryanndagreat.github.io/DiskVis](https://ryanndagreat.github.io/DiskVis) and drag your `.census.zst` file onto the page.

## CLI Reference

### scan_census.py

| Command | Description |
|---------|-------------|
| `scan <root> [--output=FILE]` | Scan directory tree, save as `.census.zst` |
| `load <file>` | Print stats from a census file |
| `merge <folder> --output=FILE` | Merge checkpoint folder into single file |

Key flags for `scan`:
- `--checkpoint_interval=100000` — directories between checkpoint saves
- `--one_file_system=True` — don't cross mount points
- `--disk_usage=True` — actual disk usage (False = apparent file size)
- `--report_errors` — print permission/access errors

### lod_census.py

| Command | Description |
|---------|-------------|
| `lod <file> [--target=500000]` | Reduce entry count by aggregating small files into folders |
| `analyze <file>` | Show size distribution to help choose a target |
