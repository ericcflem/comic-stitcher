"""Temporary observer entry point for the isolated modified-library test."""
import json
import sys
from multiprocessing import freeze_support
from pathlib import Path

freeze_support()
if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--verify-library':
        import py7zr
        payload = {
            'frozen': bool(getattr(sys, 'frozen', False)),
            'library': 'py7zr',
            'module_path': str(py7zr.__file__),
            'marker': getattr(py7zr, 'COMIC_STITCHER_REBUILD_MARKER', None),
            'executable': sys.executable,
        }
        Path(sys.argv[2]).write_text(json.dumps(payload, indent=2), encoding='utf-8')
        raise SystemExit(0 if payload['frozen'] and payload['marker'] == 'modified-library-test-1' else 2)
    from comic_stitcher.diagnostics import install_diagnostics
    install_diagnostics()
    from comic_stitcher.gui import main
    raise SystemExit(main())
