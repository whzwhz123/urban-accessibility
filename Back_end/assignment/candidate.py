from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
from scipy.sparse import vstack

from .core import (
    PreparedModel,
    _bundle_costs,
    _generate_shortest_columns,
    _path_bundle_matrix,
)
from .operators import ActiveRouteSectionCapacityOperator
from ..task_store import check_cancel


def _active_bundle_costs(
    model: PreparedModel,
    operator: ActiveRouteSectionCapacityOperator,
    path_flow: np.ndarray,
) -> np.ndarray:
    """Return exact costs only for bundles used by the current path set."""
    segment_flow = operator.forward(path_flow)
    ratio = segment_flow / operator.capacity
    active_first_segment = operator.segment_map[operator.option_first_segment]
    if np.any(active_first_segment < 0):
        raise RuntimeError("An active option starts outside the active segment set")
    headway = model.route_segments.headway_min.to_numpy(float)
    option_extra = (
        headway[operator.option_first_segment]
        * np.maximum(ratio[active_first_segment], 0.0)
        ** model.config.crowding_exponent
    )
    bundle_extra = np.bincount(
        operator.option_bundle,
        weights=operator.option_probability * option_extra,
        minlength=len(operator.active_bundle_ids),
    )
    return (
        model.bundle_base_wait[operator.active_bundle_ids]
        + model.bundle_expected_ride[operator.active_bundle_ids]
        + bundle_extra
    )


def assign_msa_active(
    model: PreparedModel,
    initial_flow: np.ndarray | None = None,
    column_round: int = 1,
    *, cancel_check=None, checkpoint=None, resume_state=None, checkpoint_interval=10,
) -> tuple[np.ndarray, pd.DataFrame, dict[str, float | int | str]]:
    """MSA equivalent to the baseline, without evaluating inactive bundles."""
    if checkpoint_interval < 1:
        raise ValueError('checkpoint_interval must be positive')
    cfg = model.config
    paths = model.paths
    operator = ActiveRouteSectionCapacityOperator(model)
    path_od = paths.od_idx.to_numpy(np.int32)
    group_starts = operator.group_starts
    path_group = operator.padded_row
    demand = operator.demand
    connector = paths.connector_cost_min.to_numpy(float)

    def costs_and_aon(
        path_flow: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, float, float]:
        active_cost = _active_bundle_costs(model, operator, path_flow)
        path_cost = connector + np.asarray(
            operator.active_path_bundle @ active_cost
        ).ravel()
        aon = np.zeros(len(paths), dtype=float)
        minimum = np.minimum.reduceat(path_cost, group_starts)
        chosen = np.isclose(path_cost, minimum[path_group], rtol=0.0, atol=1e-9)
        chosen_count = np.add.reduceat(chosen.astype(np.int32), group_starts)
        aon[chosen] = demand[path_group[chosen]] / chosen_count[path_group[chosen]]
        shortest_total = float(np.dot(demand, minimum))
        current_total = float(np.dot(path_flow, path_cost))
        gap = max(
            0.0,
            (current_total - shortest_total) / max(current_total, 1e-12),
        )
        return path_cost, aon, gap, current_total

    if initial_flow is None:
        _, path_flow, _, _ = costs_and_aon(np.zeros(len(paths), dtype=float))
    else:
        path_flow = np.asarray(initial_flow, dtype=float).copy()
        if len(path_flow) != len(paths):
            raise ValueError("initial_flow length does not match the expanded path set")
    state = resume_state or {}
    if state:
        path_flow = state['path_flow'].copy()
    logs = list(state.get('logs', []))
    started = time.perf_counter()
    converged = state.get('converged', False)
    elapsed = state.get('elapsed', 0.0)
    for iteration in range(state.get('next_iteration', 1), cfg.max_iterations + 1):
        check_cancel(cancel_check)
        path_cost, aon, gap, total = costs_and_aon(path_flow)
        logs.append(
            {
                "column_round": column_round,
                "iteration": iteration,
                "relative_gap": gap,
                "total_generalized_cost": total,
            }
        )
        if gap <= cfg.relative_gap_tol:
            converged = True
        else:
            step = 1.0 / (iteration + 1.0)
            path_flow += step * (aon - path_flow)
        if checkpoint and (converged or iteration % checkpoint_interval == 0 or iteration == cfg.max_iterations):
            checkpoint(dict(path_flow=path_flow.copy(), logs=list(logs),
                next_iteration=cfg.max_iterations + 1 if converged else iteration + 1,
                converged=converged, elapsed=elapsed + time.perf_counter() - started))
        if converged:
            break
    path_cost, _, gap, total = costs_and_aon(path_flow)
    model.paths["equilibrium_path_flow"] = path_flow
    model.paths["equilibrium_path_cost_min"] = path_cost
    assigned_by_od = np.add.reduceat(path_flow, group_starts)
    summary: dict[str, float | int | str] = {
        "converged": "yes" if converged else "no",
        "iterations": len(logs),
        "relative_gap": gap,
        "assignment_seconds": elapsed + time.perf_counter() - started,
        "max_od_conservation_error": float(
            np.max(np.abs(assigned_by_od - demand))
        ),
        "total_generalized_cost": total,
        "active_bundles": len(operator.active_bundle_ids),
        "active_options": len(operator.option_bundle),
        "active_route_segments": len(operator.active_segment_ids),
    }
    return path_flow, pd.DataFrame(logs), summary


def assign_msa_kpaths_optimized(
    model: PreparedModel,
    *, cancel_check=None, checkpoint=None, resume_state=None, checkpoint_interval=10,
) -> tuple[np.ndarray, pd.DataFrame, dict[str, float | int | str]]:
    """Preserve baseline K-path column generation while accelerating its MSA."""
    if model.config.paths_per_transfer < 1:
        raise ValueError("paths_per_transfer must be at least 1")
    path_flow: np.ndarray | None = None
    all_logs: list[pd.DataFrame] = []
    generation_rows: list[dict[str, int | float]] = []
    final_summary: dict[str, float | int | str] = {}
    total_assignment_seconds = 0.0
    total_generation_seconds = 0.0
    iteration_counts: list[int] = []
    active_stats: list[dict[str, int]] = []

    state = resume_state or {}
    if state:
        path_flow = state['path_flow']
        all_logs = state['all_logs']
        generation_rows = state['generation_rows']
        final_summary = state['final_summary']
        total_assignment_seconds = state['total_assignment_seconds']
        total_generation_seconds = state['total_generation_seconds']
        iteration_counts = state['iteration_counts']
        active_stats = state['active_stats']

    def save(next_round, msa=None):
        if checkpoint:
            checkpoint(dict(model=model, column_round=next_round, msa=msa,
                path_flow=path_flow, all_logs=all_logs, generation_rows=generation_rows,
                final_summary=final_summary, total_assignment_seconds=total_assignment_seconds,
                total_generation_seconds=total_generation_seconds,
                iteration_counts=iteration_counts, active_stats=active_stats))

    for column_round in range(state.get('column_round', 1), model.config.paths_per_transfer + 1):
        save(column_round, state.get('msa'))
        check_cancel(cancel_check)
        path_flow, logs, final_summary = assign_msa_active(
            model,
            initial_flow=path_flow,
            column_round=column_round, cancel_check=cancel_check,
            checkpoint=(lambda msa: save(column_round, msa)) if checkpoint else None,
            resume_state=state.get('msa'), checkpoint_interval=checkpoint_interval,
        )
        state = {}
        all_logs.append(logs)
        total_assignment_seconds += float(final_summary["assignment_seconds"])
        iteration_counts.append(len(logs))
        active_stats.append(
            {
                "column_round": column_round,
                "active_bundles": int(final_summary["active_bundles"]),
                "active_options": int(final_summary["active_options"]),
                "active_route_segments": int(final_summary["active_route_segments"]),
            }
        )
        if column_round >= model.config.paths_per_transfer:
            break

        # Pricing needs costs for every network bundle, so evaluate the full
        # network once per column round rather than once per MSA iteration.
        check_cancel(cancel_check)
        bundle_cost = _bundle_costs(model, path_flow)
        generation_started = time.perf_counter()
        new_paths, generation = _generate_shortest_columns(model, bundle_cost)
        generation_seconds = time.perf_counter() - generation_started
        total_generation_seconds += generation_seconds
        generation["generation_seconds"] = generation_seconds
        generation_rows.append({"column_round": column_round + 1, **generation})
        if len(new_paths) == 0:
            break

        new_matrix = _path_bundle_matrix(new_paths, len(model.bundle_keys))
        new_paths = new_paths.drop(columns="bundle_ids")
        combined_paths = pd.concat(
            [model.paths, new_paths], ignore_index=True, copy=False
        )
        combined_matrix = vstack([model.path_bundle, new_matrix], format="csr")
        combined_flow = np.concatenate(
            [path_flow, np.zeros(len(new_paths), dtype=float)]
        )
        order = np.lexsort(
            (
                combined_paths.path_rank_within_transfer.to_numpy(np.int8),
                combined_paths.transfers.to_numpy(np.int8),
                combined_paths.od_idx.to_numpy(np.int32),
            )
        )
        model.paths = combined_paths.iloc[order].reset_index(drop=True)
        model.paths["path_idx"] = np.arange(len(model.paths), dtype=np.int32)
        model.path_bundle = combined_matrix[order]
        path_flow = combined_flow[order]
        model.summary["candidate_paths"] = len(model.paths)

    if path_flow is None:
        raise RuntimeError("No assignment round was executed")
    final_summary["column_generation_rounds"] = len(all_logs)
    final_summary["iterations"] = int(sum(iteration_counts))
    final_summary["iterations_by_round"] = json.dumps(iteration_counts)
    final_summary["assignment_seconds"] = total_assignment_seconds
    final_summary["column_generation_seconds"] = total_generation_seconds
    final_summary["total_solver_seconds"] = (
        total_assignment_seconds + total_generation_seconds
    )
    final_summary["paths_per_transfer_limit"] = model.config.paths_per_transfer
    final_summary["generated_additional_paths"] = len(model.paths) - int(
        model.summary.get("initial_candidate_paths", model.summary["candidate_paths"])
    )
    final_summary["column_generation_detail"] = json.dumps(
        generation_rows, ensure_ascii=False
    )
    final_summary["active_operator_by_round"] = json.dumps(active_stats)
    return path_flow, pd.concat(all_logs, ignore_index=True), final_summary
