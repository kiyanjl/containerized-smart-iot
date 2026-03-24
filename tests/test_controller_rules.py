import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "controller-service" / "rule_engine.py"
SPEC = importlib.util.spec_from_file_location("rule_engine", MODULE_PATH)
rule_engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rule_engine)


def sample_rules():
    return {
        "temp_warning": 30,
        "temp_critical": 40,
        "stock_low": 20,
        "stock_overload": 90,
        "temp_anomaly_high": 46,
        "temp_anomaly_low": -5,
        "humidity_anomaly_high": 96,
    }


class RuleEngineTests(unittest.TestCase):
    def test_high_temperature_triggers_anomaly_shutdown(self):
        decision = rule_engine.evaluate_rules(
            {"temperature": 52, "humidity": 40, "stock": 35},
            sample_rules(),
        )

        self.assertEqual(decision["state"], "ANOMALY")
        self.assertTrue(decision["action"]["emergency_shutdown"])
        self.assertEqual(decision["action"]["fan"], "ON")

    def test_high_humidity_triggers_dehumidifier(self):
        decision = rule_engine.evaluate_rules(
            {"temperature": 22, "humidity": 99, "stock": 35},
            sample_rules(),
        )

        self.assertEqual(decision["state"], "ANOMALY")
        self.assertEqual(decision["action"]["dehumidifier"], "ON")

    def test_critical_temperature_keeps_critical_even_when_stock_low(self):
        decision = rule_engine.evaluate_rules(
            {"temperature": 41, "humidity": 55, "stock": 10},
            sample_rules(),
        )

        self.assertEqual(decision["state"], "CRITICAL")
        self.assertEqual(decision["action"]["fan"], "ON")
        self.assertTrue(decision["action"]["restock_alert"])

    def test_overload_triggers_pause_deliveries(self):
        decision = rule_engine.evaluate_rules(
            {"temperature": 21, "humidity": 50, "stock": 95},
            sample_rules(),
        )

        self.assertEqual(decision["state"], "OVERLOAD")
        self.assertTrue(decision["action"]["pause_deliveries"])

    def test_low_stock_upgrades_normal_to_warning(self):
        decision = rule_engine.evaluate_rules(
            {"temperature": 20, "humidity": 50, "stock": 12},
            sample_rules(),
        )

        self.assertEqual(decision["state"], "WARNING")
        self.assertTrue(decision["action"]["restock_alert"])

    def test_default_rules_are_used_when_rules_are_missing(self):
        decision = rule_engine.evaluate_rules(
            {"temperature": 46, "humidity": 50, "stock": 40},
            {},
        )

        self.assertEqual(decision["state"], "ANOMALY")
        self.assertTrue(decision["action"]["emergency_shutdown"])


if __name__ == "__main__":
    unittest.main()
