"""Select recorded scenarios without filling historical gaps with current defaults."""

REQUIRED=('city_id','demand_run_id','shared_demand_signature','poi_count','algorithm','headway_basis','default_headway_min')

def matching_runs(reference,history,mode='strict'):
    if mode not in ('strict','scenario'):raise ValueError('比较口径应为strict或scenario')
    if any(reference.get(key) is None for key in REQUIRED) or not reference.get('parameters'):
        return [reference]
    keys=REQUIRED+('headway_upload_sha256',)+(('hourly_demand_pax',) if mode=='strict' else ())
    def parameters(row):
        return {key:value for key,value in (row.get('parameters') or {}).items()
                if key!='period' and not (mode=='scenario' and key=='total_demand_pax_per_hour')}
    expected=parameters(reference)
    return [row for row in history if all(row.get(key)==reference.get(key) for key in keys) and parameters(row)==expected]
