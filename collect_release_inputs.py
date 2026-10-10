"""Resolve explicit Windows release wheel inputs without executing supplier code."""
import concurrent.futures
import json
import time
import urllib.request
from pathlib import Path

from packaging.tags import compatible_tags, cpython_tags

ROOT = Path(__file__).resolve().parent
TAGS = list(cpython_tags(python_version=(3, 14), abis=['cp314'], platforms=['win_amd64']))
TAGS += list(compatible_tags(python_version=(3, 14), interpreter='cp314', platforms=['win_amd64']))
RANK = {str(t): i for i, t in enumerate(TAGS)}


def resolve(record):
    from packaging.utils import parse_wheel_filename
    name, version = record['name'], record['version']
    for attempt in range(3):
        try:
            with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=60) as response:
                data = json.load(response)
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    choices = []
    for candidate in data['urls']:
        if candidate['packagetype'] != 'bdist_wheel' or candidate.get('yanked'):
            continue
        _, _, _, tags = parse_wheel_filename(candidate['filename'])
        rank = min((RANK[str(t)] for t in tags if str(t) in RANK), default=None)
        if rank is not None:
            choices.append((rank, candidate['filename'], candidate))
    if not choices:
        raise ValueError(f'No compatible wheel for {name} {version}')
    candidate = min(choices)[2]
    return {'name': name, 'version': version, 'filename': candidate['filename'],
            'url': candidate['url'], 'sha256': candidate['digests']['sha256'],
            'bytes': candidate['size'], 'basis': 'Official PyPI release JSON; CPython 3.14 Windows x64 compatible tag'}


def main():
    records = json.loads((ROOT / 'original-build-environment.json').read_text())
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        selected = list(pool.map(resolve, records))
    (ROOT / 'wheel-inputs.json').write_text(json.dumps(selected, indent=2) + '\n')
    requirements = ['# CPython 3.14 Windows x64; exact 0.6.38 installed versions.',
                    '# Hashes select compatible official wheels, not a byte-identical installer guarantee.']
    requirements += [f"{r['name']}=={r['version']} --hash=sha256:{r['sha256']}" for r in selected]
    (ROOT / 'requirements-release.txt').write_text('\n'.join(requirements) + '\n')
    print(f'Resolved {len(selected)} release wheel inputs')

if __name__ == '__main__':
    main()
