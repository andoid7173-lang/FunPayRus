"""Basic safety tests for the fresh-install reset entrypoint (no deletion)."""
import subprocess
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts'/'reset_fresh.sh'

class ResetFreshTests(unittest.TestCase):
    def test_bash_syntax(self):
        p=subprocess.run(['bash','-n',str(SCRIPT)],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_help(self):
        p=subprocess.run(['bash',str(SCRIPT),'--help'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('--dry-run',p.stdout)
        self.assertIn('RESET FUNPAYRUST',p.stdout)

    def test_reject_unknown_argument(self):
        p=subprocess.run(['bash',str(SCRIPT),'--no-confirm'],capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0)

    def test_script_has_hardcoded_app_paths_and_confirmation(self):
        source=SCRIPT.read_text(encoding='utf8')
        for marker in ('APP_DIR=/opt/funpayrus','CONFIG_DIR=/etc/funpayrus',
                       'DATA_DIR=/var/lib/funpayrus','CONFIRM_PHRASE=', 'systemctl stop',
                       'findmnt','--dry-run'):
            with self.subTest(marker=marker):
                self.assertIn(marker,source)
