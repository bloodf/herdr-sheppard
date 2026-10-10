#!/usr/bin/env python3
"""Scoped Herdr crew observer/controller. Python standard library only."""
import argparse
import concurrent.futures
import curses
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

TOKENS = ('crew_role', 'crew_model', 'crew_owner', 'crew_parent', 'crew_task',
          'crew_phase', 'crew_quota', 'crew_laya_calls', 'crew_laya_cached', 'crew_at')
ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))')


class CrewError(Exception):
    pass


def clean(value):
    text = ANSI.sub('', str(value))
    text = re.sub(r'(?i)(bearer\s+)[^\s"\']+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|authorization|access[_-]?token)\s*[=:]\s*)[^\s,"\']+', r'\1[REDACTED]', text)
    text = re.sub(r'\b(?:sk-|AIza)[A-Za-z0-9_-]{16,}', '[REDACTED]', text)
    return ''.join(c for c in text if c in '\n\t' or ord(c) >= 32)[:20000]


def key(*parts):
    return json.dumps(parts, separators=(',', ':'))


def records(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def copy_json(value):
    return json.loads(json.dumps(value))


def identity(agent):
    # Revisions change on focus/metadata. Only terminal/session replacement matters.
    data = [agent.get(k) for k in ('workspace_id', 'pane_id', 'terminal_id', 'agent_session')]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class Crew:
    def __init__(self, env=None, runner=None):
        self.env = dict(os.environ if env is None else env)
        self.runner = runner or subprocess.run
        self.binary = self.env.get('HERDR_BIN_PATH', 'herdr')
        self.local_workspace = self.env.get('HERDR_WORKSPACE_ID')
        native_workspace = None
        if self.env.get('HERDR_ENV') == '1' and self.env.get('HERDR_PLUGIN_ID') == 'bloodf.herdr-sheppard':
            try:
                context = json.loads(self.env.get('HERDR_PLUGIN_CONTEXT_JSON', '{}'))
            except (ValueError, TypeError) as exc:
                raise CrewError('Invalid native Herdr plugin context; no command was sent.') from exc
            if not isinstance(context, dict):
                raise CrewError('Invalid native Herdr plugin context; no command was sent.')
            value = context.get('workspace_id')
            if isinstance(value, str) and value:
                native_workspace = value
                if self.local_workspace and self.local_workspace != value:
                    raise CrewError('Native workspace context disagrees with the environment; no command was sent.')
                self.local_workspace = value
        scope = self.env.get('CREW_SCOPE_WORKSPACE')
        if scope and (not native_workspace or scope != native_workspace):
            raise CrewError('Workspace changed while opening Sheppard; reopen from the intended workspace. No command was sent.')
        self.pins = {}
        self.latest = {}
        self.requests = {}
        self.lock = threading.RLock()
        self.refresh_lock = threading.Lock()
        self.refreshed = 0

    def call(self, machine, args, raw=False):
        if self.env.get('HERDR_ENV') != '1' or not self.local_workspace:
            raise CrewError('Start Sheppard inside the intended Herdr workspace.')
        command = [self.binary]
        if machine != 'local':
            command += ['--machine', machine]
        command += args
        try:
            result = self.runner(command, capture_output=True, text=True, timeout=5, env=self.env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CrewError('Herdr unavailable or command timed out.') from exc
        if result.returncode:
            raise CrewError('Herdr command failed; no fallback was attempted.')
        if len(result.stdout.encode()) > 4 * 1024 * 1024:
            raise CrewError('Herdr response exceeds the inventory limit.')
        if raw:
            return clean(result.stdout)
        try:
            data = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise CrewError('Herdr returned invalid JSON.') from exc
        if isinstance(data, dict) and data.get('error'):
            raise CrewError('Herdr rejected the request.')
        return data

    def _machine_snapshot(self, machine):
        try:
            response = self.call(machine['id'], ['api', 'snapshot'])
            snapshot = response['result']['snapshot']
            if not isinstance(snapshot, dict):
                raise ValueError()
            return machine, snapshot, None
        except (CrewError, KeyError, TypeError, ValueError) as exc:
            return machine, {}, clean(str(exc)) or 'Invalid snapshot.'

    def snapshot(self, force=True):
        with self.refresh_lock:
            if not force and hasattr(self, 'public') and self.refreshed and time.monotonic() - self.refreshed < 2:
                return copy_json(self.public)
            result = self._snapshot()
            self.refreshed = time.monotonic()
            return result

    def _snapshot(self):
        machines = [{'id': 'local', 'label': 'Local'}]
        errors = []
        try:
            saved = self.call('local', ['machine', 'list', '--json'])
            if not isinstance(saved, list):
                raise CrewError('Invalid saved machine inventory.')
            seen = {'local'}
            for row in records(saved):
                mid = row.get('id')
                if isinstance(mid, str) and mid and mid not in seen and row.get('enabled', True):
                    seen.add(mid)
                    machines.append({'id': mid, 'label': clean(row.get('label', mid))[:160]})
        except CrewError as exc:
            errors.append(str(exc))
        result = {'machines': [], 'nodes': [], 'edges': [], 'errors': errors, 'captured_at': time.time()}
        private = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            snapshots = list(pool.map(self._machine_snapshot, machines))
        with self.lock:
            for machine, snap, error in snapshots:
                mid = machine['id']
                item = dict(machine, workspaces=[], error=error)
                result['machines'].append(item)
                panes = {p['pane_id']: p for p in records(snap.get('panes')) if isinstance(p.get('pane_id'), str)}
                agents = {p['pane_id']: p for p in records(snap.get('agents')) if isinstance(p.get('pane_id'), str)}
                for workspace in records(snap.get('workspaces')):
                    wid = workspace.get('workspace_id')
                    if not isinstance(wid, str):
                        continue
                    if mid == 'local' and wid != self.local_workspace:
                        continue
                    pinned = wid == (self.local_workspace if mid == 'local' else self.pins.get(mid))
                    ws = {'id': wid, 'key': key(mid, wid), 'name': clean(workspace.get('label', wid))[:160], 'pinned': pinned, 'tabs': []}
                    item['workspaces'].append(ws)
                    for tab in records(snap.get('tabs')):
                        if tab.get('workspace_id') != wid:
                            continue
                        tid = tab.get('tab_id')
                        if not isinstance(tid, str):
                            continue
                        tabout = {'id': tid, 'key': key(mid, wid, tid), 'name': clean(tab.get('label', tid))[:160], 'agents': []}
                        ws['tabs'].append(tabout)
                        for pid, pane in panes.items():
                            if pane.get('workspace_id') != wid or pane.get('tab_id') != tid:
                                continue
                            agent = agents.get(pid, pane)
                            nodekey = key(mid, wid, tid, pid)
                            tokens = dict(pane.get('tokens') or {}) if isinstance(pane.get('tokens'), dict) else {}
                            if isinstance(agent.get('tokens'), dict):
                                tokens.update(agent['tokens'])
                            try:
                                timestamp = float(tokens.get('crew_at', '0'))
                                if timestamp > 1e11:
                                    timestamp /= 1000
                                session = agent.get('agent_session') if isinstance(agent.get('agent_session'), dict) else {}
                                session_matches = False
                                if session.get('kind') == 'id' and isinstance(session.get('value'), str) and session['value']:
                                    session_matches = tokens.get('crew_session') == session['value']
                                elif session.get('kind') == 'path' and isinstance(session.get('value'), str) and session['value']:
                                    session_matches = tokens.get('crew_session_path') == hashlib.sha256(session['value'].encode()).hexdigest()
                                telemetry_current = 0 <= time.time() - timestamp <= 15 and session_matches
                            except (ValueError, TypeError):
                                telemetry_current = False
                            telemetry = {name: clean(tokens.get(name, 'unknown'))[:600] if telemetry_current else 'unknown' for name in TOKENS}
                            node = {'key': nodekey, 'machine_id': mid, 'workspace_id': wid, 'tab_id': tid,
                                    'pane_id': pid, 'terminal_id': agent.get('terminal_id'),
                                    'name': clean(agent.get('name') or agent.get('title') or pane.get('label') or pid)[:160],
                                    'status': agent.get('agent_status', 'unknown'), 'interactive_ready': agent.get('interactive_ready', False),
                                    'telemetry': telemetry, 'identity': identity(agent),
                                    'actionable': bool(pinned and pid in agents and agent.get('terminal_id'))}
                            tabout['agents'].append(node)
                            result['nodes'].append(node)
                            if pid in agents:
                                private[nodekey] = {'machine': mid, 'agent': agent, 'node': node}
            # Parent tokens are scoped to one machine/workspace; ambiguous owners have no edge.
            owners = {}
            for node in result['nodes']:
                owner = node['telemetry']['crew_owner']
                if owner != 'unknown':
                    owners.setdefault((node['machine_id'], node['workspace_id'], owner), []).append(node['key'])
            for node in result['nodes']:
                parent = owners.get((node['machine_id'], node['workspace_id'], node['telemetry']['crew_parent']), [])
                if len(parent) == 1 and parent[0] != node['key']:
                    result['edges'].append({'from': parent[0], 'to': node['key']})
            self.latest = private
            self.public = result
        return result

    def pin(self, machine_id, workspace_id):
        with self.lock:
            if machine_id == 'local':
                if workspace_id != self.local_workspace:
                    raise CrewError('Local scope is fixed to the starting workspace.')
            elif not any(m['id'] == machine_id and any(w['id'] == workspace_id for w in m['workspaces']) for m in getattr(self, 'public', {}).get('machines', [])):
                raise CrewError('Select a workspace from the current machine inventory.')
            else:
                self.pins[machine_id] = workspace_id
            self.refreshed = 0
        return {'ok': True}

    def action(self, request):
        action = request.get('action')
        if action not in ('focus', 'read', 'prompt', 'interrupt'):
            raise CrewError('Unsupported action.')
        rid = request.get('request_id')
        if not isinstance(rid, str) or not 1 <= len(rid) <= 160:
            raise CrewError('A unique request_id is required.')
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        with self.lock:
            if rid in self.requests:
                old = self.requests[rid]
                if old['digest'] != digest:
                    raise CrewError('request_id was already used for another action.')
                return old['result']
            if len(self.requests) >= 4096:
                raise CrewError('Controller action limit reached; inspect uncertain actions before restarting.')
            item = self.latest.get(request.get('key'))
            if not item or not item['node']['actionable']:
                raise CrewError('Select an agent in a pinned workspace.')
            mid, agent = item['machine'], item['agent']
            permitted = self.local_workspace if mid == 'local' else self.pins.get(mid)
            if permitted != agent.get('workspace_id'):
                raise CrewError('Workspace scope changed.')
            if request.get('identity') != identity(agent):
                raise CrewError('Refresh the changed agent before acting.')
            # Get must resolve the exact agent's pane, never a name reused by another agent.
            fresh = self.call(mid, ['agent', 'get', agent['pane_id']]).get('result', {}).get('agent')
            if not isinstance(fresh, dict) or identity(fresh) != identity(agent) or not fresh.get('terminal_id'):
                raise CrewError('Agent terminal or session changed; refresh first.')
            if action == 'prompt':
                text = request.get('text')
                if not isinstance(text, str) or not text.strip() or len(text.encode()) > 12000 or '\x00' in text:
                    raise CrewError('Prompt must contain 1–12000 bytes of text.')
                if fresh.get('agent_status') not in ('idle', 'done') or not fresh.get('interactive_ready'):
                    raise CrewError('Prompt requires a ready idle/done agent.')
                argv = ['agent', 'prompt', agent['pane_id'], text]
            elif action == 'interrupt':
                if request.get('confirmed') is not True:
                    raise CrewError('Interrupt requires explicit confirmation.')
                if fresh.get('agent_status') not in ('working', 'blocked'):
                    raise CrewError('Interrupt requires a working or blocked agent.')
                argv = ['agent', 'send-keys', agent['pane_id'], 'ctrl+c']
            elif action == 'read':
                argv = ['agent', 'read', agent['pane_id'], '--source', 'recent-unwrapped', '--lines', '80', '--format', 'text']
            else:
                argv = ['agent', 'focus', agent['pane_id']]
            # Reserve before sending. Ambiguous failure never replays a mutation.
            receipt = {'ok': False, 'state': 'uncertain', 'request_id': rid}
            self.requests[rid] = {'digest': digest, 'result': receipt}
            try:
                output = self.call(mid, argv, raw=action == 'read')
                receipt = {'ok': True, 'state': 'completed', 'request_id': rid}
                if action == 'read':
                    receipt['text'] = output
            except CrewError:
                receipt['error'] = 'Command outcome is uncertain. Inspect the agent; do not retry this action.'
            self.requests[rid]['result'] = receipt
            return receipt


def server_for(crew, dashboard=None):
    token = secrets.token_urlsafe(32)
    path = Path(dashboard or Path(__file__).with_name('dashboard.html'))

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass

        def reply(self, status, data, html=False):
            body = data.encode() if html else json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def auth(self, api=True):
            expected = '127.0.0.1:' + str(self.server.server_port)
            if self.headers.get('Host') != expected:
                return False
            origin = self.headers.get('Origin')
            if origin is not None and origin != 'http://' + expected:
                return False
            if self.command == 'POST' and origin != 'http://' + expected:
                return False
            return not api or hmac.compare_digest(self.headers.get('X-Crew-Token', ''), token)

        def do_GET(self):
            route = urlsplit(self.path).path
            if not self.auth(api=route != '/'):
                return self.reply(403, {'error': 'Forbidden'})
            if route == '/':
                # Capability arrives via fragment and is never present in HTTP logs.
                try:
                    return self.reply(200, path.read_text(), html=True)
                except OSError:
                    return self.reply(503, {'error': 'Dashboard not installed'})
            if route == '/api/snapshot':
                try:
                    return self.reply(200, crew.snapshot(force=False))
                except Exception:
                    return self.reply(502, {'error': 'Unable to refresh Herdr inventory'})
            return self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            if not self.auth():
                return self.reply(403, {'error': 'Forbidden'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384 or self.headers.get('Transfer-Encoding'):
                    return self.reply(413, {'error': 'Invalid body size'})
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    return self.reply(415, {'error': 'JSON required'})
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise CrewError('Object required')
                if self.path == '/api/action':
                    return self.reply(200, crew.action(data))
                if self.path == '/api/pin':
                    return self.reply(200, crew.pin(data.get('machine_id'), data.get('workspace_id')))
                return self.reply(404, {'error': 'Not found'})
            except (CrewError, ValueError, TypeError) as exc:
                return self.reply(409, {'error': clean(str(exc))})
            except Exception:
                return self.reply(502, {'error': 'Herdr request failed'})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.token = token
    server.url = 'http://127.0.0.1:' + str(server.server_port) + '/#token=' + token
    return server


class PopupSelection:
    """Selection follows one snapshot-bound agent, never a row position."""
    def __init__(self):
        self.selected = None
        self.initialized = False

    def refresh(self, rows):
        if not self.initialized:
            self.initialized = True
            if rows:
                self.selected = (rows[0]['key'], rows[0]['identity'])
        index = self.index(rows)
        if self.selected is not None and index is None:
            self.selected = None
        return index

    def index(self, rows):
        return next((i for i, row in enumerate(rows)
                     if self.selected == (row['key'], row['identity'])), None)

    def navigate(self, rows, delta):
        if not rows:
            self.selected = None
            return None
        current = self.index(rows)
        index = 0 if current is None else max(0, min(len(rows)-1, current + delta))
        self.selected = (rows[index]['key'], rows[index]['identity'])
        return index


def popup_palette(screen, terminal=curses):
    """Titanium approximations in existing color slots; never redefine colors."""
    fallback = {'normal': terminal.A_NORMAL, 'accent': terminal.A_NORMAL,
                'muted': terminal.A_NORMAL, 'selected': terminal.A_REVERSE,
                'warning': terminal.A_NORMAL}
    try:
        terminal.start_color()
        if not terminal.has_colors() or terminal.COLORS < 8 or terminal.COLOR_PAIRS < 6:
            return fallback
        if terminal.COLORS >= 256:
            # #151820 / #e8ecf4 / #00b4ff / #9ca3b0 / #ffb347, nearest xterm slots.
            background, ink, accent, muted, warning = 234, 255, 39, 247, 215
        else:
            background, ink, accent, muted, warning = (terminal.COLOR_BLACK, terminal.COLOR_WHITE,
                                                       terminal.COLOR_CYAN, terminal.COLOR_WHITE, terminal.COLOR_YELLOW)
        pairs = [(ink, background), (accent, background), (muted, background),
                 (background, accent), (warning, background)]
        for number, (foreground, back) in enumerate(pairs, 1):
            terminal.init_pair(number, foreground, back)
        palette = {name: terminal.color_pair(number) for number, name in enumerate(fallback, 1)}
        screen.bkgd(' ', palette['normal'])
        return palette
    except terminal.error:
        return fallback


def popup(crew):
    def run(screen):
        palette = popup_palette(screen)
        selection = PopupSelection()
        message, rows = '', []
        screen.timeout(1000)
        while True:
            snap = crew.snapshot(force=False)
            rows = snap['nodes']
            had_selection = selection.selected is not None
            selected = selection.refresh(rows)
            if had_selection and selected is None:
                message = 'Selected agent disappeared or changed. Use j/k to select before acting.'
            screen.erase()
            height, width = screen.getmaxyx()
            if height < 5 or width < 30:
                screen.addnstr(0, 0, 'Resize or q to exit', max(0, width-1))
                if screen.getch() in (ord('q'), 27):
                    return
                continue
            screen.addnstr(0, 0, 'Sheppard | j/k select · f focus · r read · p prompt · i interrupt · s pin remote · q quit', width - 1, palette['accent'])
            start = max(0, (selected or 0) - max(1, height - 5) // 2)
            for y, row in enumerate(rows[start:start + max(1, height - 5)], 2):
                text = f"{row['machine_id']}/{row['workspace_id']}/{row['tab_id']} {row['name']} [{row['status']}] {row['telemetry']['crew_role']}"
                screen.addnstr(y, 0, text, width - 1, palette['selected'] if start + y - 2 == selected else palette['normal'])
            screen.addnstr(height - 2, 0, clean(message).replace('\n', ' ')[:width-1], width-1, palette['warning'])
            screen.refresh()
            ch = screen.getch()
            if ch in (ord('q'), 27):
                return
            if ch in (ord('j'), curses.KEY_DOWN):
                selection.navigate(rows, 1)
            elif ch in (ord('k'), curses.KEY_UP):
                selection.navigate(rows, -1)
            elif rows and ch in map(ord, 'frpis'):
                if selected is None:
                    message = 'Use j/k to explicitly select an agent before acting.'
                    continue
                node = rows[selected]
                try:
                    if ch == ord('s'):
                        crew.pin(node['machine_id'], node['workspace_id'])
                        message = 'Workspace pinned.'
                        continue
                    action = {ord('f'): 'focus', ord('r'): 'read', ord('p'): 'prompt', ord('i'): 'interrupt'}[ch]
                    request = {'key': node['key'], 'identity': node['identity'], 'request_id': secrets.token_hex(16), 'action': action}
                    if action in ('prompt', 'interrupt'):
                        screen.timeout(-1)
                        screen.addnstr(height-1, 0, 'Prompt: ' if action == 'prompt' else 'Send Ctrl+C? type yes: ', width-1)
                        curses.echo()
                        value = screen.getstr(height-1, 8 if action == 'prompt' else 26, 12000).decode(errors='replace')
                        curses.noecho()
                        screen.timeout(1000)
                        if action == 'interrupt' and value != 'yes':
                            continue
                        request['text' if action == 'prompt' else 'confirmed'] = value if action == 'prompt' else True
                    result = crew.action(request)
                    if action == 'read' and result.get('ok'):
                        screen.timeout(-1)
                        screen.erase()
                        for y, line in enumerate(result['text'].splitlines()[-max(1,height-2):]):
                            screen.addnstr(y, 0, line, width-1)
                        screen.addnstr(height-1, 0, 'Press any key to return', width-1)
                        screen.getch()
                        screen.timeout(1000)
                    message = result.get('error', result['state'])
                except CrewError as exc:
                    message = str(exc)
    curses.wrapper(run)


def open_pane(crew, entrypoint):
    if entrypoint not in ('crew', 'browser'):
        raise CrewError('Unsupported plugin pane.')
    command = ['plugin', 'pane', 'open', '--plugin', 'bloodf.herdr-sheppard', '--entrypoint', entrypoint,
               '--env', 'CREW_SCOPE_WORKSPACE=' + str(crew.local_workspace)]
    # Popup placement rejects --workspace. Its native launch context is checked
    # against the captured scope when the popup process starts.
    if entrypoint == 'browser':
        command += ['--workspace', crew.local_workspace]
    return crew.call('local', command, raw=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('popup', 'serve', 'snapshot', 'open', 'browser'), nargs='?', default='popup')
    args = parser.parse_args()
    try:
        crew = Crew()
    except CrewError as exc:
        parser.exit(2, str(exc) + '\n')
    if crew.env.get('HERDR_ENV') != '1' or not crew.local_workspace:
        parser.exit(2, 'Start Sheppard inside the intended Herdr workspace. No Herdr command was sent.\n')
    if args.mode == 'snapshot':
        print(json.dumps(crew.snapshot(), indent=2))
    elif args.mode == 'popup':
        popup(crew)
    elif args.mode in ('open', 'browser'):
        open_pane(crew, 'crew' if args.mode == 'open' else 'browser')
    else:
        server = server_for(crew)
        print(server.url, flush=True)
        if crew.env.get('HERDR_CREW_BROWSER') == '1':
            webbrowser.open(server.url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
