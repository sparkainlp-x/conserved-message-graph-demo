from __future__ import annotations

import copy
import unittest

from membrane_sim import (
    E_OFF,
    E_ON,
    EdgeSpec,
    GraphSimulation,
    configuration_budgets,
    default_edge_settings,
    run_paired_experiment,
    run_stress_cases,
)


class TopologyAndBudgetTests(unittest.TestCase):
    def test_allowed_edge_matrices_match_contract(self) -> None:
        self.assertEqual(E_OFF, ((0, 1, 0), (1, 0, 1), (0, 1, 0)))
        self.assertEqual(E_ON, ((0, 1, 1), (1, 0, 1), (1, 1, 0)))
        self.assertEqual(
            {(i, j) for i in range(3) for j in range(3) if E_ON[i][j] and not E_OFF[i][j]},
            {(0, 2), (2, 0)},
        )

    def test_default_capacity_and_nominal_loss_budgets_match(self) -> None:
        off = configuration_budgets(E_OFF, default_edge_settings(False))
        on = configuration_budgets(E_ON, default_edge_settings(True))
        self.assertEqual(off["total_capacity_per_tick"], on["total_capacity_per_tick"])
        self.assertEqual(
            off["capacity_weighted_nominal_loss_per_tick"],
            on["capacity_weighted_nominal_loss_per_tick"],
        )
        self.assertEqual(off["total_capacity_per_tick"], 8)
        self.assertEqual(off["capacity_weighted_nominal_loss_per_tick"], "2/5")

    def test_each_directed_edge_has_independent_settings(self) -> None:
        settings = default_edge_settings(True)
        self.assertEqual(settings[("M1", "M3")].capacity, 2)
        self.assertEqual(settings[("M3", "M1")].capacity, 2)
        self.assertEqual(settings[("M1", "M2")].capacity, 1)
        self.assertEqual(settings[("M2", "M1")].capacity, 1)


class SimulationTests(unittest.TestCase):
    def test_shortcut_reduces_latency_for_single_loss_free_message(self) -> None:
        off_settings = {
            edge: EdgeSpec(capacity=3, latency=1, loss_numerator=0)
            for edge in default_edge_settings(False)
        }
        on_settings = {
            edge: EdgeSpec(capacity=3, latency=1, loss_numerator=0)
            for edge in default_edge_settings(True)
        }
        injections = [{"M1": 1}, {}, {}]
        absorption = [{"M3": 1}] * 3
        off = GraphSimulation(E_OFF, off_settings, deadline_ticks=3)
        on = GraphSimulation(E_ON, on_settings, deadline_ticks=3)
        off.run(injections, absorption)
        on.run(injections, absorption)
        self.assertEqual(off.summary()["mean_latency_ticks_given_delivered"], 2)
        self.assertEqual(on.summary()["mean_latency_ticks_given_delivered"], 1)
        self.assertEqual(off.summary()["delivered_by_deadline"], 1)
        self.assertEqual(on.summary()["delivered_by_deadline"], 1)

    def test_conservation_and_capacity_hold_each_tick(self) -> None:
        settings = default_edge_settings(True)
        sim = GraphSimulation(E_ON, settings, deadline_ticks=4)
        injections = [{"M1": 4} for _ in range(20)] + [{} for _ in range(4)]
        absorptions = [{"M3": 1000} for _ in injections]
        rows = sim.run(injections, absorptions)
        self.assertTrue(all(row["residual"] == 0 for row in rows))
        for row in rows:
            self.assertEqual(
                row["M_end"] - row["M_start"],
                row["I"] - row["B"] - row["L"],
            )
            for edge_name, used in row["capacity_used"].items():
                source, destination = edge_name.split("->")
                self.assertLessEqual(used, settings[(source, destination)].capacity)
        self.assertGreater(sim.summary()["final_backlog"], 0)
        self.assertGreater(sim.summary()["undelivered_at_horizon"], 0)

    def test_delayed_messages_remain_in_sender_inventory(self) -> None:
        settings = {
            ("M1", "M2"): EdgeSpec(capacity=2, latency=3),
            ("M2", "M1"): EdgeSpec(capacity=2, latency=1),
            ("M2", "M3"): EdgeSpec(capacity=2, latency=1),
            ("M3", "M2"): EdgeSpec(capacity=2, latency=1),
        }
        sim = GraphSimulation(E_OFF, settings, deadline_ticks=5)
        first = sim.step({"M1": 1})
        second = sim.step()
        self.assertEqual(first["M_end"], 1)
        self.assertEqual(second["nodes"]["M1"], 1)
        self.assertEqual(second["pending_by_edge"]["M1->M2"], 1)

    def test_cumulative_rational_loss_quota_is_deterministic(self) -> None:
        settings = {
            ("M1", "M2"): EdgeSpec(capacity=20, latency=2, loss_numerator=1, loss_denominator=20),
            ("M2", "M1"): EdgeSpec(capacity=2, latency=1),
            ("M2", "M3"): EdgeSpec(capacity=2, latency=1),
            ("M3", "M2"): EdgeSpec(capacity=2, latency=1),
        }
        sim = GraphSimulation(E_OFF, settings, deadline_ticks=4)
        sim.step({"M1": 20})
        row = sim.step()
        self.assertEqual(row["L"], 1)
        self.assertEqual(row["M_end"], 19)
        self.assertEqual(row["pending_by_edge"]["M1->M2"], 19)
        self.assertEqual(row["residual"], 0)

    def test_invalid_later_input_does_not_mutate_state(self) -> None:
        sim = GraphSimulation(E_OFF, default_edge_settings(False))
        sim.step({"M1": 2})
        snapshot = {
            "tick": sim.tick,
            "inventory": copy.deepcopy(sim.inventory),
            "rows": copy.deepcopy(sim.rows),
            "pending": copy.deepcopy(sim._pending),
            "ready": copy.deepcopy(sim._ready),
            "attempts": {key: arc.attempted_total for key, arc in sim.arcs.items()},
            "cohort_sequence": sim._cohort_sequence,
        }
        with self.assertRaises(ValueError):
            sim.step({"M1": 1, "NOPE": 3}, {"M3": 1})
        self.assertEqual(sim.tick, snapshot["tick"])
        self.assertEqual(sim.inventory, snapshot["inventory"])
        self.assertEqual(sim.rows, snapshot["rows"])
        self.assertEqual(sim._pending, snapshot["pending"])
        self.assertEqual(sim._ready, snapshot["ready"])
        self.assertEqual({key: arc.attempted_total for key, arc in sim.arcs.items()}, snapshot["attempts"])
        self.assertEqual(sim._cohort_sequence, snapshot["cohort_sequence"])

    def test_invalid_counts_and_edge_settings_are_rejected(self) -> None:
        sim = GraphSimulation(E_OFF, default_edge_settings(False))
        for invalid in (True, -1, 2.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                sim.step({"M1": invalid})
        with self.assertRaises(ValueError):
            GraphSimulation(E_OFF, {("M1", "M2"): EdgeSpec(1, 1)})
        with self.assertRaises(ValueError):
            EdgeSpec(capacity=1, latency=0)

    def test_deadline_delivery_and_conditional_latency_are_both_reported(self) -> None:
        result = run_paired_experiment(load_ticks=8, injection_per_tick=2, deadline_ticks=5)
        for configuration in ("shortcut_off", "shortcut_on"):
            summary = result[configuration]["summary"]
            self.assertIn("delivery_by_deadline_fraction", summary)
            self.assertIn("mean_latency_ticks_given_delivered", summary)
            self.assertIn("undelivered_at_horizon", summary)
            self.assertIn("final_backlog", summary)
            self.assertEqual(summary["max_conservation_residual"], 0)
        self.assertTrue(result["experiment"]["identical_injection_trace"])
        self.assertTrue(result["experiment"]["identical_absorption_request_trace"])

    def test_stress_cases_span_below_at_and_above_nominal_capacity(self) -> None:
        cases = run_stress_cases(load_ticks=8, deadline_ticks=4)
        self.assertEqual([row["offered_rate_per_tick"] for row in cases], [1, 2, 3, 4])
        self.assertEqual(cases[0]["shortcut_off_relation"], "below")
        self.assertEqual(cases[1]["shortcut_off_relation"], "at")
        self.assertEqual(cases[2]["shortcut_off_relation"], "above")
        self.assertEqual(cases[2]["shortcut_on_relation"], "at")
        self.assertEqual(cases[3]["shortcut_on_relation"], "above")


if __name__ == "__main__":
    unittest.main()
