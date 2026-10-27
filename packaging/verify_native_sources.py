"""Check retained source bytes and build entry points without running upstream code."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
import tomllib

HERE = Path(__file__).resolve().parent  # packaging/: these tools and the release records
ROOT = HERE.parent  # The source folder


def inspect_sources(root=ROOT):
    rows = []
    for item in json.loads((HERE / 'retained-source-manifest.json').read_text()):
        relative = PurePosixPath(item['local_path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe source path')
        path = root.joinpath(*relative.parts)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item['sha256']:
            raise ValueError('Source hash mismatch: ' + item['local_path'])
        expected_size = item.get('size', item.get('bytes'))
        if expected_size is not None and path.stat().st_size != expected_size:
            raise ValueError('Source size mismatch: ' + item['local_path'])
        with tarfile.open(path) as archive:
            members = archive.getmembers()
            files = [m for m in members if m.isfile()]
            top = {PurePosixPath(m.name).parts[0] for m in members if PurePosixPath(m.name).parts}
            if len(top) != 1:
                raise ValueError('Expected one source directory: ' + path.name)
            entries = [m for m in files if len(PurePosixPath(m.name).parts) == 2
                       and PurePosixPath(m.name).name in
                       {'pyproject.toml', 'setup.py', 'CMakeLists.txt', 'configure.bat', 'create_wheels.py'}]
            if not entries:
                raise ValueError('No retained build entry point: ' + path.name)
            requirements = None
            pyproject = next((m for m in entries if m.name.endswith('/pyproject.toml')), None)
            if pyproject:
                requirements = tomllib.loads(archive.extractfile(pyproject).read().decode()).get('build-system')
            rows.append({'name': item['name'], 'version': item['version'],
                         'archive': item['local_path'], 'sha256': digest,
                         'size_bytes': path.stat().st_size, 'files': len(files),
                         'source_directory': next(iter(top)),
                         'build_entry_points': [m.name for m in entries],
                         'declared_build_system': requirements,
                         'build_or_license_candidates': [m.name for m in files
                             if ('license' in PurePosixPath(m.name).name.lower()
                                 or PurePosixPath(m.name).name in {'COPYING', 'requirements.txt'}
                                 or m.name.endswith('/coin/instructions_utils.py'))]})
    return {'scope': 'Source archive integrity and retained build metadata only; no source compilation or legal clearance.',
            'passed': True, 'source_archive_count': len(rows), 'sources': rows}


def extract_sources(root, destination, report):
    destination = destination.resolve()
    if destination.exists():
        raise ValueError('Choose a new, nonexistent extraction directory')
    # Validate the entire set before creating any destination files.
    for row in report['sources']:
        with tarfile.open(root / row['archive']) as archive:
            for member in archive.getmembers():
                relative = PurePosixPath(member.name)
                if (relative.is_absolute() or '..' in relative.parts or '\\' in member.name
                        or ':' in member.name or not (member.isfile() or member.isdir())):
                    raise ValueError('Unsupported or unsafe archive member: ' + member.name)
    destination.mkdir(parents=True)
    for row in report['sources']:
        with tarfile.open(root / row['archive']) as archive:
            archive.extractall(destination, filter='data')
    print('Sources extracted to ' + str(destination))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--extract-to', type=Path)
    args = parser.parse_args()
    report = inspect_sources()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if args.extract_to:
        extract_sources(ROOT, args.extract_to, report)
    print(f"PASS: {report['source_archive_count']} matching source archives and their build entry points.")


if __name__ == '__main__':
    main()
