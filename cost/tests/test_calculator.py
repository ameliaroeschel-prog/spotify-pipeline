"""Offline tests for cost/calculator.py on a SYNTHETIC fixture (no API key, no network).

    python3 -m unittest discover -s cost/tests -v
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
COST = HERE.parent
ROOT = COST.parent
sys.path.insert(0, str(COST))
sys.path.insert(0, str(HERE))

import calculator  # noqa: E402  (importing must not make any calls)
import make_fixture  # noqa: E402


class CalculatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.run_dir = Path(cls.tmp.name) / "run"
        cls.cost_dir = Path(cls.tmp.name) / "cost"
        cls.run_dir.mkdir()
        cls.fixture = make_fixture.build(cls.run_dir / "state.db")
        calculator.export(cls.run_dir, cls.cost_dir)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def replay(self, *extra):
        argv = ["--cost-dir", str(self.cost_dir), "--out", str(self.cost_dir / "report.md"), *extra]
        ap = calculator.argparse.ArgumentParser()
        calculator.add_replay_args(ap)
        return calculator.compute(ap.parse_args(argv)), ap.parse_args(argv)

    def test_hand_arithmetic(self):
        res, _ = self.replay()
        cold = res["measured"]["cold"]
        # enrich req-1: (3000-1000)*0.10e-6 + 1000*0.01e-6 + 2000*0.50e-6 = 0.000200 + 0.000010 + 0.001000
        # enrich req-2 (flex): 2500*0.05e-6 + 1500*0.25e-6 = 0.000125 + 0.000375
        self.assertAlmostEqual(cold["roles"]["enrich"]["cost"], 0.001210 + 0.000500, places=12)
        self.assertAlmostEqual(cold["roles"]["verify"]["cost"], 800 * 0.10e-6, places=12)       # decisions, input only
        self.assertAlmostEqual(cold["roles"]["group"]["cost"], 5000 * 0.10e-6 + 400 * 0.50e-6, places=12)
        self.assertAlmostEqual(cold["roles"]["memo"]["cost"], 2000 * 0.10e-6 + 800 * 0.50e-6, places=12)
        self.assertAlmostEqual(cold["total"]["cost"], 0.003090, places=12)
        self.assertEqual(cold["total"]["unresolved"], 3)       # 3 failed attempts with unknown usage
        self.assertEqual(cold["roles"]["enrich"]["retries"], 2)
        self.assertEqual(res["measured"]["warm"]["roles"]["enrich"]["attempts"], 0)
        self.assertTrue(res["input"]["ok"])
        self.assertEqual(sorted(res["record_ids"]), sorted(res["input"]["ids"]))
        self.assertEqual(res["status"].get("failed", 0), 100 - res["status"]["completed"])

    def test_rate_multiplier_doubles_api_only(self):
        a, _ = self.replay()
        b, _ = self.replay("--rate-multiplier", "2")
        self.assertAlmostEqual(b["measured"]["cold"]["total"]["cost"], 2 * a["measured"]["cold"]["total"]["cost"], places=12)
        for name in a["projection"]["scenarios"]:
            for label in ("reuse", "no_reuse"):
                self.assertAlmostEqual(b["projection"]["scenarios"][name][label]["total"],
                                       2 * a["projection"]["scenarios"][name][label]["total"], places=9)
        self.assertEqual(a["projection"]["time_totals"], b["projection"]["time_totals"])
        self.assertEqual(a["timing"]["cold"]["e2e"], b["timing"]["cold"]["e2e"])
        self.assertEqual(a["timing"]["cold"]["stages"], b["timing"]["cold"]["stages"])

    def test_projected_rows_do_not_change_measured(self):
        a, _ = self.replay()
        b, _ = self.replay("--projected-rows", "10000")
        self.assertEqual(a["status"], b["status"])
        self.assertEqual(a["timing"], b["timing"])
        self.assertEqual(a["measured"]["cold"]["total"]["cost"], b["measured"]["cold"]["total"]["cost"])
        self.assertNotEqual(a["projection"]["scenarios"]["base"]["reuse"]["total"],
                            b["projection"]["scenarios"]["base"]["reuse"]["total"])

    def test_budget_warning_cli(self):
        out = subprocess.run([sys.executable, str(COST / "calculator.py"), "--cost-dir", str(self.cost_dir),
                              "--out", str(self.cost_dir / "r.md"), "--budget", "0.01"],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("WARNING:", out)
        out = subprocess.run([sys.executable, str(COST / "calculator.py"), "--cost-dir", str(self.cost_dir),
                              "--out", str(self.cost_dir / "r.md"), "--budget", "1000000"],
                             capture_output=True, text=True, check=True).stdout
        self.assertNotIn("WARNING:", out)

    def test_pilot_requires_yes(self):
        out = subprocess.run([sys.executable, str(COST / "calculator.py"), "pilot", "--budget", "0.25",
                              "--run-dir", "runs/does-not-matter"], capture_output=True, text=True, check=True).stdout
        self.assertIn("Nothing was run", out)
        self.assertFalse((ROOT / "runs" / "does-not-matter").exists())


if __name__ == "__main__":
    unittest.main()
