"""Download and verify locked reference wheels without installing or executing them."""
import concurrent.futures
import hashlib
import json
import os
import urllib.request
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

HERE = Path(__file__).resolve().parent  # packaging/: these tools and the release records
ROOT = HERE.parent  # The source folder


def verify(record):
    cache = ROOT / 'release-wheelhouse'
    cache.mkdir(exist_ok=True)
    target = cache / record['filename']
    if not target.exists():
        temporary = target.with_name(target.name + '.part')
        with urllib.request.urlopen(record['url'], timeout=60) as response, temporary.open('wb') as output:
            while data := response.read(1024 * 1024):
                output.write(data)
        os.replace(temporary, target)
    with target.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if target.stat().st_size != record['bytes'] or digest != record['sha256']:
        raise ValueError(f'Wheel size/hash mismatch: {target.name}; no code was installed.')
    with zipfile.ZipFile(target) as archive:
        members = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA') and name.count('/') == 1]
        if len(members) != 1:
            raise ValueError(f'Ambiguous metadata: {target.name}')
        metadata = BytesParser(policy=default).parsebytes(archive.read(members[0]))
    if canonicalize_name(metadata['Name']) != canonicalize_name(record['name']) or metadata['Version'] != record['version']:
        raise ValueError(f'Unexpected wheel identity: {target.name}')
    return {'name': record['name'], 'version': record['version'], 'filename': target.name,
            'sha256': digest, 'requires_dist': metadata.get_all('Requires-Dist', [])}


def check_dependencies(verified):
    versions = {canonicalize_name(item['name']): item['version'] for item in verified}
    environment = default_environment()
    environment.update(python_version='3.14', python_full_version='3.14.2',
                       platform_python_implementation='CPython', implementation_name='cpython',
                       implementation_version='3.14.2', sys_platform='win32', os_name='nt',
                       platform_system='Windows', platform_machine='AMD64', extra='')
    failures = []
    for item in verified:
        for dependency in item['requires_dist']:
            requirement = Requirement(dependency)
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            version = versions.get(canonicalize_name(requirement.name))
            if version is None or version not in requirement.specifier:
                failures.append(f"{item['name']} requires {dependency}; locked version is {version}")
    if failures:
        raise ValueError('\n'.join(failures))


def main():
    records = json.loads((HERE / 'wheel-inputs.json').read_text())
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        verified = list(pool.map(verify, records))
    check_dependencies(verified)
    (HERE / 'wheel-verification.json').write_text(json.dumps({
        'target': 'CPython 3.14.2 Windows x64', 'wheels': verified,
        'dependency_metadata_check': 'passed for default extras on the target platform',
        'limits': 'No Windows binary executed; does not replace pip check and Windows rebuild tests.',
    }, indent=2) + '\n')
    print(f'{len(verified)} wheels hash/identity verified; dependency metadata check passed.')


if __name__ == '__main__':
    main()
