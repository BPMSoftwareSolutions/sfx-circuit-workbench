"""Package, deploy and verify the private workbench Space from this repository."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'deploy/huggingface/space.json'
RECEIPT = ROOT / 'build/huggingface/package.receipt.json'
DEPLOYMENT = ROOT / 'build/huggingface/deployment.json'
HOST_FILES = [
    'package.json', 'package-lock.json', 'tsconfig.json', 'postcss.config.mjs',
    'next.config.ts', 'app/globals.css', 'app/lab', 'components/lab',
    'contracts/lab.ts', 'contracts/invocation.ts', 'lib/lab', 'lib/capability-api.ts',
    'generated/lab-publication.json', 'app/workbench', 'lib/workbench',
]


def load(file):
    return json.loads(Path(file).read_text(encoding='utf-8-sig'))


def write(file, value):
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def digest(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def allowed(file):
    return not (any(part in {'.git', '.next', 'node_modules', '__pycache__', '.venv'} for part in file.parts)
                or file.name.startswith('.env') or file.suffix in {'.env', '.pem', '.key', '.log'})


def inventory(directory):
    directory = directory.resolve()
    result = {}
    for file in sorted(directory.rglob('*')):
        if file.is_symlink() or not file.resolve().is_relative_to(directory):
            raise ValueError('PACKAGE_LINK_REFUSED')
        if file.is_file():
            relative = file.relative_to(directory)
            if not allowed(relative):
                raise ValueError('PACKAGE_FILE_REFUSED: ' + relative.as_posix())
            result[relative.as_posix()] = digest(file.read_bytes())
    if not result:
        raise ValueError('PACKAGE_EMPTY')
    return result


def config():
    value = load(CONFIG)
    origin = urlparse(value['origin'])
    if origin.scheme != 'https' or not origin.hostname.endswith('.hf.space') or origin.username or origin.password or origin.path or origin.query or origin.fragment:
        raise ValueError('HF_ORIGIN_REQUIRED')
    return value


def validate_workbench(directory):
    files = inventory(directory)
    version = load(directory / 'package-version.json')
    files.pop('package-version.json')
    if digest(json.dumps(files, sort_keys=True).encode()) != version['contentDigest']:
        raise ValueError('WORKBENCH_PACKAGE_CHANGED: rebuild the interactive package')
    return version


def package(platform):
    platform = platform.resolve()
    workbench = ROOT / 'build/web/package'
    version = validate_workbench(workbench)
    server = ROOT / 'build/web/workbench-publication.json'
    if load(server)['publicationId'] != version['publicationId'] or load(platform / 'generated/lab-publication.json')['publicationId'] != version['publicationId']:
        raise ValueError('PUBLICATION_MISMATCH')
    source_files = []

    def select(source, target):
        if not source.exists() or source.is_symlink():
            raise ValueError('PACKAGE_SOURCE_UNAVAILABLE: ' + str(source))
        if source.is_dir():
            for relative in inventory(source):
                source_files.append((source / relative, target / relative))
        else:
            if not allowed(source):
                raise ValueError('PACKAGE_FILE_REFUSED')
            source_files.append((source, target))

    for entry in HOST_FILES:
        source = platform / entry
        if not source.resolve().is_relative_to(platform):
            raise ValueError('HOST_PATH_OUTSIDE_CHECKOUT')
        select(source, Path(entry))
    select(workbench, Path('public/workbench'))
    select(server, Path('generated/workbench-publication.json'))
    for name, target in [('Dockerfile', 'Dockerfile'), ('README.md', 'README.md'),
                         ('layout.tsx', 'app/layout.tsx'), ('.dockerignore', '.dockerignore')]:
        select(ROOT / 'deploy/huggingface' / name, Path(target))
    # A new directory for every release prevents stale files from surviving a rebuild.
    parent = ROOT / 'build/huggingface'
    parent.mkdir(parents=True, exist_ok=True)
    if not parent.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('PACKAGE_OUTPUT_OUTSIDE_WORKBENCH')
    output = Path(tempfile.mkdtemp(prefix='bundle-', dir=parent))
    for source, relative in source_files:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    validate_workbench(output / 'public/workbench')
    record = {'artifact': output.relative_to(ROOT).as_posix(), 'packageVersion': version,
              'space': config(), 'files': inventory(output)}
    write(RECEIPT, record)
    return {'receipt': str(RECEIPT), 'artifact': str(output), 'files': len(record['files']), 'packageVersion': version}


def checked_artifact(receipt):
    record = load(receipt)
    artifact = (ROOT / record['artifact']).resolve()
    if not artifact.is_relative_to((ROOT / 'build/huggingface').resolve()):
        raise ValueError('ARTIFACT_OUTSIDE_DEPLOYMENT_DIRECTORY')
    if inventory(artifact) != record['files']:
        raise ValueError('DEPLOYMENT_ARTIFACT_CHANGED: package again before deploying')
    if validate_workbench(artifact / 'public/workbench') != record['packageVersion']:
        raise ValueError('DEPLOYMENT_PACKAGE_VERSION_CHANGED')
    if record['space'] != config():
        raise ValueError('DEPLOYMENT_TARGET_CHANGED: package again')
    return artifact, record


def token():
    from huggingface_hub import get_token
    value = os.environ.get('HF_TOKEN') or os.environ.get('HF_ACCESS_TOKEN')
    if not value and os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                value = winreg.QueryValueEx(key, 'HF_ACCESS_TOKEN')[0]
        except FileNotFoundError:
            pass
    value = value or get_token()
    if not value:
        raise ValueError('HF_TOKEN_REQUIRED: use HF_ACCESS_TOKEN, HF_TOKEN, or hf auth login')
    return value


def space_status(api, settings):
    info = api.space_info(settings['repoId'], revision=settings['branch'])
    if not info.private:
        raise ValueError('PRIVATE_SPACE_REQUIRED')
    return {'space': info.id, 'private': True, 'commit': info.sha,
            'stage': info.runtime.stage, 'runningCommit': info.runtime.raw.get('sha'),
            'fullWidth': info.card_data.get('fullWidth'), 'header': info.card_data.get('header')}


def upload(api, artifact, record, message):
    settings = record['space']
    before = space_status(api, settings)
    # Uploaded bytes are fixed before network work begins. Ignore unlisted files,
    # including anything concurrently generated in the staging directory.
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete
    operations = []
    for relative, expected in record['files'].items():
        data = (artifact / relative).read_bytes()
        if digest(data) != expected:
            raise ValueError('DEPLOYMENT_ARTIFACT_CHANGED')
        operations.append(CommitOperationAdd(path_in_repo=relative, path_or_fileobj=data))
    # Remove obsolete files only from the renderer-owned static asset directory.
    remote_files = api.list_repo_files(settings['repoId'], repo_type='space', revision=before['commit'])
    operations += [CommitOperationDelete(path_in_repo=name) for name in remote_files
                   if name.startswith('public/workbench/') and name not in record['files']]
    commit = api.create_commit(settings['repoId'], repo_type='space', revision=settings['branch'],
                               parent_commit=before['commit'], operations=operations, commit_message=message)
    return {'space': settings, 'commit': commit.oid, 'previousCommit': before['commit'],
            'packageVersion': record['packageVersion'], 'verified': False}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('VERIFICATION_REDIRECT_REFUSED')


def verify(api, release, auth, timeout):
    deadline = time.monotonic() + timeout
    settings = release['space']
    while True:
        state = space_status(api, settings)
        if state['commit'] != release['commit']:
            raise ValueError('SPACE_REVISION_CHANGED')
        if state['stage'] == 'RUNNING' and state['runningCommit'] == release['commit']:
            break
        if state['stage'] in {'BUILD_ERROR', 'RUNTIME_ERROR', 'CONFIG_ERROR', 'PAUSED', 'STOPPED'}:
            raise ValueError('SPACE_NOT_RUNNING: ' + state['stage'])
        if time.monotonic() >= deadline:
            raise ValueError('SPACE_BUILD_TIMEOUT: run verify again after the build finishes')
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    if state['fullWidth'] is not True or state['header'] != 'mini':
        raise ValueError('SPACE_VIEWPORT_CONFIGURATION_CHANGED')
    opener = build_opener(NoRedirect())
    def get(path):
        request = Request(settings['origin'] + path, headers={'Authorization': 'Bearer ' + auth, 'Cache-Control': 'no-cache'})
        with opener.open(request, timeout=30) as response:
            return response.read()
    if json.loads(get('/workbench/package-version.json')) != release['packageVersion']:
        raise ValueError('HOSTED_PACKAGE_VERSION_MISMATCH')
    if b'sfx-workbench-config' not in get('/workbench/index.html'):
        raise ValueError('HOSTED_WORKBENCH_UNAVAILABLE')
    if space_status(api, settings)['commit'] != release['commit']:
        raise ValueError('SPACE_REVISION_CHANGED')
    return {**release, **state, 'space': settings, 'verified': True,
            'verifiedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    pack = commands.add_parser('package', help='Stage the renderer from an existing workbench build and host checkout')
    pack.add_argument('--platform-root', type=Path, default=Path(os.environ.get('SFX_PLATFORM_ROOT', 'C:/lab/repos/sfx-platform')))
    for name in ['check', 'deploy']:
        command = commands.add_parser(name)
        command.add_argument('--receipt', type=Path, default=RECEIPT)
        if name == 'deploy':
            command.add_argument('--message', default='Deploy SideFX circuit workbench')
            command.add_argument('--wait-seconds', type=int, default=600)
    commands.add_parser('status', help='Read the existing private Space status')
    command = commands.add_parser('verify', help='Verify the last uploaded commit and live package without uploading')
    command.add_argument('--deployment', type=Path, default=DEPLOYMENT)
    command.add_argument('--wait-seconds', type=int, default=600)
    args = parser.parse_args()
    if args.command == 'package':
        return package(args.platform_root)
    if args.command in {'check', 'deploy'}:
        artifact, record = checked_artifact(args.receipt)
        if args.command == 'check':
            return {'passed': True, 'uploads': 0, 'files': len(record['files']), 'packageVersion': record['packageVersion']}
    from huggingface_hub import HfApi
    auth = token()
    api = HfApi(token=auth)
    if args.command == 'status':
        return space_status(api, config())
    if args.command == 'deploy':
        release = upload(api, artifact, record, args.message)
        write(DEPLOYMENT, release)  # Retain the exact commit even if the build fails.
    else:
        release = load(args.deployment)
        if release['space'] != config():
            raise ValueError('DEPLOYMENT_TARGET_CHANGED')
    result = verify(api, release, auth, args.wait_seconds)
    write(DEPLOYMENT if args.command == 'deploy' else args.deployment, result)
    return result


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except Exception as error:
        # Service exceptions can contain response details. Print only their class;
        # validation errors above contain paths/codes, never credential values.
        message = str(error) if type(error) is ValueError else type(error).__name__
        print(json.dumps({'error': message}))
        raise SystemExit(1)
