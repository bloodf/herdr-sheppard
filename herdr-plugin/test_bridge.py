"""Fixture-only native bridge tests; no real Herdr commands."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.fake = self.directory / 'fake-herdr'
        self.fake.write_text('#!/usr/bin/env python3\nimport json,os,sys\nfrom pathlib import Path\nPath(os.environ["CALLS"]).write_text(json.dumps(sys.argv[1:]))\nprint(os.environ["RESPONSE"])\n')
        self.fake.chmod(0o700)
        self.env = dict(os.environ, HERDR_ENV='1', HERDR_BIN_PATH=str(self.fake), HERDR_PLUGIN_ID='bloodf.herdr-sheppard',
                        HERDR_PLUGIN_ROOT=str(ROOT), HERDR_PLUGIN_CONTEXT_JSON=json.dumps({'focused_pane_id': 'fixture:p1'}),
                        CALLS=str(self.directory/'calls'), RESPONSE=json.dumps({'result': {'pane': {'agent_session': {'agent': 'claude', 'kind': 'id', 'value': 'fixture-session'}}}}))

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, name, args=(), env=None):
        return subprocess.run(['bash', str(ROOT/'herdr'/name), *args], env=env or self.env, input='', capture_output=True, text=True, timeout=5)

    def test_outside_herdr_no_command(self):
        env = dict(self.env, HERDR_ENV='')
        for name in ('resolve.sh', 'open.sh', 'pane.sh', 'keys.sh'):
            self.assertNotEqual(0, self.run_script(name, env=env).returncode)
        self.assertFalse((self.directory/'calls').exists())

    def test_native_claude_id_resolves(self):
        result = self.run_script('resolve.sh')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({'provider': 'claude', 'kind': 'id', 'value': 'fixture-session'}, json.loads(result.stdout))
        self.assertEqual(['pane', 'get', 'fixture:p1'], json.loads((self.directory/'calls').read_text()))

    def test_omp_graph_explicitly_unsupported(self):
        env = dict(self.env, RESPONSE=json.dumps({'result': {'pane': {'agent_session': {'agent': 'omp', 'kind': 'path', 'value': '/fixture/session.jsonl'}}}}))
        result = self.run_script('resolve.sh', env=env)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('worker manager', result.stderr)
        self.assertNotIn('/fixture/session.jsonl', result.stderr)

    def test_missing_binary_no_install_and_manager_not_gated(self):
        result = self.run_script('ensure-sheppard.sh', env=dict(self.env, SHEPPARD_BIN=str(self.directory/'absent')))
        self.assertNotEqual(0, result.returncode)
        self.assertIn('cargo build --release --bin sheppard', result.stderr)
        self.assertIn('Python manager remains available', result.stderr)
        self.assertFalse((self.directory/'calls').exists())
        manifest = (ROOT/'herdr-plugin.toml').read_text()
        self.assertNotIn('[[build]]', manifest)
        self.assertNotIn('ensure-zoe', manifest)

    def test_graph_action_correct_fork_plugin(self):
        result = self.run_script('pane.sh', ('open', 'split'))
        self.assertEqual(0, result.returncode)
        self.assertEqual(['plugin', 'pane', 'open', '--plugin', 'bloodf.herdr-sheppard', '--entrypoint', 'graph', '--placement', 'split'], json.loads((self.directory/'calls').read_text()))

    def test_selected_fork_binary_receives_native_session(self):
        binary = self.directory/'selected-sheppard'
        binary.write_text('#!/usr/bin/env python3\nimport json,os,sys\nfrom pathlib import Path\nif "--help" in sys.argv: print("--provider"); sys.exit(0)\nPath(os.environ["BINARY_CALLS"]).write_text(json.dumps(sys.argv[1:]))\n')
        binary.chmod(0o700)
        result = self.run_script('open.sh', env=dict(self.env, SHEPPARD_BIN=str(binary), BINARY_CALLS=str(self.directory/'binary-calls')))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(['--provider', 'claude', '--follow', 'fixture-session'], json.loads((self.directory/'binary-calls').read_text()))


if __name__ == '__main__':
    unittest.main()
