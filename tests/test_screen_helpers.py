import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import screen  # noqa: E402


class NeedsFullAnalysisTests(unittest.TestCase):
    def test_unknown_ticker_needs_full(self):
        self.assertTrue(screen.needs_full_analysis({}))

    def test_ticker_with_only_alert_dates_needs_full(self):
        self.assertTrue(screen.needs_full_analysis({"move_alert_last_alert_date": "2026-10-01"}))

    def test_ticker_with_completed_full_run_does_not(self):
        self.assertFalse(screen.needs_full_analysis({"last_close": 12.3, "volume_avg20": 1000.0}))

    def test_reactivated_ticker_needs_full_despite_old_state(self):
        self.assertTrue(screen.needs_full_analysis({"last_close": 12.3, "inactive": True}))


class CarryOverInactiveStateTests(unittest.TestCase):
    def test_removed_ticker_keeps_dedup_history_and_is_marked_inactive(self):
        state = {
            "KEEP": {"last_close": 1.0},
            "GONE": {"last_close": 2.0, "volume_breakout_last_alert_date": "2026-10-01"},
        }
        carried = screen.carry_over_inactive_state(state, {"KEEP"})
        self.assertEqual(set(carried), {"GONE"})
        self.assertEqual(carried["GONE"]["volume_breakout_last_alert_date"], "2026-10-01")
        self.assertTrue(carried["GONE"]["inactive"])

    def test_input_state_is_not_mutated(self):
        state = {"GONE": {"last_close": 2.0}}
        screen.carry_over_inactive_state(state, set())
        self.assertNotIn("inactive", state["GONE"])


if __name__ == "__main__":
    unittest.main()
