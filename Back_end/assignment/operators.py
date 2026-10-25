from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix

from .hardcap_operator import RouteSectionCapacityOperator
from .core import PreparedModel


@dataclass(frozen=True)
class ActiveOperatorStats:
    all_bundles: int
    active_bundles: int
    all_options: int
    active_options: int
    all_route_segments: int
    active_route_segments: int
    expanded_option_segment_entries: int


class ActiveRouteSectionCapacityOperator(RouteSectionCapacityOperator):
    """Path-route-section operator restricted to elements used by current paths."""

    def __init__(self, model: PreparedModel):
        super().__init__(model)
        self.full_capacity = self.capacity.copy()
        self.n_full_segments = len(self.full_capacity)

        self.active_bundle_ids = np.unique(model.path_bundle.indices).astype(np.int32)
        self.active_path_bundle = model.path_bundle[:, self.active_bundle_ids].tocsr()
        bundle_map = np.full(len(model.bundle_keys), -1, dtype=np.int32)
        bundle_map[self.active_bundle_ids] = np.arange(
            len(self.active_bundle_ids), dtype=np.int32
        )
        mapped_option_bundle = bundle_map[model.option_bundle]
        option_mask = mapped_option_bundle >= 0
        self.option_bundle = mapped_option_bundle[option_mask]
        self.option_probability = model.option_probability[option_mask]
        self.option_first_segment = model.option_first_segment[option_mask]
        self.option_after_segment = model.option_after_segment[option_mask]

        coverage_delta = np.zeros(self.n_full_segments + 1, dtype=np.int32)
        np.add.at(coverage_delta, self.option_first_segment, 1)
        np.add.at(coverage_delta, self.option_after_segment, -1)
        self.active_segment_ids = np.flatnonzero(
            np.cumsum(coverage_delta[:-1]) > 0
        ).astype(np.int32)
        self.segment_map = np.full(self.n_full_segments, -1, dtype=np.int32)
        self.segment_map[self.active_segment_ids] = np.arange(
            len(self.active_segment_ids), dtype=np.int32
        )
        self.capacity = self.full_capacity[self.active_segment_ids]
        self.expanded_option_segment_entries = int(
            np.sum(self.option_after_segment - self.option_first_segment)
        )

    @property
    def stats(self) -> ActiveOperatorStats:
        return ActiveOperatorStats(
            all_bundles=len(self.model.bundle_keys),
            active_bundles=len(self.active_bundle_ids),
            all_options=len(self.model.option_bundle),
            active_options=len(self.option_bundle),
            all_route_segments=self.n_full_segments,
            active_route_segments=len(self.active_segment_ids),
            expanded_option_segment_entries=self.expanded_option_segment_entries,
        )

    def forward(self, path_flow: np.ndarray) -> np.ndarray:
        bundle_flow = np.asarray(self.active_path_bundle.T @ path_flow).ravel()
        option_flow = self.option_probability * bundle_flow[self.option_bundle]
        starts = np.bincount(
            self.option_first_segment,
            weights=option_flow,
            minlength=self.n_full_segments + 1,
        )
        ends = np.bincount(
            self.option_after_segment,
            weights=option_flow,
            minlength=self.n_full_segments + 1,
        )
        full_flow = np.cumsum(starts - ends)[: self.n_full_segments]
        return full_flow[self.active_segment_ids]

    def adjoint(self, segment_value: np.ndarray) -> np.ndarray:
        values = np.asarray(segment_value, dtype=float)
        if len(values) != len(self.active_segment_ids):
            raise ValueError("segment_value length does not match active route segments")
        full_values = np.zeros(self.n_full_segments, dtype=float)
        full_values[self.active_segment_ids] = values
        prefix = np.empty(self.n_full_segments + 1, dtype=float)
        prefix[0] = 0.0
        np.cumsum(full_values, out=prefix[1:])
        option_value = (
            prefix[self.option_after_segment] - prefix[self.option_first_segment]
        )
        bundle_value = np.bincount(
            self.option_bundle,
            weights=self.option_probability * option_value,
            minlength=len(self.active_bundle_ids),
        )
        return np.asarray(self.active_path_bundle @ bundle_value).ravel()

    def expand_segment_vector(self, active_values: np.ndarray) -> np.ndarray:
        values = np.asarray(active_values, dtype=float)
        if len(values) != len(self.active_segment_ids):
            raise ValueError("active_values length does not match active route segments")
        full = np.zeros(self.n_full_segments, dtype=float)
        full[self.active_segment_ids] = values
        return full

    def build_path_segment_matrix(
        self, max_expanded_entries: int = 20_000_000
    ) -> csr_matrix:
        """Build expected path-by-active-route-segment incidence for HiGHS."""
        total = self.expanded_option_segment_entries
        if total > max_expanded_entries:
            raise MemoryError(
                f"Explicit matrix needs {total:,} option-segment entries; "
                f"limit is {max_expanded_entries:,}"
            )
        lengths = (self.option_after_segment - self.option_first_segment).astype(
            np.int64
        )
        option_rows = np.repeat(self.option_bundle, lengths)
        starts = np.repeat(self.option_first_segment, lengths)
        offsets = np.arange(total, dtype=np.int64) - np.repeat(
            np.cumsum(np.r_[0, lengths[:-1]], dtype=np.int64), lengths
        )
        full_segments = starts + offsets
        active_segments = self.segment_map[full_segments]
        if np.any(active_segments < 0):
            raise RuntimeError("Active segment mapping is incomplete")
        data = np.repeat(self.option_probability, lengths)
        bundle_segment = coo_matrix(
            (data, (option_rows, active_segments)),
            shape=(len(self.active_bundle_ids), len(self.active_segment_ids)),
        ).tocsr()
        bundle_segment.sum_duplicates()
        path_segment = (self.active_path_bundle @ bundle_segment).tocsr()
        path_segment.sum_duplicates()
        path_segment.eliminate_zeros()
        return path_segment
