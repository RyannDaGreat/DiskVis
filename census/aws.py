#!/usr/bin/env python3
"""
AWS utilities for detecting FSx for Lustre mounts and their S3 backing.

Detection chain:
    path → mount_point → lustre_name → fsx_filesystem_id → s3_uri

Each step can fail gracefully (returns None) if permissions or tools are missing.
"""
from __future__ import annotations

import os
import re
from typing import Optional


# =============================================================================
# PURE FUNCTIONS
# =============================================================================

def parse_proc_mounts() -> list[dict]:
    """
    Query (reads /proc/mounts). Parse all mounts into structured records.

    Returns:
        list[dict]: Each dict has keys 'source', 'mount_point', 'fs_type', 'options'.

    Examples:
        >>> mounts = parse_proc_mounts()
        >>> all('mount_point' in m for m in mounts)
        True
    """
    mounts = []
    with open('/proc/mounts') as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 4:
                mounts.append({
                    'source': parts[0],
                    'mount_point': parts[1],
                    'fs_type': parts[2],
                    'options': parts[3],
                })
    return mounts


def find_mount_for_path(path: str) -> Optional[dict]:
    """
    Query (reads /proc/mounts). Find the mount record that contains a given path.

    Walks up from path to find the longest matching mount point.

    Args:
        path (str): Absolute path to look up.

    Returns:
        dict | None: Mount record with 'source', 'mount_point', 'fs_type', 'options',
            or None if not found.

    Examples:
        >>> m = find_mount_for_path('/')
        >>> m is not None
        True
        >>> 'mount_point' in m
        True
    """
    path = os.path.abspath(path)
    mounts = parse_proc_mounts()

    best_match = None
    best_len = 0
    for m in mounts:
        mp = m['mount_point']
        if path == mp or path.startswith(mp + '/'):
            if len(mp) > best_len:
                best_match = m
                best_len = len(mp)
    return best_match


def is_lustre_mount(mount: dict) -> bool:
    """
    Pure. Check if a mount record is a Lustre filesystem.

    Args:
        mount (dict): Mount record from parse_proc_mounts.

    Returns:
        bool

    Examples:
        >>> is_lustre_mount({'fs_type': 'lustre', 'source': 'none', 'mount_point': '/fsx', 'options': ''})
        True
        >>> is_lustre_mount({'fs_type': 'ext4', 'source': '/dev/sda1', 'mount_point': '/', 'options': ''})
        False
    """
    return mount.get('fs_type') == 'lustre'


def extract_fsx_id_from_dns(dns_name: str) -> Optional[str]:
    """
    Pure. Extract FSx filesystem ID from a DNS name.

    On standard EC2 mounts, the source is like:
        fs-0abc123def456.fsx.us-east-1.amazonaws.com@tcp:/mountname
    or just the DNS name.

    Args:
        dns_name (str): Mount source or DNS string.

    Returns:
        str | None: FSx filesystem ID like 'fs-0abc123def456', or None.

    Examples:
        >>> extract_fsx_id_from_dns('fs-0abc123.fsx.us-east-1.amazonaws.com@tcp:/fsx')
        'fs-0abc123'
        >>> extract_fsx_id_from_dns('fs-0abc123.fsx.us-east-1.amazonaws.com')
        'fs-0abc123'
        >>> extract_fsx_id_from_dns('none')
        >>> extract_fsx_id_from_dns('10.0.1.5@tcp:/3i7nbbmv')
    """
    match = re.search(r'(fs-[0-9a-f]+)\.fsx\.[a-z0-9-]+\.amazonaws\.com', dns_name)
    if match:
        return match.group(1)
    return None


def extract_region_from_dns(dns_name: str) -> Optional[str]:
    """
    Pure. Extract AWS region from an FSx DNS name.

    Args:
        dns_name (str): DNS name containing region.

    Returns:
        str | None: Region like 'us-east-1', or None.

    Examples:
        >>> extract_region_from_dns('fs-0abc.fsx.us-west-2.amazonaws.com')
        'us-west-2'
        >>> extract_region_from_dns('none')
    """
    match = re.search(r'\.fsx\.([a-z0-9-]+)\.amazonaws\.com', dns_name)
    if match:
        return match.group(1)
    return None


# =============================================================================
# QUERIES - Lustre proc filesystem
# =============================================================================

def get_lustre_filesystems() -> dict[str, dict]:
    """
    Query (reads /sys/fs/lustre). Discover all Lustre filesystems on this machine.

    Uses /sys/fs/lustre/llite/ to find filesystem names and their metadata.
    Falls back to /proc/fs/lustre/ if /sys path doesn't exist.

    Returns:
        dict: lustre_name -> {'kbytes_total': int, 'files_total': int, 'uuid': str}
            Empty dict if no Lustre proc info found.

    Examples:
        >>> fs = get_lustre_filesystems()
        >>> isinstance(fs, dict)
        True
    """
    # Try /sys/fs/lustre/llite first (more common on modern kernels)
    for base in ['/sys/fs/lustre/llite', '/proc/fs/lustre/llite']:
        if not os.path.exists(base):
            continue

        result = {}
        for entry in os.listdir(base):
            # Entry format: "3i7nbbmv-ffff9dffed310000" (name-kernelptr)
            lustre_name = entry.split('-')[0]
            entry_path = os.path.join(base, entry)

            info = {}
            for key in ['kbytestotal', 'filestotal', 'uuid']:
                fpath = os.path.join(entry_path, key)
                if os.path.exists(fpath):
                    with open(fpath) as f:
                        val = f.read().strip()
                    if key in ('kbytestotal', 'filestotal'):
                        info[key] = int(val)
                    else:
                        info[key] = val

            result[lustre_name] = info
        return result

    return {}


def match_lustre_to_mount(lustre_name: str, lustre_info: dict,
                          lustre_mounts: list[dict]) -> Optional[str]:
    """
    Query (reads filesystem stats). Match a Lustre filesystem name to its mount point.

    Uses statvfs to compare total capacity (kbytes) between the llite proc entry
    and each Lustre mount point. This works because each FSx filesystem has a
    unique total capacity.

    Args:
        lustre_name (str): Lustre short name (e.g., '3i7nbbmv').
        lustre_info (dict): Info from get_lustre_filesystems for this name.
        lustre_mounts (list[dict]): Lustre mount records from parse_proc_mounts.

    Returns:
        str | None: Mount point path, or None if no match found.
    """
    target_kb = lustre_info.get('kbytestotal')
    if target_kb is None:
        return None

    for m in lustre_mounts:
        try:
            svfs = os.statvfs(m['mount_point'])
            mount_kb = (svfs.f_blocks * svfs.f_frsize) // 1024
            if mount_kb == target_kb:
                return m['mount_point']
        except OSError:
            continue

    return None


def get_lustre_server_nids(lustre_name: str) -> list[str]:
    """
    Query (reads /proc/fs/lustre or /sys/fs/lustre). Get server NIDs for a Lustre filesystem.

    The MDC import info contains the server IP/hostname that can sometimes
    include the FSx DNS name.

    Args:
        lustre_name (str): Lustre short name.

    Returns:
        list[str]: Server NIDs (IP@tcp or hostname@tcp format).
    """
    nids = []
    for base in ['/proc/fs/lustre/mdc', '/sys/fs/lustre/mdc']:
        if not os.path.exists(base):
            continue
        for entry in os.listdir(base):
            if not entry.startswith(lustre_name):
                continue
            import_path = os.path.join(base, entry, 'import')
            if not os.path.exists(import_path):
                continue
            with open(import_path) as f:
                for line in f:
                    line = line.strip()
                    if 'current_connection:' in line:
                        nid = line.split('current_connection:')[-1].strip()
                        if nid and nid not in nids:
                            nids.append(nid)
                    elif 'failover_nids:' in line:
                        # Format: failover_nids: [ 10.0.1.5@tcp ]
                        match = re.findall(r'[\d.]+@\w+|[\w.-]+@\w+', line)
                        for n in match:
                            if n not in nids:
                                nids.append(n)
    return nids


# =============================================================================
# QUERIES - AWS API
# =============================================================================

def find_fsx_by_mount_name(lustre_name: str, region: Optional[str] = None) -> Optional[dict]:
    """
    Query (AWS API). Find an FSx filesystem by its Lustre mount name.

    UNTESTED — requires fsx:DescribeFileSystems IAM permission which was not
    available during development. Logic is straightforward (paginate + match
    MountName) but end-to-end verification is a TODO.

    Searches all FSx for Lustre filesystems in the account/region and matches
    on LustreConfiguration.MountName.

    Args:
        lustre_name (str): Lustre mount name (e.g., '3i7nbbmv').
        region (str | None): AWS region. None uses default.

    Returns:
        dict | None: FSx filesystem record, or None if not found or no access.
    """
    try:
        import boto3
    except ImportError:
        return None

    try:
        kwargs = {}
        if region:
            kwargs['region_name'] = region
        client = boto3.client('fsx', **kwargs)

        paginator = client.get_paginator('describe_file_systems')
        for page in paginator.paginate():
            for fs in page['FileSystems']:
                if fs.get('FileSystemType') != 'LUSTRE':
                    continue
                lustre_config = fs.get('LustreConfiguration', {})
                if lustre_config.get('MountName') == lustre_name:
                    return fs
    except Exception:
        # No permissions, no credentials, service error — all handled the same
        return None

    return None


def get_s3_uri_from_fsx(fsx_record: dict) -> Optional[str]:
    """
    Pure. Extract S3 import path from an FSx filesystem record.

    Checks both the legacy DataRepositoryConfiguration and the newer
    DataRepositoryAssociations.

    Args:
        fsx_record (dict): FSx filesystem record from describe-file-systems.

    Returns:
        str | None: S3 URI like 's3://bucket/prefix', or None if no S3 backing.

    Examples:
        >>> get_s3_uri_from_fsx({'LustreConfiguration': {'DataRepositoryConfiguration': {'ImportPath': 's3://my-bucket/data'}}})
        's3://my-bucket/data'
        >>> get_s3_uri_from_fsx({'LustreConfiguration': {}})
        >>> get_s3_uri_from_fsx({})
    """
    lustre_config = fsx_record.get('LustreConfiguration', {})

    # Legacy: DataRepositoryConfiguration (older FSx filesystems)
    drc = lustre_config.get('DataRepositoryConfiguration', {})
    import_path = drc.get('ImportPath')
    if import_path:
        return import_path

    return None


def get_s3_from_dra(fsx_id: str, region: Optional[str] = None) -> Optional[str]:
    """
    Query (AWS API). Get S3 URI from Data Repository Associations.

    UNTESTED — requires fsx:DescribeDataRepositoryAssociations IAM permission
    which was not available during development. TODO: verify on a machine with
    proper IAM roles.

    Newer FSx filesystems use DRAs instead of DataRepositoryConfiguration.

    Args:
        fsx_id (str): FSx filesystem ID.
        region (str | None): AWS region.

    Returns:
        str | None: S3 URI, or None.
    """
    try:
        import boto3
    except ImportError:
        return None

    try:
        kwargs = {}
        if region:
            kwargs['region_name'] = region
        client = boto3.client('fsx', **kwargs)

        response = client.describe_data_repository_associations(
            Filters=[{'Name': 'file-system-id', 'Values': [fsx_id]}]
        )
        for assoc in response.get('Associations', []):
            s3_config = assoc.get('S3', {})
            # DRA stores the full path differently
            data_repo_path = assoc.get('DataRepositoryPath')
            if data_repo_path:
                return data_repo_path
    except Exception:
        return None

    return None


# =============================================================================
# HIGH-LEVEL DETECTION
# =============================================================================

def detect_s3_backing(path: str) -> Optional[str]:
    """
    Query. Auto-detect the S3 URI backing a filesystem path.

    Attempts multiple detection strategies in order:
    1. Check if path is on a Lustre mount
    2. Try to extract FSx ID from mount source (standard EC2 mounts)
    3. Use Lustre proc to find filesystem name, then query AWS API
    4. Try Data Repository Associations API

    Args:
        path (str): Absolute path to check.

    Returns:
        str | None: S3 URI like 's3://bucket/prefix', or None if not detectable.
            Returns None (never raises) if any detection step fails.
    """
    path = os.path.abspath(path)

    # Step 1: Find the mount
    mount = find_mount_for_path(path)
    if mount is None or not is_lustre_mount(mount):
        return None

    # Step 2: Try extracting FSx ID directly from mount source
    # (works on standard EC2 mounts where source is the DNS name)
    fsx_id = extract_fsx_id_from_dns(mount['source'])
    region = extract_region_from_dns(mount['source'])

    if fsx_id:
        # Got the FSx ID directly — query for S3 backing
        fsx_record = _lookup_fsx_by_id(fsx_id, region)
        if fsx_record:
            s3_uri = get_s3_uri_from_fsx(fsx_record)
            if s3_uri:
                return _adjust_s3_uri_for_subpath(s3_uri, mount['mount_point'], path)
            # Try DRA
            s3_uri = get_s3_from_dra(fsx_id, region)
            if s3_uri:
                return _adjust_s3_uri_for_subpath(s3_uri, mount['mount_point'], path)

    # Step 3: Use Lustre proc to find the filesystem name
    lustre_name = _find_lustre_name_for_mount(mount)
    if lustre_name is None:
        return None

    # Try extracting FSx ID from server NIDs
    nids = get_lustre_server_nids(lustre_name)
    for nid in nids:
        fsx_id = extract_fsx_id_from_dns(nid)
        region = extract_region_from_dns(nid)
        if fsx_id:
            fsx_record = _lookup_fsx_by_id(fsx_id, region)
            if fsx_record:
                s3_uri = get_s3_uri_from_fsx(fsx_record)
                if s3_uri:
                    return _adjust_s3_uri_for_subpath(s3_uri, mount['mount_point'], path)

    # Step 4: Search by mount name via AWS API
    fsx_record = find_fsx_by_mount_name(lustre_name, region)
    if fsx_record:
        s3_uri = get_s3_uri_from_fsx(fsx_record)
        if s3_uri:
            return _adjust_s3_uri_for_subpath(s3_uri, mount['mount_point'], path)
        # Try DRA
        fsx_id = fsx_record.get('FileSystemId')
        if fsx_id:
            s3_uri = get_s3_from_dra(fsx_id, region)
            if s3_uri:
                return _adjust_s3_uri_for_subpath(s3_uri, mount['mount_point'], path)

    return None


def detect_s3_backing_verbose(path: str) -> dict:
    """
    Query. Same as detect_s3_backing but returns detailed info about each detection step.

    Useful for debugging why auto-detection failed.

    Args:
        path (str): Absolute path to check.

    Returns:
        dict: Keys 'result' (str|None), 'mount' (dict|None), 'is_lustre' (bool),
              'lustre_name' (str|None), 'fsx_id' (str|None), 'steps' (list of str messages).
    """
    path = os.path.abspath(path)
    steps = []
    info = {'result': None, 'mount': None, 'is_lustre': False,
            'lustre_name': None, 'fsx_id': None, 'steps': steps}

    mount = find_mount_for_path(path)
    if mount is None:
        steps.append(f"No mount found for {path}")
        return info
    info['mount'] = mount
    steps.append(f"Mount: {mount['mount_point']} (source={mount['source']}, type={mount['fs_type']})")

    if not is_lustre_mount(mount):
        steps.append("Not a Lustre mount — S3 backing not applicable")
        return info
    info['is_lustre'] = True

    # Try DNS extraction
    fsx_id = extract_fsx_id_from_dns(mount['source'])
    if fsx_id:
        info['fsx_id'] = fsx_id
        steps.append(f"FSx ID from mount source: {fsx_id}")
    else:
        steps.append(f"Mount source '{mount['source']}' doesn't contain FSx DNS name")

    # Lustre proc
    lustre_name = _find_lustre_name_for_mount(mount)
    if lustre_name:
        info['lustre_name'] = lustre_name
        steps.append(f"Lustre filesystem name: {lustre_name}")
    else:
        steps.append("Could not determine Lustre filesystem name from proc")

    # Server NIDs
    if lustre_name:
        nids = get_lustre_server_nids(lustre_name)
        if nids:
            steps.append(f"Server NIDs: {nids}")
            for nid in nids:
                fsx_id_from_nid = extract_fsx_id_from_dns(nid)
                if fsx_id_from_nid:
                    info['fsx_id'] = fsx_id_from_nid
                    steps.append(f"FSx ID from NID: {fsx_id_from_nid}")
                    break
            else:
                steps.append("No FSx ID found in server NIDs (IPs only, no DNS)")

    # AWS API
    if info['fsx_id']:
        fsx_record = _lookup_fsx_by_id(info['fsx_id'])
        if fsx_record:
            steps.append("FSx filesystem found via API")
            s3_uri = get_s3_uri_from_fsx(fsx_record)
            if s3_uri:
                info['result'] = _adjust_s3_uri_for_subpath(s3_uri, mount['mount_point'], path)
                steps.append(f"S3 URI: {info['result']}")
            else:
                steps.append("No DataRepositoryConfiguration on this filesystem")
        else:
            steps.append("Could not look up FSx filesystem via API (no permissions?)")

    if info['result'] is None and lustre_name:
        fsx_record = find_fsx_by_mount_name(lustre_name)
        if fsx_record:
            info['fsx_id'] = fsx_record.get('FileSystemId')
            steps.append(f"Found FSx by mount name search: {info['fsx_id']}")
            s3_uri = get_s3_uri_from_fsx(fsx_record)
            if s3_uri:
                info['result'] = _adjust_s3_uri_for_subpath(s3_uri, mount['mount_point'], path)
                steps.append(f"S3 URI: {info['result']}")
            else:
                steps.append("No S3 backing found on matched filesystem")
        else:
            steps.append("Could not find FSx filesystem by mount name (no permissions or no match)")

    if info['result'] is None:
        steps.append("FAILED: Could not determine S3 backing for this path")

    return info


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _lookup_fsx_by_id(fsx_id: str, region: Optional[str] = None) -> Optional[dict]:
    """Query. Look up a single FSx filesystem by ID. UNTESTED (no IAM permissions)."""
    try:
        import boto3
    except ImportError:
        return None

    try:
        kwargs = {}
        if region:
            kwargs['region_name'] = region
        client = boto3.client('fsx', **kwargs)
        response = client.describe_file_systems(FileSystemIds=[fsx_id])
        systems = response.get('FileSystems', [])
        return systems[0] if systems else None
    except Exception:
        return None


def _find_lustre_name_for_mount(mount: dict) -> Optional[str]:
    """
    Query. Find the Lustre filesystem name for a mount point.

    Matches via statvfs total capacity against /sys/fs/lustre/llite entries.
    """
    lustre_fs = get_lustre_filesystems()
    if not lustre_fs:
        return None

    try:
        svfs = os.statvfs(mount['mount_point'])
        mount_kb = (svfs.f_blocks * svfs.f_frsize) // 1024
    except OSError:
        return None

    for name, info in lustre_fs.items():
        if info.get('kbytestotal') == mount_kb:
            return name

    return None


def _adjust_s3_uri_for_subpath(s3_uri: str, mount_point: str, path: str) -> str:
    """
    Pure. Adjust S3 URI to account for the subpath within the mount.

    If the user passes /fsx/manta/data and the mount is at /fsx with
    S3 URI s3://bucket/prefix, returns s3://bucket/prefix/manta/data.

    Args:
        s3_uri (str): Base S3 URI from FSx config.
        mount_point (str): Mount point path.
        path (str): Actual path the user wants to scan.

    Returns:
        str: Adjusted S3 URI.

    Examples:
        >>> _adjust_s3_uri_for_subpath('s3://bucket/prefix', '/fsx', '/fsx/manta/data')
        's3://bucket/prefix/manta/data'
        >>> _adjust_s3_uri_for_subpath('s3://bucket', '/fsx', '/fsx')
        's3://bucket'
        >>> _adjust_s3_uri_for_subpath('s3://bucket/', '/fsx', '/fsx/sub')
        's3://bucket/sub'
    """
    # Get the subpath relative to mount point
    if path == mount_point:
        return s3_uri

    subpath = path[len(mount_point):]
    if subpath.startswith('/'):
        subpath = subpath[1:]

    s3_uri = s3_uri.rstrip('/')
    if subpath:
        return f"{s3_uri}/{subpath}"
    return s3_uri
