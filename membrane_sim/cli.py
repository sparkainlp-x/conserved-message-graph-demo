"""Command-line interface for the three-node graph simulation."""
from __future__ import annotations

import argparse
import json
from typing import Any

from .experiments import budget_preview, run_paired_experiment, run_stress_cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the three-node graph-routing comparison.")
    parser.add_argument("--load-ticks", type=int, default=20, help="ticks with nonzero offered traffic")
    parser.add_argument("--rate", type=int, default=2, help="messages injected at M1 per load tick")
    parser.add_argument("--deadline", type=int, default=5, help="delivery deadline in ticks")
    parser.add_argument("--stress", action="store_true", help="also run below/at/above-capacity rates 1..4")
    parser.add_argument("--json", action="store_true", help="print full ledgers and summaries as JSON")
    args = parser.parse_args()

    comparison = run_paired_experiment(
        load_ticks=args.load_ticks,
        injection_per_tick=args.rate,
        deadline_ticks=args.deadline,
    )
    payload: dict[str, Any] = {"budgets": budget_preview(), "comparison": comparison}
    if args.stress:
        payload["stress_cases"] = run_stress_cases(
            load_ticks=args.load_ticks,
            deadline_ticks=args.deadline,
        )
    if args.json:
        print(json.dumps(payload, indent=2))
        return

    experiment = comparison["experiment"]
    print("Three-node graph-routing analogy; no physical or OES32 interpretation.")
    print(
        f"Paired traces: {experiment['load_ticks']} load ticks, "
        f"{experiment['injection_per_tick_at_M1']} messages/tick, "
        f"deadline {experiment['deadline_ticks']} ticks; identical offered traffic."
    )
    print(f"Matched budgets: {comparison['matched_budgets']}")
    print()
    header = (
        "configuration", "deadline delivery", "mean latency*", "delivered", "undelivered",
        "final backlog", "peak backlog", "losses", "ledger residual",
    )
    print(" | ".join(header))
    print(" | ".join(("---",) * len(header)))
    for name in ("shortcut_off", "shortcut_on"):
        summary = comparison[name]["summary"]
        fraction = summary["delivery_by_deadline_fraction"]
        delivery = "n/a" if fraction is None else f"{fraction:.1%} ({summary['delivered_by_deadline']}/{summary['injected']})"
        latency = summary["mean_latency_ticks_given_delivered"]
        latency_text = "n/a" if latency is None else f"{latency:.2f} ticks"
        print(" | ".join(map(str, (
            name.replace("_", " "), delivery, latency_text,
            summary["delivered_to_destination"], summary["undelivered_at_horizon"],
            summary["final_backlog"], summary["peak_backlog"], summary["losses"],
            summary["max_conservation_residual"],
        ))))
    print("\n*Latency is conditional on delivery to M3; undelivered and backlog counts are shown separately.")
    if args.stress:
        print("\nStress sweep (rate relative to each topology's nominal M1-to-M3 cut capacity):")
        print("rate | off relation / delivery / backlog / undelivered | on relation / delivery / backlog / undelivered")
        print("--- | --- | ---")
        for row in payload["stress_cases"]:
            off = row["shortcut_off"]
            on = row["shortcut_on"]
            print(
                f"{row['offered_rate_per_tick']} | "
                f"{row['shortcut_off_relation']} / {off['delivery_by_deadline_percent']:.1f}% / "
                f"{off['final_backlog']} / {off['undelivered_at_horizon']} | "
                f"{row['shortcut_on_relation']} / {on['delivery_by_deadline_percent']:.1f}% / "
                f"{on['final_backlog']} / {on['undelivered_at_horizon']}"
            )


if __name__ == "__main__":
    main()
