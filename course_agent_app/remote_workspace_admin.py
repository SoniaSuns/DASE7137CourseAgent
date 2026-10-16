"""Local administrator CLI; never included in the distributable extension."""
import argparse
import json
from pathlib import Path
import shutil
import zipfile

from remote_workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'course_agent_app/reports/remote_workspace'


def prepare():
    destination = ROOT/'course_agent_app/reports/remote_extension'
    destination.mkdir(parents=True, exist_ok=True)
    for source in (ROOT/'moodle_remote_extension').iterdir():
        if source.is_file():
            shutil.copy2(source, destination/source.name)
    package = destination.with_suffix('.zip')
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(destination.iterdir()):
            archive.write(source, source.name)
    download = ROOT/'00_Download'
    download.mkdir(exist_ok=True)
    shutil.copy2(package, download/'Moodle-Workspace-Chrome.zip')
    return destination, package


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('prepare')
    sub.add_parser('check-idle')
    sub.add_parser('check-network')
    invite = sub.add_parser('invite'); invite.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare':
        folder, package = prepare()
        print('Unpacked extension: '+str(folder)); print('Distributable ZIP: '+str(package))
    elif args.action == 'check-network':
        from remote_network import lan_config
        config = json.loads((ROOT/'course_agent_app/chrome_operator_config.json').read_text(encoding='utf-8'))
        if not lan_config(config['remote_workspace']):
            raise SystemExit('No LAN interface is configured.')
    elif args.action == 'check-idle':
        if not (DATA/'workspace.sqlite3').exists():
            return
        store = Workspace(DATA, recover=False)
        try:
            if store.active_count():
                raise SystemExit(2)
        finally:
            store.close()
    else:
        # Admin access must not perform the service's startup recovery.
        store = Workspace(DATA, recover=False)
        try:
            code = store.invite()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            # Do not overwrite an existing invitation or echo its credential.
            with args.output.open('x', encoding='utf-8') as file:
                json.dump({'invite': code, 'expires_in_hours': 24, 'single_use': True}, file, indent=2)
            print('Private one-time invitation saved: '+str(args.output.resolve()))
        finally:
            store.close()


if __name__ == '__main__':
    main()
