"""Deploy the pinned Windows bundle against the shared Microsoft runtime.

Only NumPy import-name bytes/checksums change. Microsoft binaries are removed,
never modified. Unreviewed binary revisions fail before any files are changed.
"""
import argparse
import json
import re
import struct
from pathlib import Path

import hashlib
import pefile

ROOT = Path(__file__).resolve().parent

def digest(data):
    return hashlib.sha256(data).hexdigest()


def imports(pe):
    return [(entry.dll.decode(), [i.name.decode() if i.name else i.ordinal for i in entry.imports])
            for entry in pe.DIRECTORY_ENTRY_IMPORT]


def patch_numpy(data, expected, old, new):
    if digest(data) != expected:
        raise ValueError('Unexpected NumPy binary; use the pinned original.')
    pe = pefile.PE(data=data)
    try:
        if pe.OPTIONAL_HEADER.DATA_DIRECTORY[4].Size:
            raise ValueError('Signed binary is not supported by this test.')
        before = imports(pe)
        entries = [e for e in pe.DIRECTORY_ENTRY_IMPORT if e.dll.decode().lower() == old.lower()]
        if len(entries) != 1:
            raise ValueError('Expected exactly one renamed runtime import.')
        offset = pe.get_offset_from_rva(entries[0].struct.Name)
        old_bytes = old.encode('ascii') + b'\0'
        new_bytes = new.encode('ascii') + b'\0'
        if data[offset:offset + len(old_bytes)].lower() != old_bytes.lower() or len(new_bytes) > len(old_bytes):
            raise ValueError('Invalid import name location or length.')
        result = bytearray(data)
        result[offset:offset + len(old_bytes)] = new_bytes + b'\0' * (len(old_bytes) - len(new_bytes))
        updated = pefile.PE(data=bytes(result))
        try:
            expected_imports = [(new if n.lower() == old.lower() else n, functions) for n, functions in before]
            if imports(updated) != expected_imports:
                raise ValueError('Unexpected import table change.')
            checksum_offset = updated.OPTIONAL_HEADER.get_field_absolute_offset('CheckSum')
            struct.pack_into('<I', result, checksum_offset, updated.generate_checksum())
        finally:
            updated.close()
        return bytes(result)
    finally:
        pe.close()



def load_config():
    return json.loads((ROOT / 'installer/shared-runtime-inputs.json').read_text())


def binaries(folder):
    return {p.relative_to(folder).as_posix(): digest(p.read_bytes())
            for p in sorted(folder.rglob('*')) if p.is_file()
            and p.suffix.lower() in {'.dll', '.pyd', '.exe', '.so'}}


def installer_generated_files(folder, actual):
    """Recognize only root Inno uninstallers with their companion data file."""
    return {name for name in actual
            if re.fullmatch(r'unins[0-9]{3}\.exe', name, flags=re.IGNORECASE)
            and (folder / name).with_suffix('.dat').is_file()}


def validate(folder, *, installed=False):
    config = load_config()
    inventory = json.loads((folder / 'licenses/build-inventory.json').read_text())
    deployment = inventory.get('runtime_deployment', {})
    if deployment.get('minimum_runtime_version') != config['minimum_runtime_version']:
        raise ValueError('Shared-runtime deployment metadata missing or incompatible.')
    if deployment.get('mode') != 'official-microsoft-installer':
        raise ValueError('Expected official Microsoft shared-runtime deployment.')
    actual = binaries(folder)
    expected = {r['path']: r['sha256'] for r in inventory['binaries']}
    # Setup adds its uninstaller after packaging. Build inputs remain strict.
    generated = installer_generated_files(folder, actual) - set(expected) if installed else set()
    checked = {name: value for name, value in actual.items() if name not in generated}
    if checked != expected:
        missing = sorted(set(expected) - set(checked))
        extra = sorted(set(checked) - set(expected))
        changed = sorted(name for name in set(expected) & set(checked)
                         if checked[name] != expected[name])
        raise ValueError('Bundle binaries differ from their recorded inventory. '
                         + f'Missing: {missing}; unexpected: {extra}; changed: {changed}')
    for name in checked:
        if Path(name).name.lower().startswith(('msvcp', 'vcruntime')):
            raise ValueError('App-local Microsoft runtime remains: ' + name)
        if Path(name).suffix.lower() not in {'.dll', '.pyd', '.exe'}:
            continue
        pe = pefile.PE(str(folder / name), fast_load=True)
        try:
            pe.parse_data_directories(directories=[1, 13])
            for attribute in ('DIRECTORY_ENTRY_IMPORT', 'DIRECTORY_ENTRY_DELAY_IMPORT'):
                for entry in getattr(pe, attribute, []):
                    if entry.dll.decode().lower() == config['renamed_import'].lower():
                        raise ValueError('Renamed Microsoft runtime import remains: ' + name)
        finally:
            pe.close()
    recorded = {r['path']: r for r in deployment['numpy_import_name_patches']}
    for row in config['numpy_consumers']:
        record = recorded.get(row['path'], {})
        if record.get('before_sha256') != row['sha256'] or actual.get(row['path']) != record.get('after_sha256'):
            raise ValueError('NumPy modification record mismatch.')
        if record.get('after_sha256') != row['patched_sha256']:
            raise ValueError('NumPy differs from the Windows-tested patch.')
    return deployment


def deploy(folder):
    config = load_config()
    inventory_path = folder / 'licenses/build-inventory.json'
    inventory = json.loads(inventory_path.read_text())
    if 'runtime_deployment' in inventory:
        validate(folder)
        print('PASS: shared-runtime deployment already matches the Windows-tested policy.')
        return
    actual = binaries(folder)
    if actual != {r['path']: r['sha256'] for r in inventory['binaries']}:
        raise ValueError('Input bundle does not match its inventory.')
    private = {name for name in actual if Path(name).name.lower().startswith(('msvcp', 'vcruntime'))}
    if private != {row['path'] for row in config['runtime_files']}:
        raise ValueError('Unexpected Microsoft runtime layout; audit the new build first.')
    patches = {}
    changes = []
    # Validate every input and both patches before mutation.
    for row in config['numpy_consumers']:
        data = (folder / row['path']).read_bytes()
        patched = patch_numpy(data, row['sha256'], config['renamed_import'], config['standard_import'])
        if digest(patched) != row['patched_sha256']:
            raise ValueError('Patch differs from the Windows-tested NumPy bytes.')
        patches[row['path']] = patched
        changes.append({'path': row['path'], 'before_sha256': digest(data), 'after_sha256': digest(patched)})
    for row in config['runtime_files']:
        if actual.get(row['path']) != row['sha256']:
            raise ValueError('Unexpected runtime bytes: ' + row['path'])
    for name, data in patches.items():
        (folder / name).write_bytes(data)
    for row in config['runtime_files']:
        (folder / row['path']).unlink()
    record = {'mode': 'official-microsoft-installer',
              'minimum_runtime_version': config['minimum_runtime_version'],
              'official_download': 'https://aka.ms/vc14/vc_redist.x64.exe',
              'numpy_import_name_patches': changes,
              'removed_app_local_runtime_files': config['runtime_files'],
              'microsoft_runtime_code_modified': False}
    inventory['runtime_deployment'] = record
    inventory['native_notices']['microsoft_runtimes'] = []
    inventory['binaries'] = [{'path': name, 'sha256': sha} for name, sha in binaries(folder).items()]
    inventory_path.write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
    (folder / 'licenses/SHARED-RUNTIME.md').write_text(
        (ROOT / 'installer/SHARED-RUNTIME.md').read_text(), encoding='utf-8')
    from package_licenses import write_index
    write_index(folder / 'licenses')
    validate(folder)
    print('PASS: removed 11 private runtimes; applied the two Windows-tested NumPy import patches.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['deploy', 'verify'])
    parser.add_argument('folder', type=Path)
    parser.add_argument('--installed', action='store_true', help='Allow the root Inno uninstaller and its data file.')
    args = parser.parse_args()
    if args.mode == 'deploy':
        deploy(args.folder)
    else:
        validate(args.folder, installed=args.installed)
        print('PASS: shared-runtime bundle verified.')


if __name__ == '__main__':
    main()
