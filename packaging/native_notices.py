"""Stage version-matched native notices; never assert release clearance."""
from pathlib import Path
import hashlib
import json
import shutil

HERE = Path(__file__).resolve().parent  # packaging/: these tools and the release records
ROOT = HERE.parent  # The source folder


def pe_version(path):
    import pefile
    binary = pefile.PE(str(path))
    try:
        for rows in getattr(binary, 'FileInfo', []) or []:
            for row in rows:
                for table in getattr(row, 'StringTable', []) or []:
                    value = table.entries.get(b'FileVersion')
                    if value:
                        return value.decode('utf-8').strip()
    finally:
        binary.close()
    raise RuntimeError(f'No file version found for native dependency {path}')


def collect_native_notices(destination, target, qt_version):
    assets = ROOT / 'assets/licenses/native'
    result = {'qt': {'version': qt_version, 'scope': 'Conservative upstream attribution set; configuration/source matching remains open.'},
              'openssl': [], 'software_opengl': [], 'microsoft_runtimes': [], 'release_clearance': False}
    if any(destination.rglob('Qt6*.dll')):
        if qt_version != '6.11.2':
            raise RuntimeError(f'Native Qt notices have only been prepared for 6.11.2, not {qt_version}. Audit the new version first.')
        shutil.copytree(assets / 'qt-6.11.2', target / 'native/qt-6.11.2', dirs_exist_ok=True)
    for path in sorted(destination.rglob('*.dll')):
        name = path.name.lower()
        if name in ('libssl-3.dll', 'libcrypto-3.dll'):
            version = pe_version(path)
            if version != '3.0.18':
                raise RuntimeError(f'OpenSSL notice material matches 3.0.18, not {version}. Audit the new version first.')
            shutil.copytree(assets / 'openssl-3.0.18', target / 'native/openssl-3.0.18', dirs_exist_ok=True)
            result['openssl'].append({'path': path.relative_to(destination).as_posix(), 'version': version,
                                      'supplier_build_provenance_verified': False})
        elif name == 'opengl32sw.dll':
            notice_source = assets / 'mesa-11.2.2-llvm-3.6.2'
            provenance = json.loads((notice_source / 'PROVENANCE.json').read_text())
            if hashlib.sha256(path.read_bytes()).hexdigest() != provenance['dll_sha256']:
                raise RuntimeError('Software OpenGL renderer differs from the notice-reviewed reference; audit the new binary first.')
            shutil.copytree(notice_source, target / 'native/mesa-11.2.2-llvm-3.6.2', dirs_exist_ok=True)
            result['software_opengl'].append({'path': path.relative_to(destination).as_posix(),
                                             'versions': {'Mesa': '11.2.2', 'LLVM': '3.6.2'},
                                             'sha256': provenance['dll_sha256'],
                                             'scope': provenance['scope']})
        elif name.startswith(('msvcp', 'vcruntime')):
            result['microsoft_runtimes'].append({'path': path.relative_to(destination).as_posix(),
                                                'version': pe_version(path), 'redistribution_basis_verified': False})
    shutil.copyfile(assets / 'source-retrieval-evidence.json', target / 'native-source-retrieval-evidence.json')
    return result
