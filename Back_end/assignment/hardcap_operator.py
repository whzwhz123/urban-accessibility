from __future__ import annotations

from dataclasses import dataclass
import time

import numpy as np
import pandas as pd

from .core import PreparedModel, _route_segment_flows


@dataclass(frozen=True)
class HardCapacityConfig:
    max_feasibility_iterations: int = 3000
    max_cost_iterations: int = 3000
    capacity_tolerance: float = 1e-4
    optimality_tolerance: float = 1e-4
    check_interval: int = 25
    power_iterations: int = 20
    step_safety: float = 0.95


@dataclass
class HardCapacityResult:
    path_flow: np.ndarray
    unserved_by_od: np.ndarray
    route_segment_flow: np.ndarray
    capacity_multipliers: np.ndarray
    path_generalized_cost: np.ndarray
    log: pd.DataFrame
    summary: dict[str, float | int | str]


class RouteSectionCapacityOperator:
    """Implicit expected path-to-route-segment incidence operator."""

    def __init__(self, model: PreparedModel):
        self.model = model
        self.path_od = model.paths.od_idx.to_numpy(np.int32)
        if np.any(self.path_od[1:] < self.path_od[:-1]):
            raise ValueError("Paths must be ordered by od_idx")
        self.group_starts = np.r_[
            0, np.flatnonzero(self.path_od[1:] != self.path_od[:-1]) + 1
        ].astype(np.int64)
        self.group_counts = np.diff(
            np.r_[self.group_starts, len(self.path_od)]
        ).astype(np.int32)
        self.group_od = self.path_od[self.group_starts]
        demand = model.od.set_index("od_idx").demand_pax_per_hour
        self.demand = demand.reindex(self.group_od).to_numpy(float)
        if np.any(~np.isfinite(self.demand)):
            raise ValueError("Every path group must have an OD demand")

        self.max_group_size = int(self.group_counts.max())
        self.padded_row = np.repeat(
            np.arange(len(self.group_counts), dtype=np.int32), self.group_counts
        )
        self.padded_col = np.concatenate(
            [np.arange(count, dtype=np.int16) for count in self.group_counts]
        )
        self.capacity = model.route_segments.capacity_per_hour.to_numpy(float)
        if np.any(self.capacity <= 0.0):
            raise ValueError("All route-segment capacities must be positive")

    def forward(self, path_flow: np.ndarray) -> np.ndarray:
        segment_flow, _ = _route_segment_flows(self.model, path_flow)
        return np.asarray(segment_flow, dtype=float)

    def adjoint(self, segment_value: np.ndarray) -> np.ndarray:
        values = np.asarray(segment_value, dtype=float)
        if len(values) != len(self.capacity):
            raise ValueError("segment_value length does not match route segments")
        prefix = np.empty(len(values) + 1, dtype=float)
        prefix[0] = 0.0
        np.cumsum(values, out=prefix[1:])
        option_value = (
            prefix[self.model.option_after_segment]
            - prefix[self.model.option_first_segment]
        )
        bundle_value = np.bincount(
            self.model.option_bundle,
            weights=self.model.option_probability * option_value,
            minlength=len(self.model.bundle_keys),
        )
        return np.asarray(self.model.path_bundle @ bundle_value).ravel()

    def project_od_demand_balls(self, values: np.ndarray) -> np.ndarray:
        """Project each OD path vector onto nonnegative sum(flow)<=demand."""
        padded = np.zeros((len(self.group_counts), self.max_group_size), dtype=float)
        padded[self.padded_row, self.padded_col] = np.maximum(values, 0.0)
        row_sum = padded.sum(axis=1)
        active = row_sum > self.demand
        if np.any(active):
            current = padded[active]
            sorted_values = np.sort(current, axis=1)[:, ::-1]
            cumulative = np.cumsum(sorted_values, axis=1)
            positions = np.arange(1, self.max_group_size + 1, dtype=float)
            condition = sorted_values - (
                cumulative - self.demand[active, None]
            ) / positions > 0.0
            rho = np.maximum(condition.sum(axis=1) - 1, 0)
            theta = (
                cumulative[np.arange(len(current)), rho] - self.demand[active]
            ) / (rho + 1.0)
            padded[active] = np.maximum(current - theta[:, None], 0.0)
        return padded[self.padded_row, self.padded_col]

    def served_by_od(self, path_flow: np.ndarray) -> np.ndarray:
        return np.add.reduceat(path_flow, self.group_starts)

    def maximum_group_value(self, values: np.ndarray) -> np.ndarray:
        return np.maximum.reduceat(values, self.group_starts)

    def minimum_group_value(self, values: np.ndarray) -> np.ndarray:
        return np.minimum.reduceat(values, self.group_starts)

    def estimate_norm(self, iterations: int) -> float:
        rng = np.random.default_rng(20260829)
        vector = rng.standard_normal(len(self.path_od))
        vector /= max(np.linalg.norm(vector), 1e-12)
        eigenvalue = 0.0
        for _ in range(iterations):
            transformed = self.adjoint(self.forward(vector))
            norm = np.linalg.norm(transformed)
            if norm <= 1e-12:
                return 1.0
            vector = transformed / norm
            eigenvalue = float(np.dot(vector, self.adjoint(self.forward(vector))))
        return max(np.sqrt(max(eigenvalue, 0.0)), 1.0)

    def capacity_metrics(self, path_flow: np.ndarray) -> tuple[np.ndarray, float, float, int]:
        load = self.forward(path_flow)
        overload = np.maximum(load - self.capacity, 0.0)
        relative = overload / self.capacity
        return (
            load,
            float(relative.max(initial=0.0)),
            float(overload.max(initial=0.0)),
            int(np.count_nonzero(relative > 1e-12)),
        )


def _initial_all_or_nothing(model: PreparedModel, operator: RouteSectionCapacityOperator) -> np.ndarray:
    cost = model.paths.free_generalized_cost_min.to_numpy(float)
    minimum = np.minimum.reduceat(cost, operator.group_starts)
    chosen = np.isclose(cost, minimum[operator.padded_row], rtol=0.0, atol=1e-10)
    chosen_count = np.add.reduceat(chosen.astype(np.int32), operator.group_starts)
    flow = np.zeros(len(cost), dtype=float)
    flow[chosen] = (
        operator.demand[operator.padded_row[chosen]]
        / chosen_count[operator.padded_row[chosen]]
    )
    return flow


def _capacity_repair(
    operator: RouteSectionCapacityOperator, path_flow: np.ndarray
) -> tuple[np.ndarray, float]:
    load = operator.forward(path_flow)
    used = load > operator.capacity
    if not np.any(used):
        return path_flow.copy(), 1.0
    scale = float(np.min(operator.capacity[used] / load[used]))
    scale = min(max(scale, 0.0), 1.0)
    return path_flow * scale, scale


def _maximum_served_phase(
    operator: RouteSectionCapacityOperator,
    config: HardCapacityConfig,
    initial_flow: np.ndarray,
    operator_norm: float,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, float | int | str]], dict[str, float | int | str]]:
    step = config.step_safety / operator_norm
    objective_scale = max(float(np.mean(operator.demand)), 1.0)
    flow = operator.project_od_demand_balls(initial_flow)
    extrapolated = flow.copy()
    multipliers = np.zeros(len(operator.capacity), dtype=float)
    log: list[dict[str, float | int | str]] = []
    started = time.perf_counter()
    converged = False
    maxflow_gap = np.inf
    for iteration in range(1, config.max_feasibility_iterations + 1):
        multipliers = np.maximum(
            0.0,
            multipliers + step * (operator.forward(extrapolated) - operator.capacity),
        )
        previous = flow
        flow = operator.project_od_demand_balls(
            previous - step * (-objective_scale + operator.adjoint(multipliers))
        )
        extrapolated = 2.0 * flow - previous
        if iteration == 1 or iteration % config.check_interval == 0:
            _, violation, overload, overloaded = operator.capacity_metrics(flow)
            feasible_flow, repair_scale = _capacity_repair(operator, flow)
            feasible_served = float(feasible_flow.sum())
            dual_path = operator.adjoint(multipliers)
            od_dual = np.maximum(
                0.0,
                operator.maximum_group_value(objective_scale - dual_path),
            )
            dual_bound = float(
                np.dot(operator.capacity, multipliers)
                + np.dot(operator.demand, od_dual)
            ) / objective_scale
            maxflow_gap = max(
                0.0,
                (dual_bound - feasible_served) / max(dual_bound, 1.0),
            )
            log.append(
                {
                    "phase": "maximum_served",
                    "iteration": iteration,
                    "served_pax_per_hour": float(flow.sum()),
                    "feasible_served_lower_bound": feasible_served,
                    "maxflow_dual_upper_bound": dual_bound,
                    "relative_optimality_gap": maxflow_gap,
                    "maximum_relative_capacity_violation": violation,
                    "maximum_overload_pax_per_hour": overload,
                    "overloaded_route_segments": overloaded,
                    "capacity_repair_scale": repair_scale,
                    "objective_scale": objective_scale,
                    "elapsed_seconds": time.perf_counter() - started,
                }
            )
            print(
                f"phase=max_served iter={iteration} feasible_served={feasible_served:.3f} "
                f"upper={dual_bound:.3f} gap={maxflow_gap:.3e} "
                f"cap_violation={violation:.3e}",
                flush=True,
            )
            if violation <= config.capacity_tolerance and maxflow_gap <= config.optimality_tolerance:
                converged = True
                break
    repaired, repair_scale = _capacity_repair(operator, flow)
    return repaired, multipliers, log, {
        "phase1_converged": "yes" if converged else "no",
        "phase1_iterations": iteration,
        "phase1_feasible_served_lower_bound_pax_per_hour": float(repaired.sum()),
        "phase1_relative_optimality_gap": float(maxflow_gap),
        "phase1_capacity_repair_scale": repair_scale,
    }


def _minimum_cost_phase(
    operator: RouteSectionCapacityOperator,
    base_cost: np.ndarray,
    served_target: float,
    config: HardCapacityConfig,
    initial_flow: np.ndarray,
    initial_multipliers: np.ndarray,
    operator_norm: float,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, float | int | str]], dict[str, float | int | str]]:
    cost_scale = max(float(np.median(base_cost)), 1.0)
    normalized_cost = base_cost / cost_scale
    service_scale = np.sqrt(len(base_cost))
    combined_norm = np.sqrt(operator_norm**2 + 1.0)
    step = config.step_safety / combined_norm
    flow = initial_flow.copy()
    extrapolated = flow.copy()
    # Phase 1 duals price served volume; phase 2 duals price normalized minutes.
    multipliers = np.zeros_like(initial_multipliers)
    service_multiplier = 0.0
    log: list[dict[str, float | int | str]] = []
    started = time.perf_counter()
    converged = False
    cost_gap = np.inf
    for iteration in range(1, config.max_cost_iterations + 1):
        multipliers = np.maximum(
            0.0,
            multipliers + step * (operator.forward(extrapolated) - operator.capacity),
        )
        service_multiplier = max(
            0.0,
            service_multiplier
            + step * (served_target - float(extrapolated.sum())) / service_scale,
        )
        previous = flow
        gradient = (
            normalized_cost
            + operator.adjoint(multipliers)
            - service_multiplier / service_scale
        )
        flow = operator.project_od_demand_balls(previous - step * gradient)
        extrapolated = 2.0 * flow - previous
        if iteration == 1 or iteration % config.check_interval == 0:
            _, violation, overload, overloaded = operator.capacity_metrics(flow)
            served = float(flow.sum())
            shortfall = max(0.0, served_target - served) / max(served_target, 1.0)
            coefficient = (
                normalized_cost
                + operator.adjoint(multipliers)
                - service_multiplier / service_scale
            )
            group_minimum = operator.minimum_group_value(coefficient)
            dual_value = float(
                -np.dot(operator.capacity, multipliers)
                + service_multiplier * served_target / service_scale
                + np.dot(operator.demand, np.minimum(group_minimum, 0.0))
            )
            primal_value = float(np.dot(normalized_cost, flow))
            cost_gap = max(
                0.0,
                (primal_value - dual_value) / max(abs(primal_value), 1.0),
            )
            relative_change = float(
                np.linalg.norm(flow - previous) / max(np.linalg.norm(flow), 1.0)
            )
            log.append(
                {
                    "phase": "minimum_cost",
                    "iteration": iteration,
                    "served_pax_per_hour": served,
                    "served_target_pax_per_hour": served_target,
                    "relative_service_shortfall": shortfall,
                    "relative_optimality_gap": cost_gap,
                    "relative_flow_change": relative_change,
                    "maximum_relative_capacity_violation": violation,
                    "maximum_overload_pax_per_hour": overload,
                    "overloaded_route_segments": overloaded,
                    "elapsed_seconds": time.perf_counter() - started,
                }
            )
            print(
                f"phase=min_cost iter={iteration} served={served:.3f} "
                f"shortfall={shortfall:.3e} gap={cost_gap:.3e} "
                f"cap_violation={violation:.3e}",
                flush=True,
            )
            if (
                violation <= config.capacity_tolerance
                and shortfall <= config.optimality_tolerance
                and cost_gap <= config.optimality_tolerance
            ):
                converged = True
                break
    repaired, repair_scale = _capacity_repair(operator, flow)
    return repaired, multipliers * cost_scale, log, {
        "phase2_converged": "yes" if converged else "no",
        "phase2_iterations": iteration,
        "phase2_relative_optimality_gap": float(cost_gap),
        "phase2_capacity_repair_scale": repair_scale,
    }


def solve_hard_capacity(
    model: PreparedModel,
    config: HardCapacityConfig = HardCapacityConfig(),
    initial_flow: np.ndarray | None = None,
) -> HardCapacityResult:
    started = time.perf_counter()
    operator = RouteSectionCapacityOperator(model)
    if initial_flow is None:
        initial_flow = _initial_all_or_nothing(model, operator)
    else:
        initial_flow = np.asarray(initial_flow, dtype=float)
    operator_norm = operator.estimate_norm(config.power_iterations)

    feasible_flow, multipliers, phase1_log, phase1 = _maximum_served_phase(
        operator, config, initial_flow, operator_norm
    )
    served_target = float(feasible_flow.sum())
    base_cost = model.paths.free_generalized_cost_min.to_numpy(float)
    final_flow, multipliers, phase2_log, phase2 = _minimum_cost_phase(
        operator,
        base_cost,
        served_target,
        config,
        feasible_flow,
        multipliers,
        operator_norm,
    )
    segment_flow, violation, overload, overloaded = operator.capacity_metrics(final_flow)
    served_by_od = operator.served_by_od(final_flow)
    unserved = np.maximum(operator.demand - served_by_od, 0.0)
    path_cost = base_cost + operator.adjoint(multipliers)
    total_demand = float(operator.demand.sum())
    total_served = float(final_flow.sum())
    summary: dict[str, float | int | str] = {
        **phase1,
        **phase2,
        "hard_capacity_converged": (
            "yes"
            if phase1["phase1_converged"] == "yes" and phase2["phase2_converged"] == "yes"
            else "no"
        ),
        "operator_norm_estimate": operator_norm,
        "total_demand_pax_per_hour": total_demand,
        "served_demand_pax_per_hour": total_served,
        "unserved_demand_pax_per_hour": float(unserved.sum()),
        "served_demand_share": total_served / total_demand,
        "maximum_relative_capacity_violation": violation,
        "maximum_overload_pax_per_hour": overload,
        "overloaded_route_segments": overloaded,
        "maximum_route_segment_vc": float(
            np.max(segment_flow / operator.capacity, initial=0.0)
        ),
        "hard_capacity_feasible": "yes" if violation <= 1e-12 else "no",
        "solver_seconds": time.perf_counter() - started,
    }
    return HardCapacityResult(
        path_flow=final_flow,
        unserved_by_od=unserved,
        route_segment_flow=segment_flow,
        capacity_multipliers=multipliers,
        path_generalized_cost=path_cost,
        log=pd.DataFrame(phase1_log + phase2_log),
        summary=summary,
    )



