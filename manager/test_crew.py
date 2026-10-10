"""Fixture-only contract tests. Never contacts the user's Herdr."""
import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

from crew import Crew, CrewError, PopupSelection, clean, open_pane, popup_palette, server_for


def snapshot():
    agent = {'pane_id': 'p1', 'terminal_id': 'term1', 'workspace_id': 'w1', 'tab_id': 't1',
             'agent_session': {'kind': 'id', 'value': 'session1', 'source': 'reported', 'agent': 'omp'},
             'name': 'Worker', 'agent_status': 'idle', 'interactive_ready': True,
             'tokens': {'crew_role': 'worker', 'crew_owner': 'worker1', 'crew_at': str(time.time()*1000), 'crew_session': 'session1'}}
    return {'workspaces': [{'workspace_id': 'w1', 'label': 'Project'}, {'workspace_id': 'w2', 'label': 'Other'}],
            'tabs': [{'workspace_id': 'w1', 'tab_id': 't1', 'label': 'Phase'}, {'workspace_id': 'w2', 'tab_id': 't2', 'label': 'Other'}],
            'panes': [copy.deepcopy(agent)], 'agents': [agent]}


class Fixture:
    def __init__(self):
        self.snapshots = {'local': snapshot(), 'remote-id': snapshot()}
        self.calls = []
        self.failure = None
        self.fresh = None

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        assert args[0] == '/fixture/herdr'
        machine = args[2] if '--machine' in args else 'local'
        rest = args[3:] if '--machine' in args else args[1:]
        if self.failure == machine or self.failure == rest[1]:
            raise subprocess.TimeoutExpired(args, 5)
        if rest == ['machine', 'list', '--json']:
            data = [{'id': 'remote-id', 'label': 'Remote', 'target': 'PRIVATE SSH', 'session': 'PRIVATE PATH', 'enabled': True}]
        elif rest == ['api', 'snapshot']:
            data = {'result': {'snapshot': self.snapshots[machine]}}
        elif rest[:2] == ['agent', 'get']:
            data = {'result': {'agent': self.fresh or self.snapshots[machine]['agents'][0]}}
        elif rest[:2] == ['agent', 'read']:
            return subprocess.CompletedProcess(args, 0, '\x1b[31mBearer abc-secret\napi_key=key-secret\nplain output', '')
        else:
            data = {'result': {}}
        return subprocess.CompletedProcess(args, 0, json.dumps(data), '')


class CrewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.crew = Crew({'HERDR_ENV': '1', 'HERDR_WORKSPACE_ID': 'w1', 'HERDR_BIN_PATH': '/fixture/herdr'}, self.fixture)

    def request(self, node, action='prompt', **extra):
        return dict(request_id='request1', key=node['key'], identity=node['identity'], action=action, text='Continue task', **extra)

    def test_popup_titanium_palette_and_monochrome_fallback(self):
        class Terminal:
            A_NORMAL, A_REVERSE, COLORS, COLOR_PAIRS = 0, 1, 256, 256
            error = RuntimeError
            def __init__(self, colored=True):
                self.colored, self.pairs = colored, []
            def start_color(self):
                pass
            def has_colors(self):
                return self.colored
            def init_pair(self, *pair):
                self.pairs.append(pair)
            def color_pair(self, number):
                return number * 256
        class Screen:
            def __init__(self):
                self.background = None
            def bkgd(self, character, attribute):
                self.background = (character, attribute)
        terminal, screen = Terminal(), Screen()
        palette = popup_palette(screen, terminal)
        self.assertEqual((2, 39, 234), terminal.pairs[1])
        self.assertEqual((' ', palette['normal']), screen.background)
        monochrome, mono_screen = Terminal(False), Screen()
        fallback = popup_palette(mono_screen, monochrome)
        self.assertEqual(1, fallback['selected'])
        self.assertEqual([], monochrome.pairs)
        self.assertIsNone(mono_screen.background)

    def test_popup_selection_follows_qualified_identity_after_reordering(self):
        a = {'key': '["local","w1","t1","p1"]', 'identity': 'session-a'}
        b = {'key': '["remote","w1","t1","p1"]', 'identity': 'session-b'}
        selected = PopupSelection()
        self.assertEqual(0, selected.refresh([a, b]))
        self.assertEqual(1, selected.refresh([b, a]))
        self.assertEqual((a['key'], a['identity']), selected.selected)
        self.assertEqual(0, selected.navigate([b, a], -1))
        self.assertEqual((b['key'], b['identity']), selected.selected)

    def test_popup_selection_clears_on_removal_or_replacement(self):
        a = {'key': 'qualified-a', 'identity': 'session-a'}
        b = {'key': 'qualified-b', 'identity': 'session-b'}
        selected = PopupSelection()
        selected.refresh([a, b])
        self.assertIsNone(selected.refresh([b]))
        self.assertIsNone(selected.selected)
        self.assertIsNone(selected.refresh([b, a]))
        self.assertEqual(0, selected.navigate([b, a], 1))
        replacement = dict(b, identity='replacement')
        self.assertIsNone(selected.refresh([replacement, a]))
        self.assertIsNone(selected.refresh([replacement, a]))
        self.assertIsNone(selected.selected)
        self.assertEqual(0, selected.navigate([replacement, a], -1))
        self.assertEqual(('qualified-b', 'replacement'), selected.selected)

    def test_outside_herdr_sends_no_commands(self):
        crew = Crew({}, self.fixture)
        with self.assertRaises(CrewError):
            crew.call('local', ['api', 'snapshot'])
        crew.snapshot()
        self.assertEqual([], self.fixture.calls)

    def test_graph_machine_qualification_and_scope(self):
        graph = self.crew.snapshot()
        self.assertEqual(2, len(graph['nodes']))
        local, remote = graph['nodes']
        self.assertNotEqual(local['key'], remote['key'])
        self.assertTrue(local['actionable'])
        self.assertFalse(remote['actionable'])
        self.assertNotIn('PRIVATE', json.dumps(graph))
        self.assertEqual(['w1'], [w['id'] for w in graph['machines'][0]['workspaces']])
        self.assertEqual('worker', local['telemetry']['crew_role'])
        self.crew.pin('remote-id', 'w1')
        graph = self.crew.snapshot()
        self.assertTrue(graph['nodes'][1]['actionable'])
        self.crew.action(self.request(graph['nodes'][1]))
        self.assertIn(['/fixture/herdr', '--machine', 'remote-id', 'agent', 'prompt', 'p1', 'Continue task'], self.fixture.calls)

    def test_remote_failure_never_falls_back(self):
        self.fixture.failure = 'remote-id'
        graph = self.crew.snapshot()
        self.assertTrue(graph['machines'][1]['error'])
        self.assertEqual(1, len(graph['nodes']))
        self.assertEqual(1, self.fixture.calls.count(['/fixture/herdr', 'api', 'snapshot']))

    def test_parent_edges(self):
        snap = self.fixture.snapshots['local']
        child = copy.deepcopy(snap['agents'][0])
        child.update(pane_id='p2', terminal_id='term2')
        child['tokens'].update(crew_owner='worker2', crew_parent='worker1')
        snap['panes'].append(child)
        snap['agents'].append(child)
        self.assertEqual(1, len(self.crew.snapshot()['edges']))

    def test_malformed_remote_inventory_is_partial(self):
        original = self.fixture.__call__
        def runner(args, **kwargs):
            if args[1:] == ['machine', 'list', '--json']:
                return subprocess.CompletedProcess(args, 0, json.dumps([None, 'bad', {'id': 'remote-id', 'enabled': True}]), '')
            return original(args, **kwargs)
        self.crew.runner = runner
        self.fixture.snapshots['remote-id'] = {'workspaces': [None, {}, {'workspace_id': 'w1'}], 'tabs': 'bad', 'panes': None, 'agents': [None]}
        graph = self.crew.snapshot()
        self.assertEqual(1, len(graph['nodes']))
        self.assertEqual(2, len(graph['machines']))
        self.assertEqual([], graph['machines'][1]['workspaces'][0]['tabs'])

    def test_cached_snapshot_coalesces_polling(self):
        self.crew.snapshot(force=False)
        count = len(self.fixture.calls)
        self.crew.snapshot(force=False)
        self.assertEqual(count, len(self.fixture.calls))

    def test_stale_or_replaced_telemetry_unknown(self):
        self.fixture.snapshots['local']['agents'][0]['tokens']['crew_at'] = '1'
        self.assertEqual('unknown', self.crew.snapshot()['nodes'][0]['telemetry']['crew_role'])
        self.fixture.snapshots['local']['agents'][0]['tokens'].update(crew_at=str(time.time()*1000), crew_session='different')
        self.assertEqual('unknown', self.crew.snapshot()['nodes'][0]['telemetry']['crew_role'])

    def test_path_session_telemetry_exact_match_required(self):
        agent = self.fixture.snapshots['local']['agents'][0]
        agent['agent_session'] = {'kind': 'path', 'value': '/private/native/session.jsonl'}
        agent['tokens']['crew_session_path'] = hashlib.sha256(agent['agent_session']['value'].encode()).hexdigest()
        graph = self.crew.snapshot()
        self.assertEqual('worker', graph['nodes'][0]['telemetry']['crew_role'])
        self.assertNotIn('/private/native/session.jsonl', json.dumps(graph))
        agent['tokens']['crew_session_path'] = 'wrong'
        self.assertEqual('unknown', self.crew.snapshot()['nodes'][0]['telemetry']['crew_role'])

    def test_missing_unknown_or_empty_session_telemetry_unknown(self):
        agent = self.fixture.snapshots['local']['agents'][0]
        for ref in (None, {}, {'kind': 'unknown', 'value': 'session1'}, {'kind': 'id', 'value': ''}, {'kind': 'path', 'value': ''}):
            agent['agent_session'] = ref
            self.assertEqual('unknown', self.crew.snapshot()['nodes'][0]['telemetry']['crew_role'])

    def test_browser_and_popup_open_native_workspace_panes(self):
        open_pane(self.crew, 'browser')
        self.assertEqual(['/fixture/herdr', 'plugin', 'pane', 'open', '--plugin', 'bloodf.herdr-sheppard', '--entrypoint', 'browser', '--env', 'CREW_SCOPE_WORKSPACE=w1', '--workspace', 'w1'], self.fixture.calls[-1])
        open_pane(self.crew, 'crew')
        self.assertEqual('crew', self.fixture.calls[-1][7])
        self.assertNotIn('--workspace', self.fixture.calls[-1])
        self.assertIn('CREW_SCOPE_WORKSPACE=w1', self.fixture.calls[-1])
        with self.assertRaises(CrewError):
            open_pane(Crew({}, self.fixture), 'browser')

    def test_popup_native_context_without_workspace_env(self):
        env = {'HERDR_ENV': '1', 'HERDR_PLUGIN_ID': 'bloodf.herdr-sheppard', 'HERDR_BIN_PATH': '/fixture/herdr',
               'HERDR_PLUGIN_CONTEXT_JSON': json.dumps({'workspace_id': 'w1'}), 'CREW_SCOPE_WORKSPACE': 'w1'}
        crew = Crew(env, self.fixture)
        self.assertEqual('w1', crew.local_workspace)
        self.assertNotIn('HERDR_WORKSPACE_ID', crew.env)
        self.assertEqual(2, len(crew.snapshot()['nodes']))

    def test_plugin_scope_mismatch_or_invalid_context_sends_no_commands(self):
        base = {'HERDR_ENV': '1', 'HERDR_PLUGIN_ID': 'bloodf.herdr-sheppard', 'HERDR_BIN_PATH': '/fixture/herdr',
                'HERDR_PLUGIN_CONTEXT_JSON': json.dumps({'workspace_id': 'w1'}), 'CREW_SCOPE_WORKSPACE': 'w1'}
        for replacement in ({'CREW_SCOPE_WORKSPACE': 'w2'}, {'HERDR_WORKSPACE_ID': 'w2'},
                            {'HERDR_PLUGIN_CONTEXT_JSON': 'invalid'}, {'HERDR_PLUGIN_CONTEXT_JSON': '{}'},
                            {'HERDR_PLUGIN_ID': 'other.plugin'}):
            with self.assertRaises(CrewError):
                Crew(dict(base, **replacement), self.fixture)
        self.assertEqual([], self.fixture.calls)

    def test_replaced_terminal_rejected(self):
        node = self.crew.snapshot()['nodes'][0]
        self.fixture.fresh = copy.deepcopy(self.fixture.snapshots['local']['agents'][0])
        self.fixture.fresh['terminal_id'] = 'replacement'
        with self.assertRaises(CrewError):
            self.crew.action(self.request(node))
        self.assertFalse(any('prompt' in c for c in self.fixture.calls))

    def test_replaced_session_rejected(self):
        node = self.crew.snapshot()['nodes'][0]
        self.fixture.fresh = copy.deepcopy(self.fixture.snapshots['local']['agents'][0])
        self.fixture.fresh['agent_session']['value'] = 'replacement'
        with self.assertRaises(CrewError):
            self.crew.action(self.request(node))

    def test_busy_prompt_and_unpinned_remote_rejected(self):
        graph = self.crew.snapshot()
        with self.assertRaises(CrewError):
            self.crew.action(self.request(graph['nodes'][1]))
        self.fixture.snapshots['local']['agents'][0]['agent_status'] = 'working'
        with self.assertRaises(CrewError):
            self.crew.action(self.request(graph['nodes'][0]))

    def test_idempotent_and_conflicting_requests(self):
        node = self.crew.snapshot()['nodes'][0]
        req = self.request(node)
        one = self.crew.action(req)
        self.assertEqual(one, self.crew.action(req))
        self.assertEqual(1, sum('prompt' in c for c in self.fixture.calls))
        with self.assertRaises(CrewError):
            self.crew.action(dict(req, text='different'))

    def test_ambiguous_mutation_never_replayed(self):
        node = self.crew.snapshot()['nodes'][0]
        self.fixture.failure = 'prompt'
        req = self.request(node)
        self.assertEqual('uncertain', self.crew.action(req)['state'])
        self.fixture.failure = None
        self.assertEqual('uncertain', self.crew.action(req)['state'])
        self.assertEqual(1, sum('prompt' in c for c in self.fixture.calls))

    def test_read_redacted_interrupt_confirmation_and_allowlist(self):
        node = self.crew.snapshot()['nodes'][0]
        read = self.crew.action(self.request(node, 'read'))
        self.assertNotIn('secret', read['text'])
        self.assertNotIn('\x1b', read['text'])
        with self.assertRaises(CrewError):
            self.crew.action(self.request(node, 'interrupt'))
        self.fixture.snapshots['local']['agents'][0].update(agent_status='working', interactive_ready=False)
        req = self.request(node, 'interrupt', confirmed=True)
        req['request_id'] = 'interrupt1'
        self.assertTrue(self.crew.action(req)['ok'])
        with self.assertRaises(CrewError):
            self.crew.action(self.request(node, 'close'))

    def test_interrupt_rejects_settled_or_unknown_fresh_state(self):
        node = self.crew.snapshot()['nodes'][0]
        for status in ('idle', 'done', 'unknown'):
            self.fixture.snapshots['local']['agents'][0]['agent_status'] = status
            with self.assertRaises(CrewError):
                self.crew.action(self.request(node, 'interrupt', confirmed=True))
        self.assertFalse(any('send-keys' in args for args in self.fixture.calls))

    def test_pin_scope_and_changes(self):
        graph = self.crew.snapshot()
        with self.assertRaises(CrewError):
            self.crew.pin('local', 'w2')
        with self.assertRaises(CrewError):
            self.crew.pin('remote-id', 'w999')
        self.crew.pin('remote-id', 'w1')
        node = self.crew.snapshot()['nodes'][1]
        self.crew.pin('remote-id', 'w2')
        with self.assertRaises(CrewError):
            self.crew.action(self.request(node))

    def test_http_capability_host_origin_and_size(self):
        with tempfile.TemporaryDirectory() as temp:
            dashboard = Path(temp)/'dashboard.html'
            dashboard.write_text('<html>Fixture dashboard</html>')
            server = server_for(self.crew, dashboard)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host = '127.0.0.1:' + str(server.server_port)
            def request(method, path, body=None, headers=None):
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                conn.request(method, path, body=body, headers=headers or {})
                response = conn.getresponse()
                result = response.status, response.read()
                conn.close()
                return result
            try:
                self.assertEqual(403, request('GET', '/api/snapshot')[0])
                headers = {'X-Crew-Token': server.token}
                self.assertEqual(200, request('GET', '/api/snapshot', headers=headers)[0])
                self.assertEqual(403, request('GET', '/', headers={'Host': 'evil.test'})[0])
                headers.update({'Content-Type': 'application/json', 'Origin': 'http://evil.test'})
                self.assertEqual(403, request('POST', '/api/pin', '{}', headers)[0])
                headers['Origin'] = 'http://' + host
                self.assertEqual(413, request('POST', '/api/pin', 'a'*17000, headers)[0])
                self.assertEqual(200, request('POST', '/api/pin', json.dumps({'machine_id': 'remote-id', 'workspace_id': 'w1'}), headers)[0])
                self.assertNotIn(server.token.encode(), request('GET', '/')[1])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_real_subprocess_fixture_only(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp)/'fake-herdr'
            fake.write_text('#!/usr/bin/env python3\nimport json\nprint(json.dumps([]))\n')
            fake.chmod(0o700)
            crew = Crew(dict(os.environ, HERDR_ENV='1', HERDR_WORKSPACE_ID='fixture', HERDR_BIN_PATH=str(fake)))
            self.assertEqual([], crew.call('local', ['machine', 'list', '--json']))


if __name__ == '__main__':
    unittest.main()
