# Three-Node Graph Routing Simulation

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23308959.svg)](https://doi.org/10.5281/zenodo.23308959)

A small, dependency-free Python simulation for comparing a three-node graph with and without a hypothetical bidirectional shortcut.

> This is a **graph-routing analogy only**. “White-hole-like” means external injection, “black-hole-like” means external absorption, and “wormhole-like” refers only to the optional M1↔M3 graph edge. The code does not model general relativity, physical membranes, spacetime, physical transport, or OES32 behavior.

## Model

The nodes are ordinary software nodes `M1`, `M2`, and `M3`. The binary matrix `E` says which directed edges are allowed; it is **not** a transition-probability matrix. Per-directed-edge capacity, latency, and deterministic loss rate are separate settings.

**Shortcut off** uses:

```text
E_off = [[0, 1, 0],
         [1, 0, 1],
         [0, 1, 0]]
```

**Shortcut on** adds the bidirectional M1↔M3 link:

```text
E_on  = [[0, 1, 1],
         [1, 0, 1],
         [1, 1, 0]]
```

External injections are `I_i[t]`; external absorption requests are `B_i[t]`. An absorption request removes up to that many ready messages from a node; actual accepted absorption is reported in the ledger. The included comparison injects at M1 and absorbs at M3, treating delivery at M3 as the benchmark outcome.

Messages waiting for capacity or latency remain in their sender's node inventory. In-flight queues are tracked explicitly. Departures use only messages ready at the beginning of a tick; due arrivals are then transferred, absorptions are applied, and new injections are admitted last. Consequently, newly injected or arrived messages do not make another edge departure in the same tick.

Every tick asserts the conserved-message ledger:

```text
ΔM = ΣI_i − ΣB_i − L
```

Internal transfers cancel. Invalid input mappings are rejected before simulator state changes. Node inventories are nonnegative integers, and edge capacities apply per directed edge and tick.

## Matched-budget comparison

The default experiment compares identical deterministic injection and absorption traces.

| Configuration | Directed-edge capacities | Total attempted capacity/tick |
|---|---|---:|
| Shortcut off | Four backbone directions × 2 | 8 |
| Shortcut on | Four backbone directions × 1; two shortcut directions × 2 | 8 |

Each direction has latency 1 and a deterministic cumulative loss quota of 1/20. Thus the capacity-weighted nominal loss budget is 8 × 1/20 = **0.4 messages/tick** in both configurations. Actual realized losses can differ because route use and integer quota rounding differ; the simulator reports observed losses separately. The matched-budget settings intentionally reallocate capacity to the shortcut rather than giving the shortcut-on graph extra aggregate capacity.

With these settings, the nominal M1-to-M3 cut capacities are 2 messages/tick with the shortcut off and 3 with it on. The stress sweep uses offered rates 1–4 so it covers below, at, and above capacity across the paired configurations.

## Reported metrics

- **Delivery by deadline:** count and fraction of all injected messages absorbed at M3 by the configured deadline.
- **Conditional latency:** mean and median ticks for messages that actually reached M3; it is shown alongside undelivered and backlog counts to avoid hiding non-arrivals.
- **Undelivered at horizon:** offered messages not delivered to M3, lost, or absorbed elsewhere by the last simulated tick.
- **Final and peak backlog:** all current inventory, including messages in flight but still assigned to their sending node.
- **Losses:** deterministic edge losses; per-tick and per-edge capacities are also visible in the ledger.
- **Per-tick conservation residual:** asserted to be zero on every tick.

The deadline observation window is included after the load stops. If a message has not been absorbed at M3 by its deadline, it is not counted as delivered-by-deadline even if it arrives later.

The JSON report includes the complete settings for every directed edge in each configuration. This project is licensed under the **GNU Affero General Public License v3.0 only (AGPL-3.0-only)**; see [LICENSE](LICENSE).

## Run

Requires Python 3.10+; no third-party packages are needed.

```bash
python3 simulate.py
python3 simulate.py --stress
python3 simulate.py --load-ticks 40 --rate 3 --deadline 5 --stress
python3 simulate.py --json > results/comparison.json
python3 -m unittest discover -s tests -v
```

The `--json` option prints the full per-tick ledgers as well as summaries. Importable APIs are available from `membrane_sim`, including `GraphSimulation`, `run_paired_experiment`, and `run_stress_cases`.

## Scope and limitations

This is synthetic discrete-event code for an illustrative graph-routing comparison. Routing uses the lowest total-latency available simple path, with deterministic tie-breaking and per-tick first-edge capacity. A batch can split across routes if an edge’s capacity is exhausted. Loss is deterministic bookkeeping using a cumulative rational quota, not random noise or a physical loss law. No result is a claim about physical membranes, black holes, wormholes, OES32, or real-world network performance.

## Cite

Concept DOI (all versions): [10.5281/zenodo.23308959](https://doi.org/10.5281/zenodo.23308959). v0.1.0: [10.5281/zenodo.23308960](https://doi.org/10.5281/zenodo.23308960). See [CITATION.cff](CITATION.cff).
