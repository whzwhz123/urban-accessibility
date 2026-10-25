from __future__ import annotations

from pathlib import Path
import time

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from scipy.sparse.csgraph import dijkstra

from .core import (
    PERIOD_WEIGHTS,
    poi_marginals,
    ExperimentConfig,
    PreparedModel,
    _candidate_pairs,
    _generate_paths,
    _layered_graph,
    _path_bundle_matrix,
    _sample_pois,
    _static_network,
)


def _minimum_costs_by_candidate(
    graph, bundles, access: pd.DataFrame, candidate_o: np.ndarray,
    candidate_d: np.ndarray, n_stations: int, n_pois: int,
    cfg: ExperimentConfig,
) -> np.ndarray:
    """Compute exact zero-flow minimum generalized cost without storing paths."""
    access_station = np.full((n_pois, cfg.access_station_count), -1, dtype=np.int32)
    access_walk = np.full((n_pois, cfg.access_station_count), np.inf, dtype=float)
    poi = access.poi_idx.to_numpy(np.int32)
    rank = access.access_rank.to_numpy(np.int32) - 1
    access_station[poi, rank] = access.station_idx.to_numpy(np.int32)
    access_walk[poi, rank] = access.walk_time_min.to_numpy(float)

    minimum = np.full(len(candidate_o), np.inf, dtype=np.float32)
    origins, starts, counts = np.unique(candidate_o, return_index=True, return_counts=True)
    source_base = (cfg.max_transfers + 2) * n_stations
    stride = max(1, len(origins) // 100)
    for completed, (origin, begin, count) in enumerate(
        zip(origins, starts, counts, strict=True), start=1
    ):
        dist = dijkstra(
            graph, directed=True, indices=source_base + int(origin),
            return_predecessors=False,
        )
        sl = slice(int(begin), int(begin + count))
        destinations = candidate_d[sl]
        stations = access_station[destinations]
        walks = access_walk[destinations]
        valid = stations >= 0
        best = np.full(len(destinations), np.inf, dtype=float)
        for boardings in range(1, cfg.max_transfers + 2):
            nodes = np.where(valid, stations + boardings * n_stations, 0)
            costs = np.where(valid, dist[nodes] + walks, np.inf)
            costs += cfg.transfer_penalty_min * (boardings - 1)
            best = np.minimum(best, np.min(costs, axis=1))
        minimum[sl] = best.astype(np.float32)
        if completed == 1 or completed == len(origins) or completed % stride == 0:
            print(f"OD cost scan: {completed}/{len(origins)} origins", flush=True)
    return minimum


def _apportion_draws(raw: np.ndarray, total_draws: int) -> np.ndarray:
    quotas = total_draws * raw / raw.sum()
    draws = np.floor(quotas).astype(np.int64)
    remainder = total_draws - int(draws.sum())
    if remainder:
        order = np.argsort(-(quotas - draws), kind="stable")
        draws[order[:remainder]] += 1
    return draws


def _sample_od_pairs(
    pois: pd.DataFrame, candidate_o: np.ndarray, candidate_d: np.ndarray,
    candidate_distance: np.ndarray, minimum_cost: np.ndarray,
    cfg: ExperimentConfig,
):
    """Stratified Monte Carlo representation of the complete fractional OD matrix."""
    production_raw, attraction = poi_marginals(pois, cfg)

    origins, starts, counts = np.unique(candidate_o, return_index=True, return_counts=True)
    active = np.zeros(len(pois), dtype=bool)
    for origin, begin, pair_count in zip(origins, starts, counts, strict=True):
        sl=slice(int(begin), int(begin + pair_count))
        active[int(origin)] = (np.isfinite(minimum_cost[sl]) & (attraction[candidate_d[sl]] > 0)).any()
    production_raw[~active] = 0.0
    if production_raw.sum() <= 0: raise ValueError('可达OD中没有正发生及吸引权重，不能生成需求')
    production = cfg.total_demand_pax_per_hour * production_raw / production_raw.sum()
    total_draws = max(1, int(round(cfg.total_demand_pax_per_hour)))
    draws = _apportion_draws(production_raw, total_draws)

    rng = np.random.default_rng(cfg.random_seed)
    selected_pair: list[np.ndarray] = []
    selected_count: list[np.ndarray] = []
    for origin, begin, pair_count in zip(origins, starts, counts, strict=True):
        origin = int(origin)
        n = int(draws[origin])
        if n == 0:
            continue
        positions = np.arange(int(begin), int(begin + pair_count), dtype=np.int64)
        finite = np.isfinite(minimum_cost[positions]) & (attraction[candidate_d[positions]] > 0)
        positions = positions[finite]
        if len(positions) == 0:
            continue
        destinations = candidate_d[positions]
        log_weight = (
            np.log(np.maximum(attraction[destinations], 1e-300))
            - cfg.gravity_theta_per_min * minimum_cost[positions].astype(float)
        )
        probability = np.exp(log_weight - log_weight.max())
        probability /= probability.sum()
        sampled = rng.choice(positions, size=n, replace=True, p=probability)
        unique, multiplicity = np.unique(sampled, return_counts=True)
        selected_pair.append(unique)
        selected_count.append(multiplicity.astype(np.int32))

    pair_idx = np.concatenate(selected_pair)
    multiplicity = np.concatenate(selected_count)
    selected_o = candidate_o[pair_idx]
    selected_d = candidate_d[pair_idx]
    selected_distance = candidate_distance[pair_idx]
    selected_demand = (
        production[selected_o] * multiplicity / draws[selected_o]
    )
    return (
        selected_o, selected_d, selected_distance, selected_demand,
        total_draws, active.sum(),
    )



def _stream_sample_od_pairs(
    graph, pois: pd.DataFrame, access: pd.DataFrame, n_stations: int,
    cfg: ExperimentConfig,
):
    """Scan the complete distance choice set one origin at a time."""
    n_pois = len(pois)
    xy = pois[["x_m", "y_m"]].to_numpy(float)
    tree = cKDTree(xy)
    access_station = np.full((n_pois, cfg.access_station_count), -1, dtype=np.int32)
    access_walk = np.full((n_pois, cfg.access_station_count), np.inf, dtype=float)
    poi = access.poi_idx.to_numpy(np.int32)
    rank = access.access_rank.to_numpy(np.int32) - 1
    access_station[poi, rank] = access.station_idx.to_numpy(np.int32)
    access_walk[poi, rank] = access.walk_time_min.to_numpy(float)

    production_raw, attraction = poi_marginals(pois, cfg)
    if production_raw.sum() <= 0: raise ValueError("全部POI发生权重为零，无法抽样OD")
    requested_draws = max(1, int(round(cfg.total_demand_pax_per_hour)))
    draws = _apportion_draws(production_raw, requested_draws)
    active = np.zeros(n_pois, dtype=bool)
    rng = np.random.default_rng(cfg.random_seed)
    selected_o: list[np.ndarray] = []
    selected_d: list[np.ndarray] = []
    selected_distance: list[np.ndarray] = []
    selected_multiplicity: list[np.ndarray] = []
    full_candidate_count = 0
    effective_draws = 0
    source_base = (cfg.max_transfers + 2) * n_stations
    minimum_sq = (cfg.min_od_distance_km * 1000.0) ** 2
    maximum_m = cfg.max_od_distance_km * 1000.0
    stride = max(1, n_pois // 100)

    for origin in range(n_pois):
        neighbors = np.asarray(
            tree.query_ball_point(xy[origin], maximum_m, return_sorted=True),
            dtype=np.int32,
        )
        delta = xy[neighbors] - xy[origin]
        distance_sq = np.einsum("ij,ij->i", delta, delta)
        keep = (distance_sq >= minimum_sq) & (neighbors != origin)
        destinations = neighbors[keep]
        distances_km = np.sqrt(distance_sq[keep]) / 1000.0
        full_candidate_count += len(destinations)
        n = int(draws[origin])
        if n == 0 or len(destinations) == 0:
            continue

        dist = dijkstra(
            graph, directed=True, indices=source_base + origin,
            return_predecessors=False,
        )
        stations = access_station[destinations]
        walks = access_walk[destinations]
        valid = stations >= 0
        best = np.full(len(destinations), np.inf, dtype=float)
        for boardings in range(1, cfg.max_transfers + 2):
            nodes = np.where(valid, stations + boardings * n_stations, 0)
            costs = np.where(valid, dist[nodes] + walks, np.inf)
            costs += cfg.transfer_penalty_min * (boardings - 1)
            best = np.minimum(best, np.min(costs, axis=1))
        feasible = np.isfinite(best) & (attraction[destinations] > 0)
        if not feasible.any():
            continue
        active[origin] = True
        destinations = destinations[feasible]
        distances_km = distances_km[feasible]
        best = best[feasible]
        log_weight = (
            np.log(np.maximum(attraction[destinations], 1e-300))
            - cfg.gravity_theta_per_min * best
        )
        probability = np.exp(log_weight - log_weight.max())
        probability /= probability.sum()
        sampled_pos = rng.choice(len(destinations), size=n, replace=True, p=probability)
        unique_pos, multiplicity = np.unique(sampled_pos, return_counts=True)
        selected_o.append(np.full(len(unique_pos), origin, dtype=np.int32))
        selected_d.append(destinations[unique_pos])
        selected_distance.append(distances_km[unique_pos])
        selected_multiplicity.append(multiplicity.astype(np.int32))
        effective_draws += n
        completed = origin + 1
        if completed == 1 or completed == n_pois or completed % stride == 0:
            print(
                f"streaming OD scan: {completed}/{n_pois} origins, "
                f"sampled_pairs={sum(map(len, selected_o))}",
                flush=True,
            )

    if not selected_o: raise ValueError("距离及线路约束下没有正需求的可达OD")
    o = np.concatenate(selected_o)
    d = np.concatenate(selected_d)
    distance = np.concatenate(selected_distance)
    multiplicity = np.concatenate(selected_multiplicity)
    active_raw = production_raw.copy()
    active_raw[~active] = 0.0
    production = cfg.total_demand_pax_per_hour * active_raw / active_raw.sum()
    demand = production[o] * multiplicity / draws[o]
    return (
        o, d, distance, demand, full_candidate_count,
        requested_draws, effective_draws, int(active.sum()),
    )

def prepare_model_sparse(base: Path, city_id: str, cfg: ExperimentConfig, headways_path: Path, *, raw_stops: Path | None = None) -> PreparedModel:
    started = time.perf_counter()
    processed = base / "processed" / city_id / "network"
    if raw_stops is None:
        raw_stops = next((base / "CPTOND-2025" / "dataset" / "bus" / "shapefiles" / city_id).glob("*_bus_stops.shp"))
    else:
        raw_stops = Path(raw_stops)
    stations = pd.read_csv(
        processed / "bus_stations_merged.csv", dtype={"station_id": str}
    )
    topology = pd.read_csv(
        processed / "bus_edges_topology.csv",
        dtype={"from_station": str, "to_station": str},
    )
    geom = pd.to_numeric(topology.geom_len_km_mean, errors="coerce")
    straight = pd.to_numeric(topology.distance_km_mean, errors="coerce")
    topology["distance_km"] = geom.where(geom > 0, straight)
    topology = topology.dropna(subset=["distance_km"]).query(
        "distance_km > 0"
    ).reset_index(drop=True)
    topology["edge_idx"] = np.arange(len(topology), dtype=np.int32)

    routes, route_segments, source_counts, bundles, options, cache_hit = _static_network(
        base, processed, stations, topology, cfg, raw_stops, headways_path
    )
    pois, access, poi_summary = _sample_pois(
        processed.parent / "poi_clustered.csv", stations, cfg
    )
    graph = _layered_graph(bundles, access, len(stations), len(pois), cfg)
    (
        selected_o, selected_d, selected_distance, selected_demand,
        full_candidate_count, total_draws, effective_draws, active_origins,
    ) = _stream_sample_od_pairs(
        graph, pois, access, len(stations), cfg
    )

    paths, support = _generate_paths(
        graph, bundles, access, selected_o, selected_d, selected_distance,
        len(stations), len(pois), cfg,
    )
    support["origin_cluster"] = pois.iloc[
        support.origin_poi_idx.to_numpy(np.int32)
    ].cluster_id.astype(str).to_numpy()
    support["destination_cluster"] = pois.iloc[
        support.destination_poi_idx.to_numpy(np.int32)
    ].cluster_id.astype(str).to_numpy()
    min_cost = paths.groupby("od_idx").free_generalized_cost_min.min()
    support["zero_flow_cost_min"] = support.od_idx.map(min_cost).to_numpy(float)
    support["friction"] = np.exp(
        -cfg.gravity_theta_per_min * support.zero_flow_cost_min.to_numpy(float)
    )
    support["demand_pax_per_hour"] = selected_demand[
        support.candidate_pair_idx.to_numpy(np.int64)
    ]
    od = support

    paths = paths.reset_index(drop=True)
    paths["path_idx"] = np.arange(len(paths), dtype=np.int32)
    for column in ("origin_poi_idx", "destination_poi_idx", "candidate_pair_idx", "od_idx"):
        paths[column] = paths[column].astype(np.int32)
        od[column] = od[column].astype(np.int32)
    for column in ("boardings", "transfers"):
        paths[column] = paths[column].astype(np.int8)
    for column in ("access_station", "egress_station"):
        paths[column] = paths[column].astype("category")
    for column in ("origin_cluster", "destination_cluster"):
        od[column] = od[column].astype("category")
    path_bundle = _path_bundle_matrix(paths, len(bundles["keys"]))
    paths.drop(columns="bundle_ids", inplace=True)

    summary = {
        "routes": len(routes),
        "route_segments": len(route_segments),
        "route_section_options": len(options["route"]),
        "common_line_bundles": len(bundles["keys"]),
        "multi_line_bundles": int(np.sum(bundles["counts"] > 1)),
        "sampled_pois": len(pois),
        "candidate_od_pairs": full_candidate_count,
        "sampled_nonzero_od_pairs": len(od),
        "feasible_od_pairs": len(od),
        "candidate_paths": len(paths),
        "initial_candidate_paths": len(paths),
        "od_sampling_draws": total_draws,
        "od_sampling_effective_draws": effective_draws,
        "od_sampling_active_origins": int(active_origins),
        "od_sampling_method": "origin-streamed stratified multinomial using the complete distance choice set and zero-flow transit generalized cost",
        "total_demand_pax_per_hour": float(od.demand_pax_per_hour.sum()),
        "preparation_seconds": time.perf_counter() - started,
        "static_network_cache_hit": "yes" if cache_hit else "no",
        "demand_unit": cfg.demand_unit,
    }
    summary.update(poi_summary)
    summary.update(source_counts)
    imputed = int(source_counts.get("network_observed_median", 0))
    summary["headway_empirical_imputation_share"] = (
        imputed / len(routes) if routes else 0.0
    )
    return PreparedModel(
        cfg, stations, topology, pois, access, od, paths, path_bundle,
        bundles["keys"], bundles["from"], bundles["to"],
        bundles["base_wait"], bundles["expected_ride"], options["bundle"],
        options["probability"], options["first_segment"],
        options["after_segment"], route_segments, summary,
    )


