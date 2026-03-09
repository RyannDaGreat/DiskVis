"""Test census/aws.py detection functions on this machine."""
import sys
sys.path.insert(0, '.')
from census.aws import (
    parse_proc_mounts,
    find_mount_for_path,
    is_lustre_mount,
    extract_fsx_id_from_dns,
    extract_region_from_dns,
    get_lustre_filesystems,
    match_lustre_to_mount,
    get_lustre_server_nids,
    get_s3_uri_from_fsx,
    _adjust_s3_uri_for_subpath,
    _find_lustre_name_for_mount,
    detect_s3_backing_verbose,
)

print("=== parse_proc_mounts ===", flush=True)
mounts = parse_proc_mounts()
lustre_mounts = [m for m in mounts if m['fs_type'] == 'lustre']
print(f"  Total mounts: {len(mounts)}", flush=True)
print(f"  Lustre mounts: {len(lustre_mounts)}", flush=True)
for m in lustre_mounts:
    print(f"    {m['mount_point']} (source={m['source']})", flush=True)

print("\n=== find_mount_for_path ===", flush=True)
for path in ['/fsx', '/fsx/manta', '/fsx_vfx', '/tmp', '/root']:
    m = find_mount_for_path(path)
    if m:
        print(f"  {path} → {m['mount_point']} ({m['fs_type']})", flush=True)
    else:
        print(f"  {path} → None", flush=True)

print("\n=== is_lustre_mount ===", flush=True)
for path in ['/fsx', '/tmp']:
    m = find_mount_for_path(path)
    if m:
        print(f"  {path}: is_lustre={is_lustre_mount(m)}", flush=True)

print("\n=== extract_fsx_id_from_dns ===", flush=True)
test_cases = [
    'fs-0abc123.fsx.us-east-1.amazonaws.com@tcp:/fsx',
    'fs-0abc123.fsx.us-east-1.amazonaws.com',
    'none',
    '10.0.1.5@tcp',
    '100.85.79.197@tcp',
]
for tc in test_cases:
    print(f"  '{tc}' → {extract_fsx_id_from_dns(tc)}", flush=True)

print("\n=== get_lustre_filesystems ===", flush=True)
fs = get_lustre_filesystems()
for name, info in fs.items():
    print(f"  {name}: kbytes={info.get('kbytestotal', '?'):,}, uuid={info.get('uuid', '?')}", flush=True)

print("\n=== match_lustre_to_mount ===", flush=True)
for name, info in fs.items():
    mount_point = match_lustre_to_mount(name, info, lustre_mounts)
    print(f"  {name} → {mount_point}", flush=True)

print("\n=== get_lustre_server_nids ===", flush=True)
for name in fs:
    nids = get_lustre_server_nids(name)
    print(f"  {name}: {nids}", flush=True)

print("\n=== _find_lustre_name_for_mount ===", flush=True)
for m in lustre_mounts:
    name = _find_lustre_name_for_mount(m)
    print(f"  {m['mount_point']} → lustre_name={name}", flush=True)

print("\n=== detect_s3_backing_verbose ===", flush=True)
for path in ['/fsx', '/fsx/manta', '/tmp']:
    print(f"\n  --- {path} ---", flush=True)
    result = detect_s3_backing_verbose(path)
    for step in result['steps']:
        print(f"    {step}", flush=True)
    print(f"    RESULT: {result['result']}", flush=True)
    print(f"    lustre_name: {result['lustre_name']}", flush=True)
    print(f"    fsx_id: {result['fsx_id']}", flush=True)

print("\n=== _adjust_s3_uri_for_subpath ===", flush=True)
cases = [
    ('s3://bucket/prefix', '/fsx', '/fsx/manta/data'),
    ('s3://bucket', '/fsx', '/fsx'),
    ('s3://bucket/', '/fsx', '/fsx/sub'),
]
for s3, mp, path in cases:
    print(f"  ({s3}, {mp}, {path}) → {_adjust_s3_uri_for_subpath(s3, mp, path)}", flush=True)

print("\nDone.", flush=True)
