"""Installer argument handling: no live OMP/Herdr command or installation."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class InstallerArguments(unittest.TestCase):
    def test_help_and_unknown_arguments_do_not_touch_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'omp'
            marker = root / 'runtime-called'
            binary.write_text('#!/bin/sh\nprintf called > "' + str(marker) + '"\nexit 99\n')
            binary.chmod(0o700)
            env = dict(os.environ, HOME=directory, PATH=directory)
            for argument, status in (('--help', 0), ('--unknown-option', 2)):
                result = subprocess.run([sys.executable, str(Path(__file__).with_name('install.py')), argument],
                                        env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(status, result.returncode, result.stderr)
                self.assertFalse(marker.exists())
                self.assertFalse((root / '.omp').exists())
            self.assertIn('unrecognized arguments', result.stderr)


if __name__ == '__main__':
    unittest.main()
