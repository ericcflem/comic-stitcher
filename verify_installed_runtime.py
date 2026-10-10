"""Write an installed verification report even when a check fails."""
import argparse
import json
from pathlib import Path

import installer_evidence
import package_shared_runtime


def verify(folder, record, output):
    result = {'passed': False, 'checks': {},
              'scope': 'Installed payload and shared-runtime packaging; not complete licensing clearance.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        package_shared_runtime.validate(folder, installed=True)
        ignored = sorted(package_shared_runtime.installer_generated_files(
            folder, package_shared_runtime.binaries(folder)))
        result['checks']['shared_runtime'] = {'passed': True, 'installer_generated_files': ignored}
    except Exception as error:
        result['checks']['shared_runtime'] = {'passed': False, 'error': str(error)}
    # Run the full payload check even if the native-only check failed.
    payload_result = output.with_name('installed-file-comparison.json')
    try:
        payload_result.unlink(missing_ok=True)
        installer_evidence.compare(folder, record, payload_result)
        result['checks']['payload'] = json.loads(payload_result.read_text())
    except Exception as error:
        result['checks']['payload'] = {'passed': False, 'error': str(error)}
        if payload_result.is_file():
            result['checks']['payload']['details'] = json.loads(payload_result.read_text())
    result['passed'] = all(check['passed'] for check in result['checks'].values())
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    if result['passed']:
        print('PASS: installed shared-runtime files and complete payload match.')
    else:
        print('FAIL: installed verification found differences; see ' + str(output))
    print('Verification report: ' + str(output))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('folder', type=Path)
    parser.add_argument('--record', type=Path, default=Path('runtime-integration/input-bundle.json'))
    parser.add_argument('--output', type=Path, default=Path('runtime-integration/installed-payload-result.json'))
    args = parser.parse_args()
    return 0 if verify(args.folder, args.record, args.output)['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
