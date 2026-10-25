from __future__ import annotations
import hashlib
import json
import os
import pickle
from pathlib import Path
from threading import RLock
from uuid import uuid4
from datetime import datetime, timezone

class TaskCancelled(RuntimeError):
    """Cooperative stop; latest committed checkpoint stays resumable."""

def check_cancel(cancel_check):
    if cancel_check is not None and cancel_check():
        raise TaskCancelled('Task cancelled at a safe boundary')

def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            preserved = path.parent / 'interrupted_writes'
            preserved.mkdir(exist_ok=True)
            temporary.replace(preserved / temporary.name)

def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'))

def content_signature(options, files=()):
    digest = hashlib.sha256(json.dumps(options, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    for name, path in sorted(files, key=lambda item: item[0]):
        digest.update(name.encode('utf-8'))
        path = Path(path)
        if not path.is_file():
            digest.update(b'<missing>')
            continue
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
    return digest.hexdigest()

class TaskStore:
    """Local JSON per task. Share one instance between API threads."""
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
    def _path(self, task_id):
        if not task_id or Path(task_id).name != task_id or task_id in ('.', '..'):
            raise ValueError('Invalid task id')
        return self.root / (task_id + '.json')
    def create(self, options, *, task_id=None, kind='assignment'):
        task_id = task_id or uuid4().hex
        with self.lock:
            if self._path(task_id).exists():
                raise ValueError('Task already exists')
            return self.update(task_id, kind=kind, options=options, status='pending', cancel_requested=False)
    def get(self, task_id):
        with self.lock:
            return json.loads(self._path(task_id).read_text(encoding='utf-8'))
    def list(self):
        with self.lock:
            return [json.loads(p.read_text(encoding='utf-8')) for p in sorted(self.root.glob('*.json'))]
    def update(self, task_id, **fields):
        with self.lock:
            path = self._path(task_id)
            record = self.get(task_id) if path.exists() else {}
            record.update(fields)
            record['task_id'] = task_id
            record['updated_at'] = datetime.now(timezone.utc).isoformat()
            write_json(path, record)
            return record
    def request_cancel(self, task_id):
        return self.update(task_id, cancel_requested=True)
    def cancel_check(self, task_id):
        return lambda: bool(self.get(task_id).get('cancel_requested'))

class CheckpointStore:
    """Trusted LOCAL pickle only; SHA256 detects corruption, not malicious input.

    Full model snapshots change only at stage/path-structure boundaries.
    Iteration snapshots contain small solver state and reference that model.
    """
    def __init__(self, root, signature):
        self.root = Path(root)
        self.signature = signature
        self._model_structure = None
        self._model_reference = None

    def _write_pickle(self, prefix, value):
        filename = prefix + '_' + uuid4().hex + '.pickle'
        payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        atomic_write(self.root / filename, payload)
        return {'file': filename, 'sha256': hashlib.sha256(payload).hexdigest()}

    def _read_pickle(self, reference):
        filename = reference['file']
        if Path(filename).name != filename:
            raise ValueError('Invalid checkpoint filename')
        payload = (self.root / filename).read_bytes()
        if hashlib.sha256(payload).hexdigest() != reference['sha256']:
            raise ValueError('Checkpoint checksum mismatch')
        return pickle.loads(payload)

    def save(self, value):
        self.root.mkdir(parents=True, exist_ok=True)
        state = dict(value)
        model = state.pop('model', None)
        if state.get('solver') is not None:
            state['solver'] = dict(state['solver'])
            state['solver'].pop('model', None)
        phase = state.get('phase')
        if model is not None:
            structure = self._model_structure
            if structure is None or structure[0] is not model.paths or structure[1] is not model.path_bundle or structure[2] != phase:
                self._model_reference = self._write_pickle('model', model)
                self._model_structure = (model.paths, model.path_bundle, phase)
        reference = self._write_pickle('state', state)
        write_json(self.root / 'latest.json', dict(**reference, model=self._model_reference,
                   signature=self.signature, format_version=2, trusted_local_pickle_only=True))

    def load(self):
        manifest = json.loads((self.root / 'latest.json').read_text(encoding='utf-8'))
        if manifest['signature'] != self.signature or manifest.get('format_version') != 2:
            raise ValueError('Checkpoint inputs or code version changed')
        state = self._read_pickle(manifest)
        reference = manifest.get('model')
        if reference:
            model = self._read_pickle(reference)
            state['model'] = model
            if state.get('solver') is not None:
                state['solver']['model'] = model
            self._model_reference = reference
            self._model_structure = (model.paths, model.path_bundle, state.get('phase'))
        return state


def assignment_preflight(poi_count, parameters=None, model=None):
    parameters = parameters or {}
    pairs = int(poi_count) * max(0, int(poi_count) - 1)
    paths = pairs * (int(parameters.get('max_transfers', 2)) + 1) * int(parameters.get('paths_per_transfer', 3))
    result = {'upper_bounds': {'ordered_od_pairs': pairs, 'candidate_paths': paths, 'path_flow_float64_bytes': paths * 8},
        'notes': 'Before distance/reachability filters. Flow bytes exclude matrices/model. No runtime estimate.'}
    if model is not None:
        from .assignment.operators import ActiveRouteSectionCapacityOperator
        operator = ActiveRouteSectionCapacityOperator(model)
        matrix = model.path_bundle
        result['measured'] = {'od_pairs': len(model.od), 'paths': len(model.paths), 'path_bundle_nnz': matrix.nnz,
            'path_bundle_bytes': matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes,
            'active_route_sections': len(operator.active_segment_ids), 'expanded_option_segment_entries': operator.expanded_option_segment_entries,
            'capacity_expansion_limit': 20_000_000, 'capacity_expansion_within_limit': operator.expanded_option_segment_entries <= 20_000_000}
    return result
