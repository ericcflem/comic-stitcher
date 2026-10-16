"""Capture an installer input bundle and verify installed copies without execution."""
import argparse
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.extend(value for key, value in attrs if key == 'href')


def capture(root, output):
    root = root.resolve()
    if not (root / 'ComicStitcher.exe').is_file():
        raise ValueError('ComicStitcher.exe missing')
    inventory = json.loads((root / 'licenses/build-inventory.json').read_text())
    expected = {row['path']: row['sha256'] for row in inventory['binaries']}
    actual = {p.relative_to(root).as_posix(): digest(p) for p in root.rglob('*')
              if p.is_file() and p.suffix.lower() in {'.dll', '.pyd', '.exe', '.so'}}
    if actual != expected:
        raise ValueError('Actual native files differ from the build inventory')
    if any('qt6pdf' in name.lower() or 'qtwebengine' in name.lower()
           or Path(name).name.lower() == 'qpdf.dll' for name in actual):
        raise ValueError('Unused QtPDF/QtWebEngine found')
    if not any(Path(name).name.lower() == 'pdfium.dll' for name in actual):
        raise ValueError('PDFium missing')
    if inventory['python'] != '3.14.2' or inventory['qt'] != '6.11.2':
        raise ValueError('Unexpected reference Python/Qt versions')
    unknown = set(inventory['frozen_code']['unmapped_roots']) - {'launch'}
    if unknown:
        raise ValueError('Unknown frozen roots: ' + ', '.join(sorted(unknown)))
    for row in inventory['packages'] + inventory['runtime_tool_notices']:
        for notice in row['notices']:
            if not (root / 'licenses' / notice).is_file():
                raise ValueError('Missing notice: ' + notice)
    parser = Links()
    parser.feed((root / 'licenses/index.html').read_text())
    for link in parser.links:
        parsed = urlsplit(link)
        if parsed.scheme or parsed.netloc:
            raise ValueError('Expected a local notice link: ' + link)
        if not (root / 'licenses' / unquote(parsed.path)).is_file():
            raise ValueError('Broken notice link: ' + link)
    if digest(root / 'unrar.exe') != 'c4f98e25a824df50f8c80304715f8e6edb9c287225d5d70d423b8b2b12d311cd':
        raise ValueError('UnRAR does not match the verified supplier')
    if digest(root / 'licenses/unrar/license.txt') != '48c3ebe5e74e4b9312cdf6b337b04f87f220416cb54ca4f4df01a1f504859d09':
        raise ValueError('UnRAR license does not match the verified supplier')
    if digest(root / 'cwebp.exe') != json.loads((Path(__file__).resolve().parent / 'vendor' / 'CWEBP-PROVENANCE.json')
                                               .read_text())['executable_sha256']:
        raise ValueError('cwebp.exe does not match the recorded libwebp download')
    record = {'scope': 'Installer payload comparison, not complete licensing clearance',
              'files': {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob('*')) if p.is_file()},
              'native_file_count': len(actual), 'notice_link_count': len(parser.links)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + '\n')
    print('PASS: input bundle verified and complete payload hashes recorded.')


def compare(root, record_path, output):
    record = json.loads(record_path.read_text())
    failures = []
    for name, value in record['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe recorded path')
        target = root / relative
        if not target.is_file() or digest(target) != value:
            failures.append(name)
    extras = sorted(p.relative_to(root).as_posix() for p in root.rglob('*')
                    if p.is_file() and p.suffix.lower() in {'.dll', '.pyd', '.so'}
                    and p.relative_to(root).as_posix() not in record['files'])
    result = {'passed': not failures and not extras, 'checked_files': len(record['files']),
              'mismatched_or_missing': failures, 'unexpected_native_libraries': extras,
              'scope': 'Static payload comparison only; installed GUI smoke tests remain separate.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    if not result['passed']:
        raise ValueError('Payload comparison failed; inspect ' + str(output))
    print('PASS: every recorded app file matches; no unexpected native libraries.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['capture', 'compare'])
    parser.add_argument('folder', type=Path)
    parser.add_argument('--record', type=Path, default=Path('installer-check/input-bundle.json'))
    parser.add_argument('--output', type=Path, default=Path('installer-check/installed-payload-result.json'))
    args = parser.parse_args()
    if args.mode == 'capture':
        capture(args.folder, args.record)
    else:
        compare(args.folder, args.record, args.output)


if __name__ == '__main__':
    main()
