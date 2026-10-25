from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import hashlib
import json
import pickle
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix, csr_matrix, vstack
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree


PM_ORIGIN_WEIGHTS = {
    "公司企业": 35.0, "科教文化服务": 22.0, "政府机构及社会团体": 12.0,
    "购物服务": 10.0, "餐饮服务": 7.0, "交通设施服务": 6.0,
    "金融保险服务": 5.0, "通行设施": 4.0, "医疗保健服务": 4.0,
    "体育休闲服务": 2.5, "生活服务": 2.5, "公共设施": 2.0,
    "地名地址信息": 1.8, "汽车服务": 1.5, "住宿服务": 1.3,
    "汽车销售": 1.2, "汽车维修": 1.2, "风景名胜": 1.2,
    "商务住宅": 1.0, "道路附属设施": 1.0,
}

PM_DESTINATION_WEIGHTS = {
    "商务住宅": 45.0, "住宿服务": 14.0, "生活服务": 5.0,
    "地名地址信息": 3.0, "购物服务": 2.5, "餐饮服务": 2.2,
    "交通设施服务": 2.0, "体育休闲服务": 2.0, "通行设施": 1.6,
    "医疗保健服务": 1.6, "风景名胜": 1.5, "公共设施": 1.4,
    "汽车服务": 1.0, "公司企业": 1.0, "科教文化服务": 0.8,
    "政府机构及社会团体": 0.8, "金融保险服务": 0.8,
    "汽车销售": 0.8, "汽车维修": 0.8, "道路附属设施": 0.8,
}

OFFPEAK_ORIGIN_WEIGHTS = {
    "商务住宅": 2.0, "住宿服务": 1.3, "生活服务": 1.8,
    "地名地址信息": 1.4, "购物服务": 2.3, "餐饮服务": 2.2,
    "交通设施服务": 1.6, "通行设施": 1.4, "医疗保健服务": 1.5,
    "公司企业": 1.3, "科教文化服务": 1.4, "政府机构及社会团体": 1.2,
    "金融保险服务": 1.2, "体育休闲服务": 1.8, "风景名胜": 1.7,
    "汽车服务": 1.2, "汽车销售": 1.0, "汽车维修": 1.0,
    "公共设施": 1.3, "道路附属设施": 1.0,
}

OFFPEAK_DESTINATION_WEIGHTS = {
    "商务住宅": 2.2, "住宿服务": 1.4, "生活服务": 1.9,
    "地名地址信息": 1.5, "购物服务": 2.4, "餐饮服务": 2.3,
    "交通设施服务": 1.7, "通行设施": 1.5, "医疗保健服务": 1.6,
    "公司企业": 1.2, "科教文化服务": 1.3, "政府机构及社会团体": 1.2,
    "金融保险服务": 1.2, "体育休闲服务": 1.9, "风景名胜": 1.8,
    "汽车服务": 1.2, "汽车销售": 1.0, "汽车维修": 1.0,
    "公共设施": 1.4, "道路附属设施": 1.0,
}

PERIOD_WEIGHTS = {
    "am_peak": (PM_DESTINATION_WEIGHTS, PM_ORIGIN_WEIGHTS),
    "offpeak": (OFFPEAK_ORIGIN_WEIGHTS, OFFPEAK_DESTINATION_WEIGHTS),
    "pm_peak": (PM_ORIGIN_WEIGHTS, PM_DESTINATION_WEIGHTS),
}

PERIOD_WINDOWS_SECONDS = {
    "am_peak": (7 * 3600, 9 * 3600),
    "offpeak": (9 * 3600, 17 * 3600),
    "pm_peak": (17 * 3600, 19 * 3600),
}


@dataclass(frozen=True)
class ExperimentConfig:
    period: str = "pm_peak"
    sample_size: int = 100
    random_seed: int = 20260825
    total_demand_pax_per_hour: float = 100_000.0
    demand_unit: str = "od_trips"
    access_station_count: int = 3
    max_walk_distance_m: float = 1000.0
    walk_speed_kmh: float = 4.5
    bus_speed_kmh: float = 18.0
    vehicle_capacity: float = 80.0
    fallback_headway_min: float = 15.0
    min_od_distance_km: float = 1.0
    max_od_distance_km: float = 20.0
    gravity_theta_per_min: float = 0.03
    default_poi_weight: float = 1.0
    poi_category_weights: dict | None = None
    od_period_weights: dict | None = None
    max_transfers: int = 2
    paths_per_transfer: int = 1
    transfer_penalty_min: float = 0.0
    crowding_exponent: float = 2.0
    relative_gap_tol: float = 1e-4
    max_iterations: int = 1000
    output_detail: str = "compact"


@dataclass
class PreparedModel:
    config: ExperimentConfig
    stations: pd.DataFrame
    topology: pd.DataFrame
    pois: pd.DataFrame
    access: pd.DataFrame
    od: pd.DataFrame
    paths: pd.DataFrame
    path_bundle: csr_matrix
    bundle_keys: np.ndarray
    bundle_from: np.ndarray
    bundle_to: np.ndarray
    bundle_base_wait: np.ndarray
    bundle_expected_ride: np.ndarray
    option_bundle: np.ndarray
    option_probability: np.ndarray
    option_first_segment: np.ndarray
    option_after_segment: np.ndarray
    route_segments: pd.DataFrame
    summary: dict[str, float | int | str]


def _load_headways(path: Path, cfg: ExperimentConfig) -> tuple[dict[str, float], dict[str, str]]:
    table = pd.read_csv(path, dtype={"route_id": str})
    headway_col = "flat_headway_min" if cfg.period == "offpeak" else "peak_headway_min"
    source_col = "flat_headway_source" if cfg.period == "offpeak" else "peak_headway_source"
    table[headway_col] = pd.to_numeric(table[headway_col], errors="coerce")
    table = table.dropna(subset=[headway_col]).drop_duplicates("route_id")
    table = table[table[headway_col] > 0]
    return (
        table.set_index("route_id")[headway_col].astype(float).to_dict(),
        table.set_index("route_id")[source_col].astype(str).to_dict(),
    )


def _clock_seconds(value: object) -> int | None:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
        return None
    return hour * 3600 + minute * 60 + second


def _time_item_overlaps_period(item: dict[str, object], period: str) -> bool | None:
    start = _clock_seconds(item.get("start_time"))
    end = _clock_seconds(item.get("end_time"))
    if start is None or end is None:
        return None
    window_start, window_end = PERIOD_WINDOWS_SECONDS[period]
    interval = str(item.get("interval_time") or "").strip()
    if start == end and not interval:
        return window_start <= start < window_end
    if start == end:
        return True
    service_ranges = [(start, end)] if end > start else [(start, 24 * 3600), (0, end)]
    return any(max(left, window_start) < min(right, window_end) for left, right in service_ranges)


def _load_service_status(path: Path, cfg: ExperimentConfig) -> dict[str, str]:
    """Classify each route as active, inactive, or unknown in the analysis period."""
    table = pd.read_csv(path, dtype=str, keep_default_na=False, usecols=["route_id", "timedesc_json"])
    statuses: dict[str, str] = {}
    for row in table.itertuples(index=False):
        route_id = str(row.route_id)
        try:
            payload = json.loads(row.timedesc_json) if row.timedesc_json else {}
        except json.JSONDecodeError:
            payload = {}
        items: list[dict[str, object]] = []
        rules = payload.get("rule_group") or []
        weekday_rules = [
            rule for rule in rules
            if "weekday" in (rule.get("date", {}).get("day_type") or [])
        ]
        all_day_rules = [
            rule for rule in rules
            if "all" in (rule.get("date", {}).get("day_type") or [])
        ]
        selected_rules = weekday_rules or all_day_rules or rules
        for rule in selected_rules:
            items.extend(rule.get("time_group") or [])
        if not items:
            for key in ("peak_time_group", "flat_time_group", "low_time_group"):
                items.extend(payload.get(key) or [])
        overlaps = [_time_item_overlaps_period(item, cfg.period) for item in items]
        usable = [value for value in overlaps if value is not None]
        status = "active" if any(usable) else ("inactive" if usable else "unknown")
        previous = statuses.get(route_id)
        if previous == "active" or status == previous:
            continue
        if status == "active" or previous is None or previous == "unknown":
            statuses[route_id] = status
    return statuses


def _build_routes(
    raw_stops: Path,
    stop_mapping: Path,
    stations: pd.DataFrame,
    topology: pd.DataFrame,
    headways_path: Path,
    timedesc_path: Path | None,
    cfg: ExperimentConfig,
) -> tuple[list[dict[str, object]], pd.DataFrame, dict[str, int]]:
    station_index = dict(zip(stations.station_id.astype(str), np.arange(len(stations))))
    topology_lookup = {
        (str(r.from_station), str(r.to_station)): int(r.edge_idx)
        for r in topology.itertuples(index=False)
    }
    headways, sources = _load_headways(headways_path, cfg)
    service_status = _load_service_status(timedesc_path, cfg) if timedesc_path else {}
    stops = gpd.read_file(raw_stops, ignore_geometry=True)[
        ["route_id", "route_cn", "stop_id", "sequence"]
    ]
    stops = stops.astype({"route_id": str, "route_cn": str, "stop_id": str})
    stops["sequence"] = pd.to_numeric(stops.sequence, errors="coerce")
    mapping = pd.read_csv(stop_mapping, dtype=str)[["stop_id", "station_id"]]
    stops = stops.dropna(subset=["sequence"]).merge(mapping, on="stop_id", how="inner")
    stops = stops.sort_values(["route_id", "route_cn", "sequence", "stop_id"])

    routes: list[dict[str, object]] = []
    segment_rows: list[dict[str, object]] = []
    source_counts: dict[str, int] = {}
    skipped_conflict = 0
    skipped_geometry = 0
    skipped_inactive = 0
    skipped_night_name = 0
    retained_unknown = 0
    retained_active = 0
    for (route_id, route_name), group in stops.groupby(["route_id", "route_cn"], sort=False):
        if str(route_name).startswith("夜"):
            skipped_night_name += 1
            continue
        status = service_status.get(route_id, "unknown")
        if status == "inactive":
            skipped_inactive += 1
            continue
        if group.groupby("sequence").station_id.nunique().gt(1).any():
            skipped_conflict += 1
            continue
        seq_ids: list[str] = []
        for sid in group.drop_duplicates(["sequence", "station_id"]).sort_values("sequence").station_id.astype(str):
            if not seq_ids or seq_ids[-1] != sid:
                seq_ids.append(sid)
        if len(seq_ids) < 2 or any(s not in station_index for s in seq_ids):
            continue
        physical = [topology_lookup.get((u, v), -1) for u, v in zip(seq_ids[:-1], seq_ids[1:], strict=True)]
        if any(e < 0 for e in physical):
            skipped_geometry += 1
            continue
        if status == "active":
            retained_active += 1
        else:
            retained_unknown += 1
        route_idx = len(routes)
        headway = float(headways.get(route_id, cfg.fallback_headway_min))
        frequency = 60.0 / headway
        capacity = frequency * cfg.vehicle_capacity
        source = sources.get(route_id, "configured_fallback")
        source_counts[source] = source_counts.get(source, 0) + 1
        global_segments: list[int] = []
        cumulative_time = [0.0]
        for pos, (u, v, edge_idx) in enumerate(zip(seq_ids[:-1], seq_ids[1:], physical, strict=True)):
            edge = topology.iloc[edge_idx]
            distance = float(edge.distance_km)
            t0 = 60.0 * distance / cfg.bus_speed_kmh
            segment_id = len(segment_rows)
            global_segments.append(segment_id)
            cumulative_time.append(cumulative_time[-1] + t0)
            segment_rows.append(
                {
                    "segment_id": segment_id, "route_idx": route_idx, "route_id": route_id,
                    "route_name": route_name, "position": pos, "from_station": u,
                    "to_station": v, "physical_edge_idx": edge_idx, "distance_km": distance,
                    "free_time_min": t0, "headway_min": headway,
                    "frequency_per_hour": frequency, "capacity_per_hour": capacity,
                    "headway_source": source,
                }
            )
        routes.append(
            {
                "route_idx": route_idx, "route_id": route_id, "route_name": route_name,
                "station_ids": seq_ids,
                "station_idx": np.asarray([station_index[s] for s in seq_ids], dtype=np.int32),
                "segment_ids": np.asarray(global_segments, dtype=np.int32),
                "cumulative_time": np.asarray(cumulative_time, dtype=float),
                "headway": headway, "frequency": frequency, "capacity": capacity,
            }
        )
    source_counts["skipped_sequence_conflict"] = skipped_conflict
    source_counts["skipped_missing_physical_edge"] = skipped_geometry
    source_counts["service_active_route_directions"] = retained_active
    source_counts["service_unknown_route_directions_retained"] = retained_unknown
    source_counts["service_inactive_route_directions_skipped"] = skipped_inactive
    source_counts["night_route_directions_skipped_by_name"] = skipped_night_name
    return routes, pd.DataFrame(segment_rows), source_counts


def _build_bundles(
    routes: list[dict[str, object]], n_stations: int
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    from_buf: list[np.ndarray] = []
    to_buf: list[np.ndarray] = []
    route_buf: list[np.ndarray] = []
    start_buf: list[np.ndarray] = []
    end_buf: list[np.ndarray] = []
    freq_buf: list[np.ndarray] = []
    ride_buf: list[np.ndarray] = []
    first_segment_buf: list[np.ndarray] = []
    after_segment_buf: list[np.ndarray] = []
    for route in routes:
        stations = route["station_idx"]
        n = len(stations)
        ii, jj = np.triu_indices(n, 1)
        from_buf.append(stations[ii])
        to_buf.append(stations[jj])
        route_buf.append(np.full(len(ii), int(route["route_idx"]), dtype=np.int32))
        start_buf.append(ii.astype(np.int16))
        end_buf.append(jj.astype(np.int16))
        freq_buf.append(np.full(len(ii), float(route["frequency"]), dtype=np.float32))
        cumulative = route["cumulative_time"]
        ride_buf.append((cumulative[jj] - cumulative[ii]).astype(np.float32))
        first_segment_buf.append(route["segment_ids"][ii])
        after_segment_buf.append((int(route["segment_ids"][0]) + jj).astype(np.int32))

    option_from = np.concatenate(from_buf)
    option_to = np.concatenate(to_buf)
    option_key = option_from.astype(np.int64) * n_stations + option_to
    order = np.argsort(option_key, kind="stable")
    option_key = option_key[order]
    option_route = np.concatenate(route_buf)[order]
    option_start = np.concatenate(start_buf)[order]
    option_end = np.concatenate(end_buf)[order]
    option_frequency = np.concatenate(freq_buf)[order].astype(float)
    option_ride = np.concatenate(ride_buf)[order].astype(float)
    option_first_segment = np.concatenate(first_segment_buf)[order]
    option_after_segment = np.concatenate(after_segment_buf)[order]
    bundle_keys, first, counts = np.unique(option_key, return_index=True, return_counts=True)
    bundle_frequency = np.add.reduceat(option_frequency, first)
    bundle_idx = np.repeat(np.arange(len(bundle_keys), dtype=np.int32), counts)
    probability = option_frequency / bundle_frequency[bundle_idx]
    bundle_expected_ride = np.add.reduceat(probability * option_ride, first)
    bundles = {
        "keys": bundle_keys,
        "from": (bundle_keys // n_stations).astype(np.int32),
        "to": (bundle_keys % n_stations).astype(np.int32),
        "first": first.astype(np.int64),
        "counts": counts.astype(np.int32),
        "frequency": bundle_frequency,
        "base_wait": 30.0 / bundle_frequency,
        "expected_ride": bundle_expected_ride,
    }
    options = {
        "bundle": bundle_idx,
        "route": option_route,
        "start": option_start,
        "end": option_end,
        "frequency": option_frequency,
        "probability": probability,
        "ride": option_ride,
        "first_segment": option_first_segment,
        "after_segment": option_after_segment,
    }
    return bundles, options


def _sample_pois(
    poi_path: Path, stations: pd.DataFrame, cfg: ExperimentConfig
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    source = pd.read_csv(poi_path)
    source_rows = len(source)
    source = source.dropna(subset=["cluster_id", "class_1", "x_m", "y_m"]).copy()
    source["x_m"] = pd.to_numeric(source.x_m, errors="coerce")
    source["y_m"] = pd.to_numeric(source.y_m, errors="coerce")
    source = source.dropna(subset=["x_m", "y_m"])
    class_conflicts = int((source.groupby("cluster_id").class_1.nunique() > 1).sum())
    if class_conflicts:
        raise ValueError(f"{class_conflicts} POI clusters contain multiple class_1 values")
    aggregation: dict[str, str] = {"class_1": "first", "x_m": "mean", "y_m": "mean"}
    for column in ("longitude", "latitude", "lon", "lat", "name"):
        if column in source.columns:
            aggregation[column] = "mean" if column != "name" else "first"
            if column != "name":
                source[column] = pd.to_numeric(source[column], errors="coerce")
    pois = source.groupby("cluster_id", as_index=False, sort=False).agg(aggregation)
    cluster_sizes = source.groupby("cluster_id", sort=False).size()
    pois["cluster_member_count"] = pois.cluster_id.map(cluster_sizes).astype(int)
    pois["poi_count"] = 1.0
    poi_summary = {
        "source_poi_rows": source_rows,
        "valid_source_poi_rows": len(source),
        "unique_poi_clusters": len(pois),
        "duplicate_poi_rows_removed_before_sampling": len(source) - len(pois),
        "poi_cluster_class_conflicts": class_conflicts,
    }
    tree = cKDTree(stations[["x_m", "y_m"]].to_numpy(float))
    nearest, _ = tree.query(pois[["x_m", "y_m"]].to_numpy(float), k=1, workers=-1)
    eligible = pois[np.asarray(nearest) <= cfg.max_walk_distance_m].copy()
    if len(eligible) < cfg.sample_size:
        raise ValueError(f"Only {len(eligible)} POIs are within walking range")
    sampled = eligible.sample(cfg.sample_size, random_state=cfg.random_seed)
    sampled = sampled.sort_values("cluster_id").reset_index(drop=True)
    if not sampled.cluster_id.is_unique:
        raise RuntimeError("POI sampling must contain unique cluster_id values")
    k = min(cfg.access_station_count, len(stations))
    distances, positions = tree.query(sampled[["x_m", "y_m"]].to_numpy(float), k=k, workers=-1)
    distances = np.asarray(distances).reshape(len(sampled), k)
    positions = np.asarray(positions).reshape(len(sampled), k)
    valid = distances <= cfg.max_walk_distance_m
    poi_idx, rank_idx = np.where(valid)
    station_idx = positions[poi_idx, rank_idx].astype(np.int32)
    access = pd.DataFrame(
        {
            "poi_idx": poi_idx.astype(np.int32),
            "cluster_id": sampled.cluster_id.astype(str).to_numpy()[poi_idx],
            "station_idx": station_idx,
            "station_id": stations.station_id.astype(str).to_numpy()[station_idx],
            "station_name": stations.station_name.astype(str).to_numpy()[station_idx],
            "access_rank": (rank_idx + 1).astype(np.int8),
            "walk_distance_m": distances[poi_idx, rank_idx],
        }
    )
    access["walk_time_min"] = 60.0 * access.walk_distance_m / 1000.0 / cfg.walk_speed_kmh
    poi_summary["walk_eligible_unique_poi_clusters"] = len(eligible)
    poi_summary["sampled_unique_poi_clusters"] = int(sampled.cluster_id.nunique())
    return sampled, access, poi_summary


def _layered_graph(
    bundles: dict[str, np.ndarray], access: pd.DataFrame, n_stations: int,
    n_pois: int, cfg: ExperimentConfig, bundle_cost: np.ndarray | None = None,
) -> csr_matrix:
    boardings = cfg.max_transfers + 1
    rows = []
    cols = []
    data = []
    edge_cost = (
        bundles["base_wait"] + bundles["expected_ride"]
        if bundle_cost is None else np.asarray(bundle_cost, dtype=float)
    )
    if len(edge_cost) != len(bundles["keys"]):
        raise ValueError("bundle_cost length does not match the bundle network")
    for layer in range(boardings):
        rows.append(bundles["from"] + layer * n_stations)
        cols.append(bundles["to"] + (layer + 1) * n_stations)
        data.append(edge_cost)
    source_base = (boardings + 1) * n_stations
    rows.append(source_base + access.poi_idx.to_numpy(np.int32))
    cols.append(access.station_idx.to_numpy(np.int32))
    data.append(access.walk_time_min.to_numpy(float))
    return coo_matrix(
        (np.concatenate(data), (np.concatenate(rows), np.concatenate(cols))),
        shape=(source_base + n_pois, source_base + n_pois),
    ).tocsr()


def _candidate_pairs(pois: pd.DataFrame, cfg: ExperimentConfig) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xy = pois[["x_m", "y_m"]].to_numpy(float)
    tree = cKDTree(xy)
    minimum_sq = (cfg.min_od_distance_km * 1000.0) ** 2
    maximum_m = cfg.max_od_distance_km * 1000.0
    maximum_sq = maximum_m**2
    origin_chunks: list[np.ndarray] = []
    destination_chunks: list[np.ndarray] = []
    distance_chunks: list[np.ndarray] = []
    chunk_size = 256
    for begin in range(0, len(xy), chunk_size):
        end = min(begin + chunk_size, len(xy))
        neighborhoods = tree.query_ball_point(
            xy[begin:end], maximum_m, return_sorted=True, workers=-1
        )
        chunk_origins: list[np.ndarray] = []
        chunk_destinations: list[np.ndarray] = []
        chunk_distances: list[np.ndarray] = []
        for local_idx, neighbors in enumerate(neighborhoods):
            origin = begin + local_idx
            destination = np.asarray(neighbors, dtype=np.int32)
            delta = xy[destination] - xy[origin]
            distance_sq = np.einsum("ij,ij->i", delta, delta)
            keep = (distance_sq >= minimum_sq) & (distance_sq <= maximum_sq) & (destination != origin)
            destination = destination[keep]
            if len(destination) == 0:
                continue
            chunk_origins.append(np.full(len(destination), origin, dtype=np.int32))
            chunk_destinations.append(destination)
            chunk_distances.append(np.sqrt(distance_sq[keep]) / 1000.0)
        if chunk_origins:
            origin_chunks.append(np.concatenate(chunk_origins))
            destination_chunks.append(np.concatenate(chunk_destinations))
            distance_chunks.append(np.concatenate(chunk_distances))
    if not origin_chunks:
        empty_int = np.empty(0, dtype=np.int32)
        return empty_int, empty_int.copy(), np.empty(0, dtype=float)
    return (
        np.concatenate(origin_chunks),
        np.concatenate(destination_chunks),
        np.concatenate(distance_chunks),
    )


def _bundle_for_pair(key: int, bundle_keys: np.ndarray) -> int:
    pos = int(np.searchsorted(bundle_keys, key))
    if pos >= len(bundle_keys) or int(bundle_keys[pos]) != int(key):
        raise RuntimeError("Layered predecessor does not map to a route-section bundle")
    return pos


def _generate_paths(
    graph: csr_matrix, bundles: dict[str, np.ndarray], access: pd.DataFrame,
    candidate_o: np.ndarray, candidate_d: np.ndarray, candidate_distance: np.ndarray,
    n_stations: int, n_pois: int, cfg: ExperimentConfig
) -> tuple[pd.DataFrame, pd.DataFrame]:
    access_station = np.full((n_pois, cfg.access_station_count), -1, dtype=np.int32)
    access_walk = np.full((n_pois, cfg.access_station_count), np.inf, dtype=float)
    access_poi = access.poi_idx.to_numpy(np.int32)
    access_rank = access.access_rank.to_numpy(np.int32) - 1
    access_station[access_poi, access_rank] = access.station_idx.to_numpy(np.int32)
    access_walk[access_poi, access_rank] = access.walk_time_min.to_numpy(float)
    origins, origin_starts, origin_counts = np.unique(
        candidate_o, return_index=True, return_counts=True
    )
    source_base = (cfg.max_transfers + 2) * n_stations
    path_chunks: list[pd.DataFrame] = []
    support_chunks: list[pd.DataFrame] = []
    path_count = 0
    progress_stride = max(1, len(origins) // 100)
    for completed, (origin, pair_start, pair_count) in enumerate(
        zip(origins, origin_starts, origin_counts, strict=True), start=1
    ):
        origin = int(origin)
        source = source_base + origin
        dist, pred = dijkstra(graph, directed=True, indices=source, return_predecessors=True)
        origin_path_rows: list[dict[str, object]] = []
        origin_support_rows: list[dict[str, object]] = []
        for pair_idx in range(int(pair_start), int(pair_start + pair_count)):
            destination = int(candidate_d[pair_idx])
            destination_valid = access_station[destination] >= 0
            destination_station = access_station[destination, destination_valid]
            destination_walk = access_walk[destination, destination_valid]
            od_path_start = path_count + len(origin_path_rows)
            for boardings in range(1, cfg.max_transfers + 2):
                nodes = destination_station + boardings * n_stations
                totals = dist[nodes] + destination_walk
                best_pos = int(np.argmin(totals))
                if not np.isfinite(totals[best_pos]):
                    continue
                destination_node = int(nodes[best_pos])
                egress_time = float(destination_walk[best_pos])
                cur = destination_node
                bundle_rev: list[int] = []
                access_station_idx = -1
                while cur != source:
                    previous = int(pred[cur])
                    if previous < 0:
                        bundle_rev = []
                        break
                    if previous == source:
                        access_station_idx = cur % n_stations
                    elif cur < source_base and previous < source_base:
                        previous_layer, previous_station = divmod(previous, n_stations)
                        current_layer, current_station = divmod(cur, n_stations)
                        if current_layer == previous_layer + 1:
                            bundle_rev.append(
                                _bundle_for_pair(previous_station * n_stations + current_station, bundles["keys"])
                            )
                    cur = previous
                if not bundle_rev or access_station_idx < 0:
                    continue
                bundle_ids = tuple(reversed(bundle_rev))
                origin_position = int(np.flatnonzero(access_station[origin] == access_station_idx)[0])
                origin_walk = float(access_walk[origin, origin_position])
                transfer_penalty_cost = cfg.transfer_penalty_min * (len(bundle_ids) - 1)
                origin_path_rows.append(
                    {
                        "origin_poi_idx": origin, "destination_poi_idx": destination,
                        "candidate_pair_idx": pair_idx, "boardings": len(bundle_ids),
                        "transfers": len(bundle_ids) - 1, "bundle_ids": bundle_ids,
                        "path_rank_within_transfer": 1,
                        "path_hash": np.int64(hash(bundle_ids)),
                        "access_station_idx": access_station_idx,
                        "egress_station_idx": int(destination_station[best_pos]),
                        "access_time_min": origin_walk,
                        "egress_time_min": egress_time,
                        "transfer_penalty_cost_min": transfer_penalty_cost,
                        "connector_cost_min": (
                            origin_walk + egress_time + transfer_penalty_cost
                        ),
                        "free_generalized_cost_min": float(totals[best_pos]) + transfer_penalty_cost,
                    }
                )
            if path_count + len(origin_path_rows) > od_path_start:
                origin_support_rows.append(
                    {
                        "candidate_pair_idx": pair_idx, "origin_poi_idx": origin,
                        "destination_poi_idx": destination,
                        "euclidean_distance_km": float(candidate_distance[pair_idx]),
                        "path_start": od_path_start,
                        "path_end": path_count + len(origin_path_rows),
                    }
                )
        if origin_path_rows:
            path_chunk = pd.DataFrame(origin_path_rows)
            for column in (
                "origin_poi_idx", "destination_poi_idx", "candidate_pair_idx",
                "access_station_idx", "egress_station_idx",
            ):
                path_chunk[column] = path_chunk[column].astype(np.int32)
            for column in ("boardings", "transfers", "path_rank_within_transfer"):
                path_chunk[column] = path_chunk[column].astype(np.int8)
            path_chunks.append(path_chunk)
            path_count += len(origin_path_rows)
        if origin_support_rows:
            support_chunk = pd.DataFrame(origin_support_rows)
            for column in (
                "candidate_pair_idx", "origin_poi_idx", "destination_poi_idx"
            ):
                support_chunk[column] = support_chunk[column].astype(np.int32)
            for column in ("path_start", "path_end"):
                support_chunk[column] = support_chunk[column].astype(np.int64)
            support_chunks.append(support_chunk)
        if completed == 1 or completed == len(origins) or completed % progress_stride == 0:
            print(f"path generation: {completed}/{len(origins)} origins", flush=True)
    paths = pd.concat(path_chunks, ignore_index=True, copy=False)
    paths.insert(0, "path_idx", np.arange(len(paths), dtype=np.int32))
    station_ids = np.empty(n_stations, dtype=object)
    station_ids.fill(None)
    station_ids[access.station_idx.to_numpy(np.int32)] = access.station_id.astype(str).to_numpy()
    paths["access_station"] = pd.Categorical(
        station_ids[paths.access_station_idx.to_numpy(np.int32)]
    )
    paths["egress_station"] = pd.Categorical(
        station_ids[paths.egress_station_idx.to_numpy(np.int32)]
    )
    paths.drop(columns=["access_station_idx", "egress_station_idx"], inplace=True)
    support = pd.concat(support_chunks, ignore_index=True, copy=False)
    support.insert(0, "od_idx", np.arange(len(support), dtype=np.int32))
    paths = paths.merge(support[["candidate_pair_idx", "od_idx"]], on="candidate_pair_idx", how="left")
    paths["od_idx"] = paths.od_idx.astype(np.int32)
    return paths, support


def _generate_shortest_columns(
    model: PreparedModel,
    bundle_cost: np.ndarray,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Generate at most one new congested shortest path per OD and transfer count."""
    cfg = model.config
    n_stations = len(model.stations)
    n_pois = len(model.pois)
    bundles = {
        "keys": model.bundle_keys,
        "from": model.bundle_from,
        "to": model.bundle_to,
        "base_wait": model.bundle_base_wait,
        "expected_ride": model.bundle_expected_ride,
    }
    graph = _layered_graph(
        bundles, model.access, n_stations, n_pois, cfg, bundle_cost=bundle_cost
    )

    access_station = np.full((n_pois, cfg.access_station_count), -1, dtype=np.int32)
    access_walk = np.full((n_pois, cfg.access_station_count), np.inf, dtype=float)
    access_poi = model.access.poi_idx.to_numpy(np.int32)
    access_rank = model.access.access_rank.to_numpy(np.int32) - 1
    access_station[access_poi, access_rank] = model.access.station_idx.to_numpy(np.int32)
    access_walk[access_poi, access_rank] = model.access.walk_time_min.to_numpy(float)

    od = model.od.sort_values(["origin_poi_idx", "od_idx"], kind="stable")
    od_origin = od.origin_poi_idx.to_numpy(np.int32)
    od_destination = od.destination_poi_idx.to_numpy(np.int32)
    od_idx_array = od.od_idx.to_numpy(np.int32)
    candidate_pair = od.candidate_pair_idx.to_numpy(np.int32)
    origins, starts, counts = np.unique(od_origin, return_index=True, return_counts=True)
    source_base = (cfg.max_transfers + 2) * n_stations
    station_ids = model.stations.station_id.astype(str).to_numpy()
    free_bundle_cost = model.bundle_base_wait + model.bundle_expected_ride

    existing_count = model.paths.groupby(["od_idx", "transfers"], observed=True).size().to_dict()
    existing_hash = set(
        zip(
            model.paths.od_idx.to_numpy(np.int32),
            model.paths.transfers.to_numpy(np.int8),
            model.paths.path_hash.to_numpy(np.int64),
        )
    )
    generated_count: dict[tuple[int, int], int] = {}
    rows: list[dict[str, object]] = []
    duplicate_count = 0
    capped_count = 0
    progress_stride = max(1, len(origins) // 20)

    for completed, (origin, begin, count) in enumerate(
        zip(origins, starts, counts, strict=True), start=1
    ):
        origin = int(origin)
        source = source_base + origin
        dist, pred = dijkstra(graph, directed=True, indices=source, return_predecessors=True)
        for pos in range(int(begin), int(begin + count)):
            destination = int(od_destination[pos])
            destination_valid = access_station[destination] >= 0
            destination_station = access_station[destination, destination_valid]
            destination_walk = access_walk[destination, destination_valid]
            od_idx = int(od_idx_array[pos])
            for boardings in range(1, cfg.max_transfers + 2):
                transfers = boardings - 1
                count_key = (od_idx, transfers)
                current_count = int(existing_count.get(count_key, 0)) + int(
                    generated_count.get(count_key, 0)
                )
                if current_count >= cfg.paths_per_transfer:
                    capped_count += 1
                    continue
                nodes = destination_station + boardings * n_stations
                totals = dist[nodes] + destination_walk
                best_pos = int(np.argmin(totals))
                if not np.isfinite(totals[best_pos]):
                    continue
                destination_node = int(nodes[best_pos])
                cur = destination_node
                bundle_rev: list[int] = []
                access_station_idx = -1
                while cur != source:
                    previous = int(pred[cur])
                    if previous < 0:
                        bundle_rev = []
                        break
                    if previous == source:
                        access_station_idx = cur % n_stations
                    elif cur < source_base and previous < source_base:
                        previous_layer, previous_station = divmod(previous, n_stations)
                        current_layer, current_station = divmod(cur, n_stations)
                        if current_layer == previous_layer + 1:
                            bundle_rev.append(
                                _bundle_for_pair(
                                    previous_station * n_stations + current_station,
                                    model.bundle_keys,
                                )
                            )
                    cur = previous
                if not bundle_rev or access_station_idx < 0:
                    continue
                bundle_ids = tuple(reversed(bundle_rev))
                path_hash = np.int64(hash(bundle_ids))
                hash_key = (od_idx, transfers, path_hash)
                if hash_key in existing_hash:
                    duplicate_count += 1
                    continue
                origin_position = int(
                    np.flatnonzero(access_station[origin] == access_station_idx)[0]
                )
                origin_walk = float(access_walk[origin, origin_position])
                egress_time = float(destination_walk[best_pos])
                transfer_penalty_cost = cfg.transfer_penalty_min * transfers
                connector_cost = origin_walk + egress_time + transfer_penalty_cost
                rows.append(
                    {
                        "origin_poi_idx": origin,
                        "destination_poi_idx": destination,
                        "candidate_pair_idx": int(candidate_pair[pos]),
                        "od_idx": od_idx,
                        "boardings": boardings,
                        "transfers": transfers,
                        "bundle_ids": bundle_ids,
                        "path_rank_within_transfer": current_count + 1,
                        "path_hash": path_hash,
                        "access_station": station_ids[access_station_idx],
                        "egress_station": station_ids[int(destination_station[best_pos])],
                        "access_time_min": origin_walk,
                        "egress_time_min": egress_time,
                        "transfer_penalty_cost_min": transfer_penalty_cost,
                        "connector_cost_min": connector_cost,
                        "free_generalized_cost_min": connector_cost
                        + float(np.sum(free_bundle_cost[np.asarray(bundle_ids, dtype=np.int32)])),
                        "column_generation_cost_min": float(totals[best_pos])
                        + transfer_penalty_cost,
                    }
                )
                existing_hash.add(hash_key)
                generated_count[count_key] = generated_count.get(count_key, 0) + 1
        if completed == 1 or completed == len(origins) or completed % progress_stride == 0:
            print(
                f"column path generation: {completed}/{len(origins)} origins, new={len(rows)}",
                flush=True,
            )

    if not rows:
        return pd.DataFrame(), {
            "new_paths": 0,
            "duplicate_candidates": duplicate_count,
            "capped_candidates": capped_count,
        }
    paths = pd.DataFrame(rows)
    for column in ("origin_poi_idx", "destination_poi_idx", "candidate_pair_idx", "od_idx"):
        paths[column] = paths[column].astype(np.int32)
    for column in ("boardings", "transfers", "path_rank_within_transfer"):
        paths[column] = paths[column].astype(np.int8)
    return paths, {
        "new_paths": len(paths),
        "duplicate_candidates": duplicate_count,
        "capped_candidates": capped_count,
    }


def poi_marginals(pois, cfg):
    from ..city_demand import importance
    original = PERIOD_WEIGHTS[cfg.period]
    custom = (cfg.od_period_weights or {}).get(cfg.period)
    origin, destination = (custom['production'], custom['attraction']) if custom else original
    factor = importance(pois.class_1.fillna('其他'), cfg.poi_category_weights) if cfg.poi_category_weights is not None else np.ones(len(pois))
    count = pois.poi_count.to_numpy(float) * factor
    return (count * pois.class_1.map(origin).fillna(cfg.default_poi_weight).to_numpy(float),
            count * pois.class_1.map(destination).fillna(cfg.default_poi_weight).to_numpy(float))


def _build_demand(
    pois: pd.DataFrame, paths: pd.DataFrame, support: pd.DataFrame, cfg: ExperimentConfig
) -> pd.DataFrame:
    min_cost = paths.groupby("od_idx").free_generalized_cost_min.min()
    support = support.merge(min_cost.rename("zero_flow_cost_min"), left_on="od_idx", right_index=True)
    production_raw, attraction = poi_marginals(pois, cfg)
    support = support[(attraction[support.destination_poi_idx.to_numpy(int)] > 0) & (production_raw[support.origin_poi_idx.to_numpy(int)] > 0)].copy()
    if support.empty: raise ValueError('没有正发生及吸引权重的可达OD')
    active_origins = np.unique(support.origin_poi_idx.to_numpy(np.int32))
    production = np.zeros(len(pois), dtype=float)
    production[active_origins] = production_raw[active_origins]
    production *= cfg.total_demand_pax_per_hour / production.sum()
    o = support.origin_poi_idx.to_numpy(np.int32)
    d = support.destination_poi_idx.to_numpy(np.int32)
    friction = np.exp(-cfg.gravity_theta_per_min * support.zero_flow_cost_min.to_numpy(float))
    pair_weight = attraction[d] * friction
    denominator = np.bincount(o, weights=pair_weight, minlength=len(pois))
    demand = production[o] * pair_weight / denominator[o]
    support["origin_cluster"] = pois.iloc[o].cluster_id.astype(str).to_numpy()
    support["destination_cluster"] = pois.iloc[d].cluster_id.astype(str).to_numpy()
    support["friction"] = friction
    support["demand_pax_per_hour"] = demand
    return support


def _path_bundle_matrix(paths: pd.DataFrame, n_bundles: int) -> csr_matrix:
    counts = paths.bundle_ids.map(len).to_numpy(np.int64)
    indptr = np.empty(len(paths) + 1, dtype=np.int64)
    indptr[0] = 0
    np.cumsum(counts, out=indptr[1:])
    indices = np.fromiter(
        (int(bundle_id) for bundle_ids in paths.bundle_ids for bundle_id in bundle_ids),
        dtype=np.int32,
        count=int(indptr[-1]),
    )
    data = np.ones(len(indices), dtype=float)
    return csr_matrix((data, indices, indptr), shape=(len(paths), n_bundles))


def _route_segment_flows(
    model: PreparedModel, path_flow: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    bundle_flow = np.asarray(model.path_bundle.T @ path_flow).ravel()
    option_flow = model.option_probability * bundle_flow[model.option_bundle]
    n_segments = len(model.route_segments)
    starts = np.bincount(
        model.option_first_segment, weights=option_flow, minlength=n_segments + 1
    )
    ends = np.bincount(
        model.option_after_segment, weights=option_flow, minlength=n_segments + 1
    )
    segment_flow = np.cumsum(starts - ends)[:n_segments]
    boarding_flow = starts[:n_segments]
    return segment_flow, boarding_flow


def _bundle_costs(model: PreparedModel, path_flow: np.ndarray) -> np.ndarray:
    segment_flow, _ = _route_segment_flows(model, path_flow)
    segment_capacity = model.route_segments.capacity_per_hour.to_numpy(float)
    segment_headway = model.route_segments.headway_min.to_numpy(float)
    ratio = segment_flow / segment_capacity
    option_extra = (
        segment_headway[model.option_first_segment]
        * np.maximum(ratio[model.option_first_segment], 0.0)
        ** model.config.crowding_exponent
    )
    bundle_extra = np.bincount(
        model.option_bundle,
        weights=model.option_probability * option_extra,
        minlength=len(model.bundle_keys),
    )
    return model.bundle_base_wait + model.bundle_expected_ride + bundle_extra


def _static_network(
    base: Path,
    processed: Path,
    stations: pd.DataFrame,
    topology: pd.DataFrame,
    cfg: ExperimentConfig,
    raw_stops: Path,
    headways_path: Path,
    timedesc_path: Path | None = None,
) -> tuple[list[dict[str, object]], pd.DataFrame, dict[str, int], dict[str, np.ndarray], dict[str, np.ndarray], bool]:
    inputs = [
        processed / "bus_stations_merged.csv",
        processed / "bus_edges_topology.csv",
        processed / "bus_stopid_to_station.csv",
        headways_path,
        *sorted(raw_stops.parent.glob(f"{raw_stops.stem}.*")),
    ]
    if timedesc_path:
        inputs.append(timedesc_path)
    signature = {
        "cache_version": 5,
        "period": cfg.period,
        "bus_speed_kmh": cfg.bus_speed_kmh,
        "vehicle_capacity": cfg.vehicle_capacity,
        "fallback_headway_min": cfg.fallback_headway_min,
        "inputs": [
            (path.relative_to(base).as_posix() if path.is_relative_to(base) else path.resolve().as_posix(),
             path.stat().st_size, path.stat().st_mtime_ns)
            for path in inputs
        ],
    }
    key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:16]
    cache_dir = processed.parent / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"static_network_{key}.pkl"
    if cache_path.exists():
        with cache_path.open("rb") as stream:
            routes, route_segments, source_counts, bundles, options = pickle.load(stream)
        return routes, route_segments, source_counts, bundles, options, True

    routes, route_segments, source_counts = _build_routes(
        raw_stops, processed / "bus_stopid_to_station.csv", stations, topology,
        headways_path, timedesc_path, cfg,
    )
    bundles, options = _build_bundles(routes, len(stations))
    temporary = cache_path.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        pickle.dump((routes, route_segments, source_counts, bundles, options), stream, protocol=pickle.HIGHEST_PROTOCOL)
    temporary.replace(cache_path)
    return routes, route_segments, source_counts, bundles, options, False

def save_results(model: PreparedModel, path_flow: np.ndarray, logs: pd.DataFrame, output: Path) -> dict[str, Path]:
    if model.config.output_detail not in {"compact", "full"}:
        raise ValueError("output_detail must be 'compact' or 'full'")
    output.mkdir(parents=True, exist_ok=True)
    segment_flow, boarding_flow = _route_segment_flows(model, path_flow)
    route_results = model.route_segments.copy()
    route_results["onboard_flow_pax_per_hour"] = segment_flow
    route_results["boarding_flow_pax_per_hour"] = boarding_flow
    route_results["vc"] = segment_flow / route_results.capacity_per_hour
    route_results["capacity_deficit_pax_per_hour"] = np.maximum(
        segment_flow - route_results.capacity_per_hour.to_numpy(float), 0.0
    )
    route_results["required_frequency_per_hour"] = segment_flow / model.config.vehicle_capacity
    route_results["required_headway_min"] = np.divide(
        60.0,
        route_results.required_frequency_per_hour.to_numpy(float),
        out=np.full(len(route_results), np.inf),
        where=route_results.required_frequency_per_hour.to_numpy(float) > 0,
    )
    physical_flow = np.bincount(
        route_results.physical_edge_idx.to_numpy(np.int32), weights=segment_flow,
        minlength=len(model.topology),
    )
    physical_capacity = np.bincount(
        route_results.physical_edge_idx.to_numpy(np.int32),
        weights=route_results.capacity_per_hour.to_numpy(float), minlength=len(model.topology),
    )
    physical = model.topology.copy()
    physical["flow_pax_per_hour"] = physical_flow
    physical["capacity_pax_per_hour"] = physical_capacity
    physical["vc"] = np.divide(
        physical_flow, physical_capacity, out=np.zeros_like(physical_flow), where=physical_capacity > 0
    )
    physical["capacity_deficit_pax_per_hour"] = np.maximum(physical_flow - physical_capacity, 0.0)
    physical["capacity_feasible"] = physical["vc"] <= 1.0 + 1e-9
    transfer = (
        model.paths.groupby("transfers", as_index=False).equilibrium_path_flow.sum()
        .rename(columns={"equilibrium_path_flow": "passengers_per_hour"})
    )
    transfer["share"] = transfer.passengers_per_hour / transfer.passengers_per_hour.sum()
    files = {
        "route_segments": output / "route_segment_flows.csv",
        "physical": output / "physical_segment_flows.csv",
        "access": output / "poi_access_stations.csv",
        "pois": output / "sampled_pois.csv",
        "transfers": output / "transfer_distribution.csv",
        "iterations": output / "iteration_log.csv",
    }
    route_results.to_csv(files["route_segments"], index=False, encoding="utf-8-sig")
    physical.to_csv(files["physical"], index=False, encoding="utf-8-sig")
    if model.config.output_detail == "full":
        files["paths"] = output / "path_results.csv"
        files["od"] = output / "od_demand.csv"
        for begin in range(0, len(model.paths), 100_000):
            end = min(begin + 100_000, len(model.paths))
            path_chunk = model.paths.iloc[begin:end].copy()
            path_chunk.insert(
                path_chunk.columns.get_loc("transfers") + 1,
                "bundle_ids",
                [
                    tuple(model.path_bundle.indices[model.path_bundle.indptr[row]:model.path_bundle.indptr[row + 1]])
                    for row in range(begin, end)
                ],
            )
            path_chunk.to_csv(
                files["paths"], mode="w" if begin == 0 else "a", header=begin == 0,
                index=False, encoding="utf-8-sig" if begin == 0 else "utf-8",
            )
        model.od.to_csv(files["od"], index=False, encoding="utf-8-sig")
    model.access.to_csv(files["access"], index=False, encoding="utf-8-sig")
    model.pois.to_csv(files["pois"], index=False, encoding="utf-8-sig")
    transfer.to_csv(files["transfers"], index=False, encoding="utf-8-sig")
    logs.to_csv(files["iterations"], index=False, encoding="utf-8-sig")
    return files


def save_parameters(cfg: ExperimentConfig, output: Path) -> None:
    payload = {
        "experiment": asdict(cfg),
        "active_origin_weights": (cfg.od_period_weights or {}).get(cfg.period, {}).get("production", PERIOD_WEIGHTS[cfg.period][0]),
        "active_destination_weights": (cfg.od_period_weights or {}).get(cfg.period, {}).get("attraction", PERIOD_WEIGHTS[cfg.period][1]),
        "common_line_bundle_rule": "same boarding station and same alighting decision station",
        "common_line_split_rule": "actual route frequency share",
        "route_set_rule": (
            "congestion-driven column generation with at most paths_per_transfer "
            "routes for each transfer count from 0 to max_transfers"
        ),
        "demand_unit_definition": "total OD passenger trips per hour; transfers create additional boardings",
        "poi_cluster_weight_rule": (
            "each unique cluster is one POI; cluster member count is retained for audit but excluded from demand weights"
        ),
        "service_period_window": {
            "offpeak": "weekday 09:00-17:00",
            "pm_peak": "weekday 17:00-19:00",
        }[cfg.period],
        "capacity_interpretation": (
            "soft crowding cost; V/C above one is diagnosed but not prohibited as a hard constraint"
        ),
    }
    (output / "parameters.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
