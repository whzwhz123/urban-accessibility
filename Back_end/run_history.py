"""Read recorded experiment settings without inventing historical defaults."""
import json
from pathlib import Path
from dataclasses import fields
from .assignment.core import ExperimentConfig


def recorded_run(folder):
    folder = Path(folder)
    summary = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
    snapshot = folder / 'parameters.json'
    saved = json.loads(snapshot.read_text(encoding='utf-8')) if snapshot.is_file() else {}
    original = summary.get('parameters') or {}
    config_fields = {field.name for field in fields(ExperimentConfig)}
    values = {key: value for key, value in saved.items() if key in config_fields and value is not None}
    values.update({key: value for key, value in (summary.get('parameters') or {}).items() if value is not None})
    summary['parameters'] = values
    summary['parameter_sources'] = {key: 'summary.json' if original.get(key) is not None else 'parameters.json' for key in values}
    # Summary is authoritative; snapshots only fill missing fields that were actually recorded.
    for key, source in [('period', 'period'), ('poi_count', 'sample_size'),
                        ('hourly_demand_pax', 'total_demand_pax_per_hour')]:
        if summary.get(key) is None and source in values:
            summary[key] = values[source]
    for key in ('headway_basis', 'default_headway_min', 'assumed_headway_min', 'headway_source_counts', 'headway_default_share', 'headway_upload_sha256'):
        if summary.get(key) is None and saved.get(key) is not None:
            summary[key] = saved[key]
    return summary
