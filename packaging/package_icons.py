"""Embed the bundled interface artwork as Qt resources.

The generated comic_stitcher/icons_resource.py is kept in the source. Qt's resource
compiler also records each image's file time, so a fresh run never produces the
same bytes; the file is only replaced when the icon images or their names
actually change. That keeps a build from altering the recorded source.
"""
import ast
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent  # packaging/: these tools and the release records
ROOT = HERE.parent  # The source folder


def resource_contents(source: bytes) -> tuple[bytes, bytes]:
    """The embedded image data and names from a generated resource file.

    Its third table (qt_resource_struct) also holds file times, which are left out.
    """
    found = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ('qt_resource_data', 'qt_resource_name'):
                found[node.targets[0].id] = ast.literal_eval(node.value)
    return found['qt_resource_data'], found['qt_resource_name']


def same_icons(recorded: bytes, fresh: bytes) -> bool:
    try:
        return resource_contents(recorded) == resource_contents(fresh)
    except (KeyError, SyntaxError, ValueError):
        return False


def prepare():
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory)
        for asset in (ROOT / 'assets' / 'icons').iterdir():
            if asset.suffix.lower() in ('.svg', '.png'):
                shutil.copyfile(asset, stage / asset.name)
        assets = sorted(stage.iterdir())
        files = '\n'.join(f'<file alias="{p.name}">{p.name}</file>' for p in assets)
        qrc = stage / 'icons.qrc'
        qrc.write_text('<RCC><qresource prefix="/comic-icons">' + files + '</qresource></RCC>')
        compiler = Path(sys.executable).parent / ('pyside6-rcc.exe' if sys.platform == 'win32' else 'pyside6-rcc')
        command = str(compiler) if compiler.exists() else shutil.which('pyside6-rcc')
        prefix = [command] if command else [sys.executable, '-c', 'from PySide6.scripts.pyside_tool import rcc; rcc()']
        output = ROOT / 'comic_stitcher' / 'icons_resource.py'
        fresh = stage / 'icons_resource.py'
        subprocess.run(prefix + [str(qrc), '-o', str(fresh)], check=True)
        if output.is_file() and same_icons(output.read_bytes(), fresh.read_bytes()):
            print('Interface icons unchanged; keeping the recorded icons_resource.py.')
        else:
            output.write_bytes(fresh.read_bytes().replace(b'\r\n', b'\n'))
    return ROOT / 'assets' / 'licenses' / 'Phosphor-LICENSE.txt'


if __name__ == '__main__':
    prepare()
    print('Embedded interface icon resources are ready.')
