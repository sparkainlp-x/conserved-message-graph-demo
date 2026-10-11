"""Deterministic three-node graph-routing simulation.

This is a graph-accounting analogy only. It does not model physical membranes,
general relativity, black holes, wormholes, or OES32 behavior.
"""
from __future__ import annotations

import heapq
from collections import deque
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Mapping, Sequence

NODES = ("M1", "M2", "M3")
NODE_INDEX = {node: index for index, node in enumerate(NODES)}
E_OFF = ((0, 1, 0), (1, 0, 1), (0, 1, 0))
E_ON = ((0, 1, 1), (1, 0, 1), (1, 1, 0))
EdgeKey = tuple[str, str]


def _integer(name: str, value: object, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _validate_matrix(matrix: Sequence[Sequence[int]]) -> tuple[tuple[int, ...], ...]:
    try:
        normalized = tuple(tuple(row) for row in matrix)
    except TypeError as exc:
        raise ValueError("allowed-edge matrix must be a 3x3 sequence") from exc
    if len(normalized) != 3 or any(len(row) != 3 for row in normalized):
        raise ValueError("allowed-edge matrix must be 3x3")
    for i in range(3):
        for j in range(3):
            value = normalized[i][j]
            if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1):
                raise ValueError("allowed-edge matrix entries must be 0 or 1")
            if i == j and value != 0:
                raise ValueError("self-edges are not allowed")
            if value != normalized[j][i]:
                raise ValueError("this comparison requires a symmetric allowed-edge matrix")
    return normalized


@dataclass(frozen=True, slots=True)
class EdgeSpec:
    """Settings for one directed edge; capacity is attempted messages per tick."""

    capacity: int
    latency: int
    loss_numerator: int = 0
    loss_denominator: int = 1

    def __post_init__(self) -> None:
        _integer("capacity", self.capacity)
        _integer("latency", self.latency, minimum=1)
        _integer("loss numerator", self.loss_numerator)
        _integer("loss denominator", self.loss_denominator, minimum=1)
        if self.loss_numerator > self.loss_denominator:
            raise ValueError("loss rate must be between 0 and 1 inclusive")

    @property
    def loss_rate(self) -> Fraction:
        return Fraction(self.loss_numerator, self.loss_denominator)


@dataclass(slots=True)
class _ArcRuntime:
    spec: EdgeSpec
    attempted_total: int = 0

    def losses_for(self, attempts: int) -> int:
        before = (self.attempted_total * self.spec.loss_numerator) // self.spec.loss_denominator
        self.attempted_total += attempts
        after = (self.attempted_total * self.spec.loss_numerator) // self.spec.loss_denominator
        return after - before


@dataclass(slots=True)
class _Cohort:
    cohort_id: int
    source: str
    injection_tick: int
    injected: int
    lost: int = 0
    absorbed_at_target: int = 0
    absorbed_elsewhere: int = 0
    on_time: int = 0
    latency_amounts: list[tuple[int, int]] = field(default_factory=list)


@dataclass(slots=True)
class _Batch:
    cohort_id: int
    injection_tick: int
    target: str
    amount: int
    visited: frozenset[str]


class GraphSimulation:
    """Run a deterministic, discrete-time simulation on M1, M2, and M3.

    All inventory—including messages in delayed transfers—remains assigned to
    the sending node until arrival. Departures use only batches ready at the
    start of a tick; due arrivals are then transferred, requested external
    absorptions are applied, and new injections are admitted last. Thus arrivals
    and injections cannot be re-routed in the same tick they enter a node.

    ``absorption_requests`` are per-node external absorption requests B_i[t].
    Actual absorption is limited to eligible inventory at that node. Invalid
    input mappings are rejected before any simulator state is mutated.
    """

    def __init__(
        self,
        allowed_edges: Sequence[Sequence[int]],
        edge_settings: Mapping[EdgeKey, EdgeSpec],
        *,
        destination: str = "M3",
        deadline_ticks: int = 5,
    ) -> None:
        self.matrix = _validate_matrix(allowed_edges)
        if destination not in NODES:
            raise ValueError(f"destination must be one of {NODES}")
        self.deadline_ticks = _integer("deadline_ticks", deadline_ticks)
        expected = {
            (NODES[i], NODES[j])
            for i in range(3)
            for j in range(3)
            if self.matrix[i][j] == 1
        }
        if not isinstance(edge_settings, Mapping):
            raise ValueError("edge_settings must map directed node pairs to EdgeSpec")
        if set(edge_settings) != expected:
            missing = sorted(expected - set(edge_settings))
            extra = sorted(set(edge_settings) - expected)
            raise ValueError(f"edge settings must match allowed directions; missing={missing}, extra={extra}")
        for key, spec in edge_settings.items():
            if not isinstance(key, tuple) or len(key) != 2 or key[0] not in NODES or key[1] not in NODES:
                raise ValueError("edge keys must be (source, destination) pairs from M1/M2/M3")
            if not isinstance(spec, EdgeSpec):
                raise ValueError("every directed edge setting must be an EdgeSpec")

        self.destination = destination
        self.arcs = {key: _ArcRuntime(spec) for key, spec in edge_settings.items()}
        self.inventory = {node: 0 for node in NODES}
        self._ready = {node: deque() for node in NODES}
        # (due_tick, insertion_sequence, source, destination, batch)
        self._pending: list[tuple[int, int, str, str, _Batch]] = []
        self._sequence = 0
        self._cohort_sequence = 0
        self._cohorts: dict[int, _Cohort] = {}
        self.tick = 0
        self.rows: list[dict[str, object]] = []

    @staticmethod
    def _normalize_counts(name: str, values: Mapping[str, int] | None) -> dict[str, int]:
        if values is None:
            values = {}
        if not isinstance(values, Mapping):
            raise ValueError(f"{name} must be a mapping from node names to integer counts")
        unknown = set(values) - set(NODES)
        if unknown:
            raise ValueError(f"{name} contains unknown nodes: {sorted(unknown)}")
        normalized = {node: 0 for node in NODES}
        for node, raw_value in values.items():
            normalized[node] = _integer(f"{name}[{node}]", raw_value)
        return normalized

    def _candidate_paths(
        self,
        origin: str,
        visited: frozenset[str],
        capacity_used: Mapping[EdgeKey, int],
    ) -> list[tuple[int, int, tuple[str, ...]]]:
        paths: list[tuple[int, int, tuple[str, ...]]] = []

        def walk(node: str, path: tuple[str, ...], total_latency: int) -> None:
            if node == self.destination:
                paths.append((total_latency, len(path) - 1, path))
                return
            for next_node in NODES:
                key = (node, next_node)
                if key not in self.arcs or next_node in visited:
                    continue
                walk(next_node, path + (next_node,), total_latency + self.arcs[key].spec.latency)

        walk(origin, (origin,), 0)
        viable = [
            item for item in paths
            if self.arcs[(origin, item[2][1])].spec.capacity
            - capacity_used.get((origin, item[2][1]), 0) > 0
        ]
        return sorted(viable, key=lambda item: (item[0], item[1], item[2]))

    def _route_departures(self, capacity_used: dict[EdgeKey, int]) -> int:
        losses = 0
        for origin in NODES:
            if origin == self.destination:
                continue
            queue = self._ready[origin]
            original_batches = len(queue)
            for _ in range(original_batches):
                batch = queue.popleft()
                while batch.amount > 0:
                    paths = self._candidate_paths(origin, batch.visited, capacity_used)
                    if not paths:
                        break
                    path = paths[0][2]
                    next_node = path[1]
                    key = (origin, next_node)
                    arc = self.arcs[key]
                    room = arc.spec.capacity - capacity_used.get(key, 0)
                    sent = min(batch.amount, room)
                    if sent <= 0:
                        break
                    batch.amount -= sent
                    capacity_used[key] = capacity_used.get(key, 0) + sent
                    batch_losses = arc.losses_for(sent)
                    losses += batch_losses
                    cohort = self._cohorts[batch.cohort_id]
                    cohort.lost += batch_losses
                    self.inventory[origin] -= batch_losses
                    survivors = sent - batch_losses
                    if survivors:
                        moving = _Batch(
                            cohort_id=batch.cohort_id,
                            injection_tick=batch.injection_tick,
                            target=batch.target,
                            amount=survivors,
                            visited=batch.visited | {next_node},
                        )
                        due_tick = self.tick + arc.spec.latency - 1
                        heapq.heappush(
                            self._pending,
                            (due_tick, self._sequence, origin, next_node, moving),
                        )
                        self._sequence += 1
                if batch.amount:
                    queue.append(batch)
        return losses

    def _deliver_due(self) -> None:
        while self._pending and self._pending[0][0] <= self.tick:
            _, _, origin, destination, batch = heapq.heappop(self._pending)
            self.inventory[origin] -= batch.amount
            self.inventory[destination] += batch.amount
            self._ready[destination].append(batch)

    def _apply_absorptions(self, requests: Mapping[str, int]) -> tuple[dict[str, int], int]:
        absorbed_by_node = {node: 0 for node in NODES}
        total = 0
        for node in NODES:
            remaining = requests[node]
            queue = self._ready[node]
            original_batches = len(queue)
            for _ in range(original_batches):
                batch = queue.popleft()
                absorbed = min(remaining, batch.amount)
                if absorbed:
                    batch.amount -= absorbed
                    remaining -= absorbed
                    absorbed_by_node[node] += absorbed
                    total += absorbed
                    self.inventory[node] -= absorbed
                    cohort = self._cohorts[batch.cohort_id]
                    if node == batch.target:
                        cohort.absorbed_at_target += absorbed
                        latency = self.tick - batch.injection_tick
                        cohort.latency_amounts.append((latency, absorbed))
                        if latency <= self.deadline_ticks:
                            cohort.on_time += absorbed
                    else:
                        cohort.absorbed_elsewhere += absorbed
                if batch.amount:
                    queue.append(batch)
            # Batches after the requested amount remain queued in original order.
        return absorbed_by_node, total

    def _assert_state(self, start_total: int, injections: int, absorptions: int, losses: int) -> int:
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in self.inventory.values()):
            raise AssertionError("node inventory must remain non-negative integers")
        represented = {node: sum(batch.amount for batch in self._ready[node]) for node in NODES}
        for _, _, origin, _, batch in self._pending:
            represented[origin] += batch.amount
        if represented != self.inventory:
            raise AssertionError(f"inventory/queue mismatch: inventory={self.inventory}, represented={represented}")
        end_total = sum(self.inventory.values())
        residual = (end_total - start_total) - (injections - absorptions - losses)
        if residual != 0:
            raise AssertionError(f"conservation error at tick {self.tick}: residual={residual}")
        return residual

    def step(
        self,
        injections: Mapping[str, int] | None = None,
        absorption_requests: Mapping[str, int] | None = None,
    ) -> dict[str, object]:
        """Advance one tick; invalid requests leave all state unchanged."""
        # Full preflight before any mutation.
        injection_i = self._normalize_counts("injections", injections)
        absorption_i = self._normalize_counts("absorption_requests", absorption_requests)
        start_total = sum(self.inventory.values())
        capacity_used: dict[EdgeKey, int] = {}

        loss_total = self._route_departures(capacity_used)
        self._deliver_due()
        absorbed_i, absorbed_total = self._apply_absorptions(absorption_i)

        # New external messages are admitted at tick end and become routeable
        # on the next tick. Each positive node injection forms one cohort.
        for node in NODES:
            amount = injection_i[node]
            if amount == 0:
                continue
            cohort_id = self._cohort_sequence
            self._cohort_sequence += 1
            self._cohorts[cohort_id] = _Cohort(
                cohort_id=cohort_id,
                source=node,
                injection_tick=self.tick,
                injected=amount,
            )
            self.inventory[node] += amount
            self._ready[node].append(
                _Batch(
                    cohort_id=cohort_id,
                    injection_tick=self.tick,
                    target=self.destination,
                    amount=amount,
                    visited=frozenset({node}),
                )
            )

        injection_total = sum(injection_i.values())
        residual = self._assert_state(start_total, injection_total, absorbed_total, loss_total)
        pending_by_edge = {f"{src}->{dst}": 0 for src, dst in self.arcs}
        for _, _, src, dst, batch in self._pending:
            pending_by_edge[f"{src}->{dst}"] += batch.amount
        ready_by_node = {node: sum(batch.amount for batch in self._ready[node]) for node in NODES}
        row: dict[str, object] = {
            "tick": self.tick,
            "nodes": dict(self.inventory),
            "ready": ready_by_node,
            "pending_by_edge": pending_by_edge,
            "M_start": start_total,
            "M_end": sum(self.inventory.values()),
            "I_i": injection_i,
            "B_i": absorbed_i,
            "B_requested_i": absorption_i,
            "I": injection_total,
            "B": absorbed_total,
            "L": loss_total,
            "capacity_used": {f"{src}->{dst}": amount for (src, dst), amount in capacity_used.items()},
            "residual": residual,
        }
        self.rows.append(row)
        self.tick += 1
        return row

    def run(
        self,
        injection_trace: Sequence[Mapping[str, int]],
        absorption_trace: Sequence[Mapping[str, int]] | None = None,
    ) -> list[dict[str, object]]:
        """Run paired traces. Missing absorption trace means zero absorption."""
        if not isinstance(injection_trace, Sequence) or isinstance(injection_trace, (str, bytes)):
            raise ValueError("injection_trace must be a sequence of per-tick mappings")
        if absorption_trace is None:
            absorption_trace = [{} for _ in injection_trace]
        if not isinstance(absorption_trace, Sequence) or isinstance(absorption_trace, (str, bytes)):
            raise ValueError("absorption_trace must be a sequence of per-tick mappings")
        if len(injection_trace) != len(absorption_trace):
            raise ValueError("injection_trace and absorption_trace must have equal lengths")
        # Preflight the entire trace to avoid partial runs on a malformed later tick.
        normalized_i = [self._normalize_counts("injections", row) for row in injection_trace]
        normalized_b = [self._normalize_counts("absorption_requests", row) for row in absorption_trace]
        return [self.step(i, b) for i, b in zip(normalized_i, normalized_b)]

    def summary(self) -> dict[str, int | float | None]:
        """Return delivery, conditional latency, losses, and horizon queue metrics."""
        total_injected = sum(cohort.injected for cohort in self._cohorts.values())
        target_delivered = sum(cohort.absorbed_at_target for cohort in self._cohorts.values())
        delivered_by_deadline = sum(cohort.on_time for cohort in self._cohorts.values())
        total_losses = sum(cohort.lost for cohort in self._cohorts.values())
        absorbed_elsewhere = sum(cohort.absorbed_elsewhere for cohort in self._cohorts.values())
        undelivered = max(0, total_injected - target_delivered - total_losses - absorbed_elsewhere)
        latency_weight = sum(amount for cohort in self._cohorts.values() for _, amount in cohort.latency_amounts)
        latency_sum = sum(latency * amount for cohort in self._cohorts.values() for latency, amount in cohort.latency_amounts)
        return {
            "ticks": len(self.rows),
            "deadline_ticks": self.deadline_ticks,
            "injected": total_injected,
            "delivered_to_destination": target_delivered,
            "delivered_by_deadline": delivered_by_deadline,
            "delivery_by_deadline_fraction": delivered_by_deadline / total_injected if total_injected else None,
            "delivery_by_deadline_percent": 100.0 * delivered_by_deadline / total_injected if total_injected else None,
            "mean_latency_ticks_given_delivered": latency_sum / latency_weight if latency_weight else None,
            "median_latency_ticks_given_delivered": self._weighted_median_latency(),
            "losses": total_losses,
            "absorbed_elsewhere": absorbed_elsewhere,
            "undelivered_at_horizon": undelivered,
            "final_backlog": sum(self.inventory.values()),
            "peak_backlog": max((int(row["M_end"]) for row in self.rows), default=0),
            "final_in_flight": sum(batch.amount for _, _, _, _, batch in self._pending),
            "final_ready": sum(sum(batch.amount for batch in queue) for queue in self._ready.values()),
            "max_conservation_residual": max((abs(int(row["residual"])) for row in self.rows), default=0),
        }

    def _weighted_median_latency(self) -> int | None:
        values = sorted(
            (latency, amount)
            for cohort in self._cohorts.values()
            for latency, amount in cohort.latency_amounts
        )
        total = sum(amount for _, amount in values)
        if total == 0:
            return None
        threshold = (total + 1) // 2
        cumulative = 0
        for latency, amount in values:
            cumulative += amount
            if cumulative >= threshold:
                return latency
        raise AssertionError("weighted median calculation failed")


def default_edge_settings(shortcut: bool) -> dict[EdgeKey, EdgeSpec]:
    """Return matched-budget configurations used by the bundled comparison.

    Off: four directed backbone arcs, capacity 2 each (total 8).
    On: backbone arcs capacity 1 each plus two shortcut arcs capacity 2 each
    (total 8). Every directed arc uses the same 5% deterministic loss quota;
    therefore the capacity-weighted nominal loss budget is 8 * 1/20 = 0.4
    messages per tick in either configuration. Each direction is configured
    independently in this returned mapping.
    """
    matrix = E_ON if shortcut else E_OFF
    result: dict[EdgeKey, EdgeSpec] = {}
    for i, source in enumerate(NODES):
        for j, destination in enumerate(NODES):
            if matrix[i][j] == 0:
                continue
            is_shortcut = {source, destination} == {"M1", "M3"}
            capacity = 2 if (not shortcut or is_shortcut) else 1
            result[(source, destination)] = EdgeSpec(
                capacity=capacity,
                latency=1,
                loss_numerator=1,
                loss_denominator=20,
            )
    return result


def configuration_budgets(
    allowed_edges: Sequence[Sequence[int]], edge_settings: Mapping[EdgeKey, EdgeSpec]
) -> dict[str, int | str]:
    matrix = _validate_matrix(allowed_edges)
    expected = {
        (NODES[i], NODES[j])
        for i in range(3)
        for j in range(3)
        if matrix[i][j]
    }
    if set(edge_settings) != expected:
        raise ValueError("edge settings must exactly match allowed directed edges")
    total_capacity = sum(spec.capacity for spec in edge_settings.values())
    nominal_loss = sum((spec.capacity * spec.loss_rate for spec in edge_settings.values()), Fraction(0, 1))
    return {
        "directed_edges": len(edge_settings),
        "total_capacity_per_tick": total_capacity,
        "capacity_weighted_nominal_loss_per_tick": str(nominal_loss),
    }
