from __future__ import annotations

import numpy as np
import pandas as pd

from .operators import ActiveRouteSectionCapacityOperator
from ..task_store import check_cancel


def capacity_filter(model, desired_flow: np.ndarray, max_reallocation_rounds: int = 1000, *, cancel_check=None):
    check_cancel(cancel_check)
    operator = ActiveRouteSectionCapacityOperator(model)
    path_segment = operator.build_path_segment_matrix(max_expanded_entries=20_000_000)
    desired_segment_flow = np.asarray(path_segment.T @ desired_flow).ravel()
    segment_service_ratio = np.ones(len(operator.capacity), dtype=float)
    loaded = desired_segment_flow > 0.0
    segment_service_ratio[loaded] = np.minimum(
        1.0, operator.capacity[loaded] / desired_segment_flow[loaded]
    )
    counts = np.diff(path_segment.indptr)
    if np.any(counts == 0):
        raise RuntimeError("A candidate path has no active route-section incidence")
    path_service_ratio = np.minimum.reduceat(
        segment_service_ratio[path_segment.indices], path_segment.indptr[:-1]
    )
    served_flow = desired_flow * path_service_ratio
    initial_served = float(served_flow.sum())
    served_segment_flow = np.asarray(path_segment.T @ served_flow).ravel()
    residual_capacity = np.maximum(operator.capacity - served_segment_flow, 0.0)
    residual_demand = np.maximum(
        operator.demand - operator.served_by_od(served_flow), 0.0
    )
    path_cost = model.paths.equilibrium_path_cost_min.to_numpy(float)
    numerical_capacity_tol = 1e-10 * max(float(operator.capacity.max()), 1.0)
    numerical_demand_tol = 1e-10 * max(float(operator.demand.sum()), 1.0)
    reallocation_log: list[dict[str, float | int | str]] = []

    for round_number in range(1, max_reallocation_rounds + 1):
        check_cancel(cancel_check)
        segment_available = residual_capacity > numerical_capacity_tol
        feasible_count = np.add.reduceat(
            segment_available[path_segment.indices].astype(np.int16),
            path_segment.indptr[:-1],
        )
        path_feasible = feasible_count == counts
        masked_cost = np.where(path_feasible, path_cost, np.inf)
        minimum_cost = np.minimum.reduceat(masked_cost, operator.group_starts)
        active_group = (
            (residual_demand > numerical_demand_tol) & np.isfinite(minimum_cost)
        )
        if not np.any(active_group):
            stop_reason = "no_feasible_residual_path"
            break

        chosen = path_feasible & np.isclose(
            path_cost,
            minimum_cost[operator.padded_row],
            rtol=0.0,
            atol=1e-9,
        )
        chosen &= active_group[operator.padded_row]
        chosen_count = np.add.reduceat(
            chosen.astype(np.int16), operator.group_starts
        )
        proposal = np.zeros(len(desired_flow), dtype=float)
        proposal[chosen] = (
            residual_demand[operator.padded_row[chosen]]
            / chosen_count[operator.padded_row[chosen]]
        )
        proposed_segment_flow = np.asarray(path_segment.T @ proposal).ravel()
        segment_scale = np.ones(len(operator.capacity), dtype=float)
        proposed = proposed_segment_flow > 0.0
        segment_scale[proposed] = np.minimum(
            1.0,
            residual_capacity[proposed] / proposed_segment_flow[proposed],
        )
        proposal_path_scale = np.minimum.reduceat(
            segment_scale[path_segment.indices], path_segment.indptr[:-1]
        )
        added_flow = proposal * proposal_path_scale
        added_total = float(added_flow.sum())
        if added_total <= numerical_demand_tol:
            stop_reason = "numerical_no_progress"
            break

        added_segment_flow = np.asarray(path_segment.T @ added_flow).ravel()
        served_flow += added_flow
        served_segment_flow += added_segment_flow
        residual_capacity = np.maximum(
            operator.capacity - served_segment_flow, 0.0
        )
        residual_capacity[
            residual_capacity <= numerical_capacity_tol
        ] = 0.0
        residual_demand = np.maximum(
            operator.demand - operator.served_by_od(served_flow), 0.0
        )
        reallocation_log.append(
            {
                "round": round_number,
                "active_od": int(np.count_nonzero(active_group)),
                "chosen_paths": int(np.count_nonzero(chosen)),
                "added_served_pax_per_hour": added_total,
                "cumulative_served_pax_per_hour": float(served_flow.sum()),
                "remaining_demand_pax_per_hour": float(residual_demand.sum()),
                "saturated_route_segments": int(
                    np.count_nonzero(residual_capacity == 0.0)
                ),
            }
        )
        if float(residual_demand.sum()) <= numerical_demand_tol:
            stop_reason = "all_demand_served"
            break
    else:
        stop_reason = "maximum_reallocation_rounds"

    relative_violation = np.max(
        np.maximum(served_segment_flow - operator.capacity, 0.0)
        / operator.capacity,
        initial=0.0,
    )
    unserved_by_od = np.maximum(
        operator.demand - operator.served_by_od(served_flow), 0.0
    )
    final_path_service_ratio = np.divide(
        served_flow,
        desired_flow,
        out=np.zeros_like(served_flow),
        where=desired_flow > 0.0,
    )
    reallocation_summary = {
        "initial_bottleneck_served_pax_per_hour": initial_served,
        "reallocated_served_pax_per_hour": float(served_flow.sum()) - initial_served,
        "reallocation_rounds": len(reallocation_log),
        "reallocation_stop_reason": stop_reason,
    }
    return (
        operator, served_flow, unserved_by_od, final_path_service_ratio,
        desired_segment_flow, served_segment_flow, relative_violation,
        pd.DataFrame(reallocation_log), reallocation_summary,
    )
