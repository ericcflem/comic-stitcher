"""Embed the bundled interface artwork as Qt resources."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


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
        output = ROOT / 'comic_builder' / 'icons_resource.py'
        subprocess.run(prefix + [str(qrc), '-o', str(output)], check=True)
    return ROOT / 'assets' / 'licenses' / 'Phosphor-LICENSE.txt'


if __name__ == '__main__':
    prepare()
    print('Embedded interface icon resources are ready.')
