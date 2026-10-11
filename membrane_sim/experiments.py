"""Paired comparison and stress-case helpers for the three-node simulator."""
from __future__ import annotations

from typing import Any

from .core import (
    E_OFF,
    E_ON,
    NODES,
    EdgeSpec,
    GraphSimulation,
    configuration_budgets,
    default_edge_settings,
)


def _positive_int(name: str, value: int, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def make_traces(
    *, load_ticks: int, injection_per_tick: int, deadline_ticks: int
) -> tuple[list[dict[str, int]], list[dict[str, int]]]:
    """Build a deterministic M1 offered-load trace and M3 absorption request.

    The trace continues with zero injections through every cohort's deadline
    window. M3 requests more absorption than the total offer, so service at the
    destination does not become an accidental bottleneck in these experiments.
    """
    _positive_int("load_ticks", load_ticks)
    _positive_int("injection_per_tick", injection_per_tick, minimum=0)
    _positive_int("deadline_ticks", deadline_ticks, minimum=0)
    trace_length = load_ticks + deadline_ticks
    total_offer = load_ticks * injection_per_tick
    injections = [
        {"M1": injection_per_tick} if tick < load_ticks else {}
        for tick in range(trace_length)
    ]
    absorptions = [{"M3": total_offer} for _ in range(trace_length)]
    return injections, absorptions


def _make_simulation(shortcut: bool, deadline_ticks: int) -> GraphSimulation:
    matrix = E_ON if shortcut else E_OFF
    return GraphSimulation(
        matrix,
        default_edge_settings(shortcut),
        destination="M3",
        deadline_ticks=deadline_ticks,
    )


def _serialize_edge_settings(settings: dict[tuple[str, str], EdgeSpec]) -> dict[str, dict[str, int | str]]:
    return {
        f"{source}->{destination}": {
            "capacity_per_tick": spec.capacity,
            "latency_ticks": spec.latency,
            "loss_rate": f"{spec.loss_numerator}/{spec.loss_denominator}",
        }
        for (source, destination), spec in sorted(settings.items())
    }


def run_paired_experiment(
    *, load_ticks: int = 20, injection_per_tick: int = 2, deadline_ticks: int = 5
) -> dict[str, Any]:
    """Run off/on configurations with the exact same deterministic traces."""
    injections, absorptions = make_traces(
        load_ticks=load_ticks,
        injection_per_tick=injection_per_tick,
        deadline_ticks=deadline_ticks,
    )
    off_settings = default_edge_settings(False)
    on_settings = default_edge_settings(True)
    off_budget = configuration_budgets(E_OFF, off_settings)
    on_budget = configuration_budgets(E_ON, on_settings)
    if (
        off_budget["total_capacity_per_tick"] != on_budget["total_capacity_per_tick"]
        or off_budget["capacity_weighted_nominal_loss_per_tick"]
        != on_budget["capacity_weighted_nominal_loss_per_tick"]
    ):
        raise AssertionError(f"comparison budgets are not matched: off={off_budget}, on={on_budget}")
    matched_budget = {
        "total_capacity_per_tick": off_budget["total_capacity_per_tick"],
        "capacity_weighted_nominal_loss_per_tick": off_budget["capacity_weighted_nominal_loss_per_tick"],
        "shortcut_off_directed_edges": off_budget["directed_edges"],
        "shortcut_on_directed_edges": on_budget["directed_edges"],
    }

    off = GraphSimulation(E_OFF, off_settings, deadline_ticks=deadline_ticks)
    on = GraphSimulation(E_ON, on_settings, deadline_ticks=deadline_ticks)
    off.run(injections, absorptions)
    on.run(injections, absorptions)
    return {
        "experiment": {
            "load_ticks": load_ticks,
            "injection_per_tick_at_M1": injection_per_tick,
            "deadline_ticks": deadline_ticks,
            "trace_ticks_including_deadline_window": len(injections),
            "identical_injection_trace": True,
            "identical_absorption_request_trace": True,
        },
        "matched_budgets": matched_budget,
        "shortcut_off": {
            "allowed_edge_matrix": [list(row) for row in E_OFF],
            "edge_settings": _serialize_edge_settings(off_settings),
            "summary": off.summary(),
            "ledger": off.rows,
        },
        "shortcut_on": {
            "allowed_edge_matrix": [list(row) for row in E_ON],
            "edge_settings": _serialize_edge_settings(on_settings),
            "summary": on.summary(),
            "ledger": on.rows,
        },
    }


def _load_relation(rate: int, capacity: int) -> str:
    return "below" if rate < capacity else "at" if rate == capacity else "above"


def run_stress_cases(
    *, load_ticks: int = 40, deadline_ticks: int = 5, rates: tuple[int, ...] = (1, 2, 3, 4)
) -> list[dict[str, Any]]:
    """Run paired offered-load sweeps spanning both topologies' cut capacities.

    Under the matched-budget settings, the M1-to-M3 nominal cut capacity is 2
    messages/tick with the shortcut off and 3 with it on. Rates 1..4 therefore
    include below, at, and above-capacity cases across the pair.
    """
    _positive_int("load_ticks", load_ticks)
    _positive_int("deadline_ticks", deadline_ticks, minimum=0)
    for rate in rates:
        _positive_int("stress rate", rate)
    off_capacity = 2
    on_capacity = 3
    results: list[dict[str, Any]] = []
    for rate in rates:
        pair = run_paired_experiment(
            load_ticks=load_ticks,
            injection_per_tick=rate,
            deadline_ticks=deadline_ticks,
        )
        results.append({
            "offered_rate_per_tick": rate,
            "shortcut_off_relation": _load_relation(rate, off_capacity),
            "shortcut_on_relation": _load_relation(rate, on_capacity),
            "shortcut_off": pair["shortcut_off"]["summary"],
            "shortcut_on": pair["shortcut_on"]["summary"],
        })
    return results


def budget_preview() -> dict[str, dict[str, int | str]]:
    """Return the declared directed capacity and nominal-loss budgets."""
    return {
        "shortcut_off": configuration_budgets(E_OFF, default_edge_settings(False)),
        "shortcut_on": configuration_budgets(E_ON, default_edge_settings(True)),
    }
