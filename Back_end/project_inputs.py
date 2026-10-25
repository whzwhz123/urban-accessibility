"""Portable city source inputs; never restore processed caches as fresh inputs."""
import csv
import io
import json
import zipfile
from pathlib import Path


def export_inputs(root, city, bus):
    import geopandas as gpd

    folder = 'project_inputs/' + city['id']
    files = {}
    source = root / 'processed' / city['id'] / 'poi_clustered.csv'
    source.resolve().relative_to(root.resolve())
    output = io.StringIO(newline='')
    fields = ['id', 'name', 'class_1', 'class_2', 'lon', 'lat']
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    seen = set()
    with source.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not {'cluster_id', 'name', 'class_1', 'lon', 'lat'} <= set(reader.fieldnames or []):
            raise ValueError('Processed POI lacks portable source fields')
        for row in reader:
            # Cluster IDs are unique even when provincial source IDs are empty/repeated.
            identity = row['cluster_id']
            if not identity or identity in seen:
                raise ValueError('Processed POI cluster IDs must be nonempty and unique')
            seen.add(identity)
            writer.writerow({**{key: row.get(key, '') for key in fields}, 'id': identity})
    if not seen:
        raise ValueError('Processed city POI is empty')
    files[folder + '/poi.csv'] = output.getvalue().encode('utf-8-sig')
    for kind in ('stops', 'segments', 'routes'):
        paths = list((bus / city['id']).glob('*_bus_' + kind + '.shp'))
        if len(paths) != 1:
            raise ValueError('City network requires one source layer: ' + kind)
        paths[0].resolve().relative_to(root.resolve())
        frame = gpd.read_file(paths[0])
        if frame.crs is None:
            raise ValueError('City network source CRS is missing: ' + kind)
        files[folder + '/' + kind + '.geojson'] = frame.to_crs(4326).to_json().encode('utf-8')
    # Enforce the existing importer limits already at export, not after migration.
    from .custom_inputs import MAX_UPLOAD_BYTES, MAX_EXPANDED_BYTES
    if sum(map(len, files.values())) > MAX_EXPANDED_BYTES:
        raise ValueError('Portable inputs exceed the 512 MiB expanded package limit')
    package = io.BytesIO()
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(Path(name).name, raw)
    if package.tell() > MAX_UPLOAD_BYTES:
        raise ValueError('Portable inputs exceed the 100 MiB compressed package limit')
    return files, {'schema': 1, 'coordinate_system': 'WGS84',
                   'files': list(files), 'poi_source': source.relative_to(root).as_posix(),
                   'poi_id_field': 'cluster_id'}


def restore_inputs(data, project, original_city=None):
    from .custom_inputs import import_package
    from .input_versions import activate
    from .registered_cities import register, registered
    from .project_bundle import _inside, _sha

    root = Path(data).resolve()
    city_id = project['city']['id']
    if original_city is not None and original_city['id'] != city_id:
        raise ValueError('Original city does not match project city')
    records = {city['id']: city for city in registered(root)}
    pointer = root / 'custom_inputs' / city_id / 'active.json'
    existing = original_city or records.get(city_id)
    if existing is None and pointer.is_file():
        existing = json.loads(pointer.read_text(encoding='utf-8'))['city']
    # A caller with a national catalog should supply its live city record.
    if existing is None and (root / 'CPTOND-2025/dataset/bus/shapefiles' / city_id).exists():
        raise ValueError('Existing national city requires original_city from the API registry')
    city = dict(existing or project['city'])
    # Pending service data belongs to this project, never to a previous version.
    city.pop('pending_service_csv', None)
    city.pop('pending_service_provenance', None)
    if existing is None:
        # Reuse registration validation; no national files/catalog are required.
        city = {key: city[key] for key in ('id', 'name', 'province', 'city_code', 'center')}
    spec = project['manifest'].get('project_inputs')
    if not spec or spec.get('schema') != 1 or spec.get('coordinate_system') != 'WGS84':
        raise ValueError('Project has no portable city input package; export it again with inputs')
    entries = {entry['path']: entry for entry in project['manifest']['files']}
    expected = {'poi.csv', 'stops.geojson', 'segments.geojson', 'routes.geojson'}
    names = set()
    buffer = io.BytesIO()
    scoped = _inside(root, project['data_root'])
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for relative in spec['files']:
            if relative not in entries or not relative.startswith('project_inputs/' + city_id + '/'):
                raise ValueError('Invalid portable input attachment')
            name = Path(relative).name
            if name not in expected or name in names:
                raise ValueError('Duplicate or unsupported portable input')
            names.add(name)
            source = _inside(scoped, relative)
            raw = source.read_bytes()
            entry = entries[relative]
            if source.is_symlink() or len(raw) != entry['size'] or _sha(raw) != entry['sha256']:
                raise ValueError('Portable input checksum mismatch: ' + relative)
            archive.writestr(name, raw)
    if names != expected:
        raise ValueError('Portable city inputs are incomplete')
    pending_service = {}
    service_csv = 'processed/' + city_id + '/service/bus_headways_by_period.csv'
    if service_csv in entries:
        def verified_attachment(relative):
            source = _inside(scoped, relative)
            raw = source.read_bytes()
            entry = entries[relative]
            if source.is_symlink() or len(raw) != entry['size'] or _sha(raw) != entry['sha256']:
                raise ValueError('Service attachment checksum mismatch: ' + relative)
            return source, raw

        service_path, _ = verified_attachment(service_csv)
        pending_service['pending_service_csv'] = service_path.relative_to(root).as_posix()
        service_manifest = 'processed/' + city_id + '/service/service_manifest.json'
        if service_manifest in entries:
            _, raw = verified_attachment(service_manifest)
            provenance = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(provenance, dict):
                raise ValueError('Service manifest must be a JSON object')

            def without_credentials(value):
                if isinstance(value, dict):
                    # Retain source-kind/summary metadata, not authentication material.
                    sensitive = ('credential', 'password', 'passwd', 'secret', 'token',
                                 'apikey', 'accesskey', 'authorization', 'cookie',
                                 'privatekey', 'clientkey')
                    return {key: without_credentials(item) for key, item in value.items()
                            if key.lower().replace('_', '').replace('-', '') not in
                            ('key', 'auth', 'headers')
                            and not any(part in key.lower().replace('_', '').replace('-', '')
                                        for part in sensitive)}
                if isinstance(value, list):
                    return [without_credentials(item) for item in value]
                return value

            pending_service['pending_service_provenance'] = without_credentials(provenance)
    imported = import_package(city, root, buffer.getvalue(), 'project_inputs.zip', 'WGS84')
    if pending_service:
        # activate reads manifest.city; updating only the returned city loses these fields.
        manifest_path = Path(imported['manifest'])
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['city'].update(pending_service)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    if existing is None:
        center = city['center']
        city = register(root, {**city, 'longitude': center[0], 'latitude': center[1]}, records)
    # activate retains the previous pointer in activation_history and all source versions.
    restored = activate(root, city, imported['version'])
    return {'city': restored, 'input_status': 'restored', 'input_version': imported['version'],
            'city_status': 'existing' if existing is not None else 'registered',
            'restore_warnings': [{'reference': city_id, 'reason': 'inputs_require_preparation',
                                  'message': 'Restored inputs are a new version; prepare inputs before solving. Historical signatures are unchanged.'}]
                                + [{'reference': city_id, 'reason': 'input_import_warning', 'message': message}
                                   for message in imported.get('warnings', [])]}
