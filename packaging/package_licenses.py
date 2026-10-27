"""Collect notices from the actual build environment; this is not a release clearance."""
from __future__ import annotations
import hashlib
import html
import importlib.metadata as metadata
import json
import platform
import re
import shutil
import sys
import sysconfig
from pathlib import Path
from packaging.requirements import Requirement

HERE = Path(__file__).resolve().parent  # packaging/: these tools and the release records
ROOT = HERE.parent  # The source folder
QT_PACKAGES = {'pyside6', 'pyside6-essentials', 'pyside6-addons', 'shiboken6'}
PUBLIC_DOCUMENTS = (
    'LICENSE', 'README.md', 'docs/RELEASE_NOTES.md', 'docs/CHANGELOG.md', 'docs/PROVENANCE.md',
    'docs/START_HERE.md', 'docs/SOURCE_ACCESS.md', 'docs/SOURCE_PACKAGE_CONTENTS.md', 'docs/BUILDING.md',
    'docs/REBUILDING_AND_REPLACING_LIBRARIES.md', 'docs/NATIVE_SOURCE_REBUILD.md',
    'docs/THIRD_PARTY_ACKNOWLEDGEMENTS.md',
)
INTERNAL_DOCUMENTS = (
    'PUBLICATION_PLAN.md', 'RELEASE_CHECKLIST.md', 'GITHUB_LAYOUT.md', 'STATUS.md',
    'REVIEW_GUIDE.md', 'LICENSE_AUDIT.md', 'WINDOWS_BUNDLE_AUDIT.md',
    'RUNTIME_INTEGRATION_TEST.md', 'MICROSOFT_REDISTRIBUTION.md',
    'CLEANUP_NOTES.md', 'PACKAGE_REVIEW_CHECKS.json',
)


def copy_public_documents(destination):
    """Refresh shipped guides, including when reusing an older build folder."""
    for document in INTERNAL_DOCUMENTS:
        (destination / document).unlink(missing_ok=True)
    target = destination / 'licenses'
    target.mkdir(parents=True, exist_ok=True)
    (target / 'RELEASE-LICENSE-REVIEW.md').unlink(missing_ok=True)
    for document in PUBLIC_DOCUMENTS:
        output = destination / document
        output.parent.mkdir(parents=True, exist_ok=True)
        if document.startswith('docs/'):
            (destination / Path(document).name).unlink(missing_ok=True)
        shutil.copyfile(ROOT / document, destination / document)
    (target / 'SOURCE-AND-LICENSES.txt').write_text(
        'Matching sources and component licenses are described in docs/SOURCE_ACCESS.md\n'
        'in the parent application folder. Build instructions are in docs/BUILDING.md;\n'
        'library replacement instructions are in docs/REBUILDING_AND_REPLACING_LIBRARIES.md.\n'
        'Complete supplier license texts are retained in this licenses folder.\n',
        encoding='utf-8')


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def license_label(dist):
    value = dist.metadata.get('License-Expression') or dist.metadata.get('License') or 'See bundled notices'
    return value if len(value) <= 180 and '\n' not in value else 'See bundled notices'


def runtime_distributions(requirements):
    lines = [Requirement(line) for line in requirements.read_text().splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    # A line limited to another platform (for example "; sys_platform == 'linux'")
    # is not part of this build.
    pending = [req.name for req in lines if req.marker is None or req.marker.evaluate()]
    found = {}
    while pending:
        name = pending.pop(0)
        dist = metadata.distribution(name)
        key = canonical(dist.metadata['Name'])
        if key in found:
            continue
        found[key] = dist
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker is None or req.marker.evaluate({'extra': ''}):
                pending.append(req.name)
    return found


def notice_member(member):
    name = member.name.lower()
    if member.suffix.lower() in ('.py', '.pyc', '.pyo'):
        return False
    return ('licenses' in (part.lower() for part in member.parts) or
            name.startswith(('license', 'licence', 'copying', 'notice', 'copyright')))


def frozen_distributions(destination):
    """Resolve installed owners of code actually frozen by this build.

    This deliberately includes packaging tools if they enter PYZ. Vendor notices
    are retained from their containing distribution's complete file manifest.
    """
    from PyInstaller.archive.readers import CArchiveReader
    executable = destination / 'ComicStitcher.exe'
    if not executable.is_file():
        return {}, {'inspected': False, 'unmapped_roots': [], 'modules': []}
    archive = CArchiveReader(str(executable))
    modules = sorted(archive.open_embedded_archive('PYZ.pyz').toc)
    owners = metadata.packages_distributions()
    found = {}
    unmapped = []
    internal = {'comic_builder', 'launch', '_pyi_rth_utils'}
    for root in sorted({name.split('.')[0] for name in modules}):
        if root in internal or root in sys.stdlib_module_names:
            continue
        names = owners.get(root, [])
        if not names:
            unmapped.append(root)
        for name in names:
            dist = metadata.distribution(name)
            found[canonical(dist.metadata['Name'])] = dist
    return found, {'inspected': True, 'unmapped_roots': unmapped, 'modules': modules}


def vendor_metadata(dist):
    """Record exact vendor metadata, without claiming every vendor is frozen."""
    rows = []
    from email.parser import Parser
    for member in dist.files or []:
        if '_vendor' not in member.parts or member.name != 'METADATA':
            continue
        text = Path(dist.locate_file(member)).read_text(encoding='utf-8')
        data = Parser().parsestr(text)
        rows.append({'name': data.get('Name'), 'version': data.get('Version'),
                     'license': data.get('License-Expression') or data.get('License'),
                     'metadata_path': str(member),
                     'scope': 'Vendor metadata from containing wheel; see frozen module list for inclusion.'})
    return rows


def write_index(target):
    data = json.loads((target / 'build-inventory.json').read_text())
    rows = ''.join('<tr><td>' + html.escape(row['name']) + '</td><td>' +
                   html.escape(row['version']) + '</td><td>' + html.escape(row['license']) + '</td></tr>'
                   for row in data['packages'] + data.get('runtime_tool_notices', []))
    links = []
    for path in sorted(target.rglob('*')):
        if path.is_file() and path.name not in ('index.html', 'build-inventory.json'):
            relative = path.relative_to(target).as_posix()
            links.append('<li><a href="' + html.escape(relative, quote=True) + '">' + html.escape(relative) + '</a></li>')
    (target / 'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Comic Stitcher — third-party notices</title><style>body{font:16px system-ui;max-width:1000px;margin:30px auto;padding:20px}td,th{padding:8px;text-align:left;border-bottom:1px solid #ddd}table{width:100%}</style>
<h1>Third-party notices</h1><p>These components retain their own licenses. License labels are upstream metadata, not a grant from Comic Stitcher.</p>
<p>Qt/PySide6 is used under LGPLv3. The archive stack includes LGPLv2.1-or-later components. You may replace the LGPL components and reverse engineer for debugging modifications to them. Consult the supplied license texts and release documentation for full terms.</p>
<p>UnRAR, when included, has a separate redistribution license and is used only for extraction. Its matching notice appears below.</p>
<table><tr><th>Component</th><th>Version</th><th>Upstream license</th></tr>''' + rows + '</table><h2>Files</h2><ul>' + ''.join(links) + '</ul></html>', encoding='utf-8')


def collect(destination):
    from build_policy import validate_qt_bundle
    from verify_microsoft_terms import verify_terms
    microsoft_terms = verify_terms()
    validate_qt_bundle(destination)
    target = destination / 'licenses'
    target.mkdir(parents=True, exist_ok=True)
    packages = []
    distributions = runtime_distributions(ROOT / 'requirements.txt')
    frozen, frozen_inventory = frozen_distributions(destination)
    distributions.update(frozen)
    for key, dist in sorted(distributions.items()):
        copied = []
        for member in dist.files or []:
            if not notice_member(member):
                continue
            source = Path(dist.locate_file(member))
            if not source.is_file():
                continue
            if member.is_absolute() or '..' in member.parts:
                raise RuntimeError(f'Unsafe notice path in {key}: {member}')
            output = target / key / Path(*member.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, output)
            copied.append(output.relative_to(target).as_posix())
        if not copied and key not in QT_PACKAGES:
            raise RuntimeError(f'No license notice was found for {key}; inspect its exact wheel before packaging.')
        # Qt wheels may omit standalone license files. Retain metadata evidence,
        # the official license texts, and a clear source/attribution follow-up.
        info = target / key / 'PACKAGE-METADATA.txt'
        info.parent.mkdir(parents=True, exist_ok=True)
        info.write_text(dist.read_text('METADATA') or '', encoding='utf-8')
        packages.append({'name': dist.metadata['Name'], 'version': dist.version,
                         'license': license_label(dist),
                         'notices': copied, 'vendor_metadata': vendor_metadata(dist)})
    runtime_tool_notices = []
    for tool in ('pyinstaller', 'pyinstaller-hooks-contrib'):
        dist = metadata.distribution(tool)
        copied = []
        for member in dist.files or []:
            if not notice_member(member):
                continue
            if member.is_absolute() or '..' in member.parts:
                raise RuntimeError(f'Unsafe notice path in {tool}: {member}')
            source = Path(dist.locate_file(member))
            if not source.is_file():
                continue
            output = target / tool / Path(*member.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, output)
            copied.append(output.relative_to(target).as_posix())
        if not copied:
            raise RuntimeError(f'No embedded-runtime license notice was found for {tool}.')
        runtime_tool_notices.append({'name': dist.metadata['Name'], 'version': dist.version,
                                    'license': license_label(dist), 'notices': copied,
                                    'role': 'build tool and potentially embedded runtime code'})
    for subdir in ('qt', 'microsoft'):
        shutil.copytree(ROOT / 'assets' / 'licenses' / subdir, target / subdir, dirs_exist_ok=True)
    shutil.copyfile(ROOT / 'assets/licenses/Phosphor-LICENSE.txt', target / 'Phosphor-LICENSE.txt')
    shutil.copyfile(ROOT / 'docs/THIRD_PARTY_ACKNOWLEDGEMENTS.md', target / 'THIRD_PARTY_ACKNOWLEDGEMENTS.md')
    shutil.copyfile(ROOT / 'installer/component-terms.txt', target / 'microsoft' / 'Component-terms.txt')
    shutil.copyfile(ROOT / 'installer/microsoft-terms-review.json', target / 'microsoft' / 'prerequisite-record.json')
    runtime_tool_notices.append({
        'name': 'Microsoft Visual C++ x64 prerequisite',
        'version': microsoft_terms['runtime_version'],
        'license': 'Microsoft proprietary runtime terms; separate publisher redistribution grant',
        'notices': ['microsoft/Runtime-LICENSE.txt', 'microsoft/Runtime-LICENSE.docx',
                    'microsoft/Component-terms.txt', 'microsoft/prerequisite-record.json'],
        'role': 'Installer prerequisite; shared system runtime, not an app-local DLL bundle',
    })
    candidates = [Path(sys.base_prefix) / 'LICENSE.txt', Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt']
    python_license = next((p for p in candidates if p.is_file()), None)
    if python_license is None:
        raise RuntimeError('The matching Python LICENSE.txt was not found; supply it from this Python installation.')
    (target / 'python').mkdir(exist_ok=True)
    shutil.copyfile(python_license, target / 'python' / 'LICENSE.txt')
    packages.append({'name': 'Python', 'version': platform.python_version(), 'license': 'PSF-2.0 and incorporated notices', 'notices': ['python/LICENSE.txt']})
    from PySide6.QtCore import qVersion
    data = {'platform': platform.platform(), 'python': platform.python_version(), 'qt': qVersion(),
            'packages': packages, 'runtime_tool_notices': runtime_tool_notices,
            'frozen_code': frozen_inventory, 'release_clearance': False,
            'release_follow_up': ['Supply corresponding source/replacement information for LGPL components.',
                                 'Check Qt/native third-party notices against the final Windows binaries.',
                                 'Confirm matching UnRAR notice and Windows runtime redistribution terms.',
                                 'Verify final source access, rebuild instructions, and installed offline notices.']}
    if frozen_inventory['unmapped_roots']:
        data['release_follow_up'].append('Resolve frozen module ownership: ' + ', '.join(frozen_inventory['unmapped_roots']))
    if not frozen_inventory['inspected']:
        data['release_follow_up'].append('Frozen executable was not inspected in this collection.')
    from native_notices import collect_native_notices
    data['native_notices'] = collect_native_notices(destination, target, data['qt'])
    data['application_license'] = {'id': 'MIT', 'notice': 'ComicStitcher-MIT.txt', 'scope': 'See docs/PROVENANCE.md; third-party material retains its licenses.'}
    shutil.copyfile(ROOT / 'LICENSE', target / 'ComicStitcher-MIT.txt')
    data['binaries'] = [{'path': path.relative_to(destination).as_posix(),
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                        for path in sorted(destination.rglob('*'))
                        if path.is_file() and path.suffix.lower() in ('.dll', '.exe', '.pyd', '.so')]
    data['build_tools'] = {'PyInstaller': metadata.version('pyinstaller'),
                           'pyinstaller-hooks-contrib': metadata.version('pyinstaller-hooks-contrib')}
    data['build_environment'] = sorted(
        ({'name': dist.metadata['Name'], 'version': dist.version}
         for dist in metadata.distributions() if dist.metadata.get('Name')),
        key=lambda row: canonical(row['name']))
    data['qt_modules'] = sorted({path.name for path in destination.rglob('Qt6*.dll') if path.is_file()})
    data['pdf_backends'] = {'jpeg_extraction': 'pypdf', 'rendering': 'pypdfium2/PDFium',
                            'qt_pdf_packaged': False, 'basis': 'Bundle validated by build_policy before collection.'}
    (target / 'build-inventory.json').write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    (target / 'resolved-runtime-requirements.txt').write_text('\n'.join(f"{row['name']}=={row['version']}" for row in packages if row['name'] != 'Python') + '\n', encoding='utf-8')
    copy_public_documents(destination)
    write_index(target)
    print(f'Collected notices for {len(packages)} components in {target}. Source and license information is in docs/SOURCE_ACCESS.md.')


if __name__ == '__main__':
    collect(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'dist' / 'ComicStitcher')
