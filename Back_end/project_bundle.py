"""Project ZIPs. Paths are DATA-relative; activation is the API caller's job."""
import hashlib
import io
import json
import re
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4

SCHEMA = 'urban-project-v1'
SAFE_SUFFIXES = {'.json', '.geojson', '.csv', '.xlsx', '.shp', '.shx', '.dbf',
                 '.prj', '.cpg', '.qix', '.tif', '.tiff', '.png'}
MAX_BYTES = 2 * 1024 ** 3
MAX_FILES = 20000


def _relative(value):
    value = str(value).replace('\\', '/')
    parts = value.split('/')
    if not value or any(p in ('', '.', '..') or ':' in p for p in parts):
        raise ValueError('Invalid DATA-relative path: ' + value)
    for part in parts:
        if part.endswith((' ', '.')) or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', part, re.I):
            raise ValueError('Unsafe filename: ' + value)
    return PurePosixPath(value).as_posix()


def _inside(root, value):
    path = root / _relative(value)
    path.resolve().relative_to(root.resolve())
    return path


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')


def _portable(value, root, missing, label='payload'):
    if isinstance(value, dict):
        return {k: _portable(v, root, missing, label + '.' + k) for k, v in value.items()}
    if isinstance(value, list):
        return [_portable(v, root, missing, label) for v in value]
    if isinstance(value, str):
        path = Path(value)
        if path.is_absolute() or PureWindowsPath(value).is_absolute():
            try:
                return path.resolve().relative_to(root).as_posix()
            except ValueError:
                missing.append({'reference': label, 'reason': 'path_outside_DATA'})
                return None
    return value


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def export_project(data, city, payload, include_inputs=False, include_results=False):
    """Return ZIP bytes + manifest; never collect, compute, activate or copy a dataset root.

    payload: name, workspace, demand_run, assignment_run, evaluation_run,
    demand_config, assignment_parameters, analysis_parameters, display_settings,
    version_references; optional dependencies is a list of DATA-relative files.
    """
    root = Path(data).resolve()
    city = dict(city) if isinstance(city, dict) else {'id': city}
    city_id = _relative(city['id'])
    if '/' in city_id:
        raise ValueError('Invalid city id')
    missing, files, references = [], {}, []
    saved = _portable(payload, root, missing)
    portable_city = _portable(city, root, missing, 'city')

    def add(path, kind, enabled):
        path = Path(path)
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except ValueError:
            missing.append({'reference': kind, 'reason': 'path_outside_DATA'})
            return
        # Source POI dependencies can refer to an entire province. Only the
        # generated city CSV is a transferable POI source, including explicit dependencies.
        declared_poi = city.get('poi_file')
        poi_dir = _inside(root, (portable_city.get('source_directory_override') or {}).get('poi_dir', 'POI'))
        if kind == 'inputs' and (relative.split('/')[0].casefold() == 'poi'
                or (declared_poi and path.resolve() == (poi_dir / declared_poi).resolve())):
            return
        if path.is_symlink():
            missing.append({'path': relative, 'reason': 'symlink_excluded'})
            return
        if path.is_dir():
            for child in sorted(path.iterdir()):
                add(child, kind, enabled)
            return
        if not path.is_file():
            missing.append({'path': relative, 'reason': 'not_found'})
            return
        if path.suffix.lower() not in SAFE_SUFFIXES:
            missing.append({'path': relative, 'reason': 'unsafe_or_unsupported_attachment'})
            return
        references.append({'path': relative, 'kind': kind})
        if not enabled:
            missing.append({'path': relative, 'reason': 'attachment_not_selected'})
            return
        content = path.read_bytes()
        if path.suffix.lower() in ('.json', '.geojson'):
            original = json.loads(content)
            portable = _portable(original, root, missing, relative)
            # Preserve bytes if possible: result signatures are never recalculated.
            if portable != original:
                content = _json(portable)
        files[relative] = (content, path.stat().st_mtime_ns)

    overrides = portable_city.get('source_directory_override') or {}
    bus = _inside(root, overrides.get('bus_dir', 'CPTOND-2025/dataset/bus/shapefiles'))
    input_spec = None
    if include_inputs:
        from .project_inputs import export_inputs
        try:
            portable_files, input_spec = export_inputs(root, city, bus)
            for relative, content in portable_files.items():
                files[relative] = (content, None)
                references.append({'path': relative, 'kind': 'inputs'})
        except (OSError, ValueError, KeyError) as error:
            missing.append({'reference': 'project_inputs', 'reason': 'portable_inputs_unavailable',
                            'message': str(error)})
    for folder in ('processed', 'population', 'evaluation_inputs'):
        path = root / folder / city_id
        if path.exists():
            add(path, 'inputs', include_inputs)

    workspace = saved.get('workspace') or {}
    for kind, folder, key in [('demand', 'demand_runs', 'demand_id'),
                               ('assignment', 'runs', 'assignment_id'),
                               ('evaluation', 'evaluation_runs', 'evaluation_id')]:
        run = saved.get(kind + '_run') or {}
        identity = run.get('run_id') or workspace.get(key)
        if not identity:
            continue
        identity = _relative(identity)
        if '/' in identity:
            raise ValueError('Invalid run id')
        source = root / folder / city_id / identity
        add(source, 'results', include_results)
        summary = source / 'summary.json'
        if summary.is_file():
            metadata = json.loads(summary.read_text(encoding='utf-8'))
            demand_id = metadata.get('demand_run_id')
            if demand_id:
                demand_id = _relative(demand_id)
                if '/' in demand_id:
                    raise ValueError('Invalid demand run id')
                add(root / 'demand_runs' / city_id / demand_id, 'results', include_results)
            dependencies = [metadata.get('source_poi')]
            dependencies += list((metadata.get('source_files') or {}).values())
            for dependency in dependencies:
                if dependency:
                    add(_inside(root, dependency), 'inputs', include_inputs)
            if kind != 'demand' and not metadata.get('source_files'):
                missing.append({'reference': kind + ':' + identity, 'reason': 'source_provenance_incomplete'})
    for dependency in saved.get('dependencies', []):
        path = _inside(root, dependency)
        # Explicit dependencies are files, never a route to recursively copy DATA.
        if not path.is_file():
            missing.append({'path': dependency, 'reason': 'dependency_file_not_found'})
        else:
            add(path, 'inputs', include_inputs)

    manifest = {'schema': SCHEMA, 'project_id': uuid4().hex,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'city': portable_city, 'payload': saved,
                'include_inputs': bool(include_inputs), 'include_results': bool(include_results),
                'project_inputs': input_spec,
                'references': references, 'missing_dependencies': missing,
                'files': [{'path': name, 'sha256': _sha(content), 'size': len(content), 'mtime_ns': mtime}
                          for name, (content, mtime) in sorted(files.items())],
                'limitations': ['Pickle/checkpoints are not transferable.',
                                'Historical signatures remain strict; portable files do not certify resumability.']}
    if len(files) > MAX_FILES or sum(len(content) for content, _ in files.values()) > MAX_BYTES:
        raise ValueError('Project attachments exceed import limits')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('project.json', _json(manifest))
        for name, (content, _) in files.items():
            archive.writestr('data/' + name, content)
    return {'content': buffer.getvalue(), 'filename': city_id + '_project.zip',
            'manifest': manifest, 'missing_dependencies': missing}


def import_project(data, content):
    try:
        return _import_project(data, content)
    except (zipfile.BadZipFile, KeyError, TypeError, AttributeError) as error:
        raise ValueError('工程文件损坏、格式错误或缺少必需清单') from error


def _import_project(data, content):
    """Stage raw ZIP bytes under projects/<new id>/data; no activation or pickle loading.

    API must scope file reads to returned data_root for this manually opened project.
    Do not merge attachments into global DATA or silently activate custom-input versions.
    """
    import os

    if not isinstance(content, bytes) or len(content) > MAX_BYTES:
        raise ValueError('Invalid or oversized project ZIP')
    root = Path(data).resolve()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES + 1 or sum(info.file_size for info in infos) > MAX_BYTES:
            raise ValueError('Expanded project exceeds limits')
        seen = set()
        for info in infos:
            name = _relative(info.filename)
            if name.casefold() in seen or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                raise ValueError('Duplicate, linked or encrypted project member')
            seen.add(name.casefold())
            if name != 'project.json' and (not name.startswith('data/') or Path(name).suffix.lower() not in SAFE_SUFFIXES):
                raise ValueError('Unsupported project attachment: ' + name)
        manifest = json.loads(archive.read('project.json'))
        if not isinstance(manifest,dict) or not isinstance(manifest.get('city'),dict) or not isinstance(manifest.get('files'),list):
            raise ValueError('工程清单格式错误')
        if manifest.get('schema') != SCHEMA:
            raise ValueError('Unsupported project schema')
        city_id = _relative(manifest['city']['id'])
        if '/' in city_id:
            raise ValueError('Invalid city id')
        if not isinstance(manifest.get('payload'), dict):
            raise ValueError('Invalid project payload')
        missing = list(manifest.get('missing_dependencies', []))
        portable = _portable(manifest, root, [], 'project')
        if portable != manifest:
            raise ValueError('Project contains nonportable absolute paths')
        entries = manifest.get('files', [])
        expected = {'project.json'}
        validated = []
        for entry in entries:
            relative = _relative(entry['path'])
            name = 'data/' + relative
            if name in expected:
                raise ValueError('Duplicate manifest attachment')
            expected.add(name)
            raw = archive.read(name)
            if len(raw) != entry['size'] or _sha(raw) != entry['sha256']:
                raise ValueError('Project attachment checksum mismatch: ' + relative)
            if Path(relative).suffix.lower() in ('.json', '.geojson'):
                obj = json.loads(raw)
                if _portable(obj, root, [], relative) != obj:
                    raise ValueError('Attachment contains absolute paths: ' + relative)
            validated.append((relative, raw, entry.get('mtime_ns')))
        if expected != {info.filename for info in infos}:
            raise ValueError('ZIP and project manifest differ')
        identity = uuid4().hex
        namespace = _inside(root, 'projects/' + identity)
        namespace.mkdir(parents=True, exist_ok=False)
        scoped = namespace / 'data'
        scoped.mkdir()
        for relative, raw, mtime in validated:
            path = _inside(scoped, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as target:
                target.write(raw)
            if isinstance(mtime, int) and mtime >= 0:
                os.utime(path, ns=(mtime, mtime))
        result = {'project_id': identity, 'city': manifest['city'], 'payload': manifest['payload'],
                  'data_root': scoped.relative_to(root).as_posix(),
                  'missing_dependencies': missing, 'manifest': manifest,
                  'restore_status': 'partial' if missing else 'attachments_restored',
                  'requires_signature_validation': True, 'can_resume_checkpoint': False}
        with (namespace / 'project.json').open('xb') as target:
            target.write(_json(result))
        return result


def list_projects(data, city):
    city_id = city['id'] if isinstance(city, dict) else city
    projects = []
    for path in sorted((Path(data) / 'projects').glob('*/project.json'), reverse=True):
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved['city']['id'] == city_id:
            projects.append(saved)
    return {'projects': projects}


def restore_references(data, project_id):
    """Explicitly install versioned result attachments; never activate shared inputs.

    Returns the saved project plus restored_references and restore_warnings.
    A conflicting or damaged attachment skips its entire run directory.
    """
    import shutil

    if not isinstance(project_id, str) or not re.fullmatch(r'[0-9a-f]{32}', project_id):
        raise ValueError('Invalid project id')
    root = Path(data).resolve()
    namespace = _inside(root, 'projects/' + project_id)
    project = json.loads((namespace / 'project.json').read_text(encoding='utf-8'))
    scoped = namespace / 'data'
    city_id = _relative(project['city']['id'])
    if '/' in city_id:
        raise ValueError('Invalid project city id')
    groups = {}
    warnings = []
    for entry in project['manifest'].get('files', []):
        relative = _relative(entry['path'])
        parts = PurePosixPath(relative).parts
        if (len(parts) < 4 or parts[0] not in ('runs', 'demand_runs', 'evaluation_runs')
                or parts[1] != city_id
                or not re.fullmatch(r'[0-9]{8}_[0-9]{6}_[0-9a-f]{8}', parts[2])):
            continue
        group = '/'.join(parts[:3])
        groups.setdefault(group, []).append((relative, entry))

    restored = []
    for group, entries in sorted(groups.items()):
        conflicts, damaged, candidates = [], [], []
        # Inspect every file before creating anything in this version directory.
        for relative, entry in entries:
            try:
                source = _inside(scoped, relative)
                target = _inside(root, relative)
                if (source.is_symlink() or not source.is_file()
                        or source.suffix.lower() not in SAFE_SUFFIXES
                        or 'checkpoints' in PurePosixPath(relative).parts[3:]
                        or source.stat().st_size != entry['size']
                        or _sha(source.read_bytes()) != entry['sha256']):
                    damaged.append(relative)
                    continue
                if target.exists() or target.is_symlink():
                    if (target.is_symlink() or not target.is_file()
                            or _sha(target.read_bytes()) != entry['sha256']):
                        conflicts.append(relative)
                for parent in target.parents:
                    if parent == root:
                        break
                    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                        conflicts.append(parent.relative_to(root).as_posix())
                candidates.append((relative, source, target))
            except (OSError, ValueError):
                damaged.append(relative)
        if conflicts or damaged:
            warnings.append({'reference': group, 'reason': 'group_skipped',
                             'conflicts': sorted(set(conflicts)),
                             'invalid_attachments': sorted(set(damaged))})
            continue
        copied, existing, failed = [], [], []
        for relative, source, target in candidates:
            if target.is_file():
                existing.append(relative)
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                # Exclusive creation prevents overwriting a file added after preflight.
                # copyfileobj + copystat preserves copy2 semantics without overwrite.
                with target.open('xb') as destination, source.open('rb') as origin:
                    shutil.copyfileobj(origin, destination)
                shutil.copystat(source, target)
                copied.append(relative)
            except FileExistsError:
                if target.is_file() and _sha(target.read_bytes()) == _sha(source.read_bytes()):
                    existing.append(relative)
                else:
                    failed.append(relative)
                    break
            except OSError:
                failed.append(relative)
                break
        if failed:
            warnings.append({'reference': group, 'reason': 'copy_interrupted',
                             'files': failed, 'copied_files': copied,
                             'message': 'Existing files were not overwritten; this group is not fully restored.'})
            continue
        restored.append({'kind': group.split('/')[0], 'city_id': city_id,
                         'run_id': group.split('/')[2], 'path': group,
                         'copied_files': copied, 'existing_files': existing})
    if not groups:
        warnings.append({'reference': project_id, 'reason': 'no_versioned_result_attachments'})
    return {**project, 'restored_references': restored, 'restore_warnings': warnings}


def restore_project(data, project_id, *, restore_inputs=False, original_city=None):
    """Explicit project opening; default preserves result-only restore behavior.

    API supplies its existing city record and updates its live registry from city.
    Input activation is opt-in, always imports a new version, and never replaces
    existing source datasets or relaxes historical result signatures.
    """
    if original_city is not None:
        if not isinstance(project_id,str) or not re.fullmatch(r'[0-9a-f]{32}',project_id):raise ValueError('Invalid project id')
        saved=json.loads((Path(data)/'projects'/project_id/'project.json').read_text(encoding='utf-8'))
        if original_city['id']!=saved['city']['id']:raise ValueError('Original city does not match project city')
    project = restore_references(data, project_id)
    project.update(input_status='not_requested', city_status='unchanged')
    if original_city is not None:
        if original_city['id'] != project['city']['id']:
            raise ValueError('Original city does not match project city')
        project['city'] = dict(original_city)
    if not restore_inputs:
        return project
    from .project_inputs import restore_inputs as restore_city_inputs
    try:
        restored = restore_city_inputs(data, project, original_city)
    except (OSError, ValueError, KeyError, TypeError) as error:
        project['input_status'] = 'failed'
        project['restore_warnings'].append({'reference': project_id,
            'reason': 'input_restore_failed', 'message': str(error)})
        return project
    warnings = project['restore_warnings'] + restored.pop('restore_warnings')
    project.update(restored, restore_warnings=warnings)
    return project