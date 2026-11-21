import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "log_rotation.sh"


class RotationTest(unittest.TestCase):
    def test_preserves_contents_and_bounds_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "syslog.jsonl"
            for number in range(7):
                log.write_text(f"message {number}\n")
                subprocess.run(["sh", str(SCRIPT), str(log)], check=True)
                self.assertFalse(log.exists())

            self.assertEqual(
                {p.name: p.read_text() for p in Path(directory).iterdir()},
                {f"syslog.jsonl.{n}": f"message {7 - n}\n" for n in range(1, 6)},
            )

    def test_reports_failed_rotation_without_truncating_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "syslog.jsonl"
            log.write_text("unread message\n")
            commands = Path(directory) / "bin"
            commands.mkdir()
            failing_mv = commands / "mv"
            failing_mv.write_text("#!/bin/sh\nexit 42\n")
            failing_mv.chmod(0o755)
            result = subprocess.run(
                ["sh", str(SCRIPT), str(log)],
                env={**os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"]},
                check=False,
            )
            self.assertEqual(result.returncode, 42)
            self.assertEqual(log.read_text(), "unread message\n")
            self.assertFalse(Path(str(log) + ".1").exists())


if __name__ == "__main__":
    unittest.main()
