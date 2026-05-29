"""Static checks on the shell scripts in `scripts/`.

`bash -n` parses each script without running it, catching syntax errors (an
unterminated heredoc, a missing `fi`, a typo'd `$()`) the moment they're
introduced — no server, no bench. The scripts are the source of truth for
server-side logic (Taste 11-13), so a broken one is a production bug; this is
the cheapest net that catches the common edit mistakes.
"""

import subprocess
import unittest
from pathlib import Path

# atlas/tests/test_scripts_static.py -> repo root is three parents up.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"


class TestScriptsStatic(unittest.TestCase):
	def test_every_shell_script_parses(self) -> None:
		scripts = sorted(_SCRIPTS_DIR.rglob("*.sh"))
		self.assertTrue(scripts, f"no scripts found under {_SCRIPTS_DIR}")
		for script in scripts:
			with self.subTest(script=str(script.relative_to(_REPO_ROOT))):
				result = subprocess.run(
					["bash", "-n", str(script)],
					capture_output=True,
					text=True,
				)
				self.assertEqual(
					result.returncode,
					0,
					f"bash -n failed for {script}:\n{result.stderr}",
				)
