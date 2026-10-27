"""Verify retained terms and their binding to the reviewed Microsoft prerequisite.

No Microsoft installer is executed. CAB parsing is restricted to the known
single-folder MSZIP bootstrapper container; unsupported layouts fail closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent  # packaging/: these tools and the release records
ROOT = HERE.parent  # The source folder
RECORD = ROOT / 'installer/microsoft-terms-review.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def bootstrap_payloads(executable, offset):
    header = struct.unpack_from('<4s5IBB5H', executable, offset)
    signature, _, size, _, files_at, _, minor, major, folders, files, flags, _, _ = header
    if (signature, minor, major, folders, flags) != (b'MSCF', 3, 1, 1, 0):
        raise ValueError('Unsupported bootstrapper CAB layout')
    cab = executable[offset:offset + size]
    if len(cab) != size:
        raise ValueError('Truncated bootstrapper CAB')
    blocks_at, blocks, compression = struct.unpack_from('<IHH', cab, 36)
    if compression != 1:
        raise ValueError('Expected MSZIP compression')
    members = []
    pos = files_at
    for _ in range(files):
        length, start, folder, _, _, _ = struct.unpack_from('<IIHHHH', cab, pos)
        pos += 16
        end = cab.index(b'\0', pos)
        name = cab[pos:end].decode('ascii')
        pos = end + 1
        if folder != 0 or name in {item[0] for item in members}:
            raise ValueError('Unsupported folder or duplicate CAB member')
        members.append((name, start, length))
    stream = bytearray()
    pos = blocks_at
    for _ in range(blocks):
        _, packed, unpacked = struct.unpack_from('<IHH', cab, pos)
        pos += 8
        block = cab[pos:pos + packed]
        pos += packed
        if len(block) != packed or block[:2] != b'CK':
            raise ValueError('Invalid MSZIP block')
        decoder = zlib.decompressobj(-15, zdict=bytes(stream[-32768:]))
        data = decoder.decompress(block[2:]) + decoder.flush()
        if len(data) != unpacked or not decoder.eof:
            raise ValueError('Invalid MSZIP expansion')
        stream.extend(data)
    result = {}
    for name, start, length in members:
        data = bytes(stream[start:start + length])
        if len(data) != length:
            raise ValueError('CAB member exceeds expanded data')
        result[name] = data
    return result


def verify_terms(root=ROOT, runtime=None):
    root = Path(root)
    record = json.loads((root / 'installer/microsoft-terms-review.json').read_text())
    for name, expected in record['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe terms record path')
        if digest((root / relative).read_bytes()) != expected:
            raise ValueError(f'Retained terms/evidence changed: {name}')
    if runtime is not None:
        executable = Path(runtime).read_bytes()
        if digest(executable) != record['runtime_sha256']:
            raise ValueError('Microsoft installer changed: a fresh terms review is required')
        payloads = bootstrap_payloads(executable, record['bootstrap_cab_offset'])
        for member, expected in record['bootstrap_payload_hashes'].items():
            if digest(payloads[member]) != expected:
                raise ValueError(f'Bootstrapper evidence mismatch: {member}')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path)
    args = parser.parse_args()
    record = verify_terms(runtime=args.runtime)
    print(f"PASS: retained Microsoft terms verified for runtime {record['runtime_version']}"
          + ('; exact EXE and bootstrapper evidence verified' if args.runtime else ''))
