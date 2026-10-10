#!/usr/bin/env python3
"""Stage Crew and register its OMP reporter; never operate existing sessions."""
import datetime
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

# Parse before reading credentials/runtime registration or creating staging paths.
parser = argparse.ArgumentParser(description='Install the optional Sheppard OMP reporter and stage the Herdr plugin. Existing sessions are not restarted.')
parser.parse_args()

here = Path(__file__).resolve().parent
home = Path.home()
omp = shutil.which('omp')
if not omp:
    raise RuntimeError('OMP is required')
manifest = json.loads((here / 'package.json').read_text())
if manifest['name'] != '@bloodf/omp-herdr-sheppard' or not (here / 'dist/reporter.js').is_file():
    raise RuntimeError('Expected prebuilt Crew package')
plugins = json.loads(subprocess.check_output([omp, 'plugin', 'list', '--json'], text=True))
rows = plugins.get('npm', []) if isinstance(plugins, dict) else plugins
laya = home / '.omp/plugins/node_modules/@bloodf/omp-laya-skills/dist/index.js'
if not laya.is_file() or not any(row.get('name') == '@bloodf/omp-laya-skills' and row.get('enabled') for row in rows):
    raise RuntimeError('Install and enable the existing Laya skills package first')
lib = home / '.omp/agent/lib'
lib.mkdir(parents=True, exist_ok=True)
link = lib / 'herdr-sheppard'
if link.exists() and not link.is_symlink():
    raise RuntimeError('Refusing to replace a non-symlink herdr-sheppard directory')
stage_root = Path(tempfile.mkdtemp(prefix='herdr-sheppard-', dir=lib))
stage = stage_root / 'manager'
stage.mkdir()
shutil.copytree(here.parent / 'herdr-plugin', stage_root / 'herdr-plugin')
shutil.copyfile(here.parent / 'LICENSE', stage_root / 'LICENSE')
graph_binary = here.parent / 'target/release/sheppard'
if graph_binary.is_file():
    (stage_root / 'target/release').mkdir(parents=True)
    shutil.copy2(graph_binary, stage_root / 'target/release/sheppard')
for name in ['package.json', 'README.md', 'crew.py', 'dashboard.html', 'dist', 'native-smoke.py', 'LICENSE', 'NOTICE', 'ZOD-LICENSE']:
    source = here / name
    if source.is_dir():
        shutil.copytree(source, stage / name)
    else:
        shutil.copyfile(source, stage / name)
subprocess.run(['python3', str(stage / 'native-smoke.py'), str(home / '.omp/agent/extensions/laya-orchestrate/index.js'), '--plugin', str(laya), '--reporter', str(stage / 'dist/reporter.js')], check=True)
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
backup = home / '.omp/agent/backups/herdr-sheppard' / stamp
backup.mkdir(parents=True)
for name in ['package.json', 'omp-plugins.lock.json']:
    source = home / '.omp/plugins' / name
    if source.exists():
        shutil.copyfile(source, backup / name)
previous = home / '.omp/plugins/node_modules/@bloodf/omp-herdr-sheppard'
if previous.is_symlink():
    (backup / 'previous-link.txt').write_text(str(previous.resolve()))
subprocess.run([omp, 'plugin', 'link', str(stage)], check=True)
subprocess.run([omp, 'plugin', 'enable', manifest['name']], check=True)
plugins = json.loads(subprocess.check_output([omp, 'plugin', 'list', '--json'], text=True))
rows = plugins.get('npm', []) if isinstance(plugins, dict) else plugins
if len([row for row in rows if row.get('name') == manifest['name'] and row.get('enabled') is True]) != 1:
    raise RuntimeError('OMP plugin registration was not verified')
# Prevent the old and renamed reporters loading together in new OMP sessions.
old_name = '@bloodf/omp-herdr-crew'
if any(row.get('name') == old_name and row.get('enabled') is True for row in rows):
    subprocess.run([omp, 'plugin', 'disable', old_name], check=True)
    updated = json.loads(subprocess.check_output([omp, 'plugin', 'list', '--json'], text=True))
    updated_rows = updated.get('npm', []) if isinstance(updated, dict) else updated
    if any(row.get('name') == old_name and row.get('enabled') is True for row in updated_rows):
        raise RuntimeError('Old reporter remains enabled; resolve before starting new sessions')
# Stable path for native Herdr registration from a genuine Herdr terminal.
temporary = lib / ('herdr-sheppard-link-' + stamp)
temporary.symlink_to(stage_root)
temporary.replace(link)
print(json.dumps({'ompReporter': 'enabled', 'path': str(stage_root), 'stablePath': str(link), 'herdrRegistration': 'pending: run herdr plugin link ' + str(link / 'herdr-plugin') + ' inside Herdr', 'existingSessionsRestarted': False, 'backup': str(backup)}))
