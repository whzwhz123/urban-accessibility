export const COMPARISON_PERIODS = ['am_peak','offpeak','pm_peak'];
const keys = ['city_id','demand_run_id','shared_demand_signature','poi_count','hourly_demand_pax','algorithm','headway_basis','default_headway_min'];
function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])]));
  return value;
}
export function matchingPeriods(reference, runs) {
  if (!reference || keys.some(key => reference[key] == null) || !reference.parameters) return {};
  const parameters = value => JSON.stringify(stable(Object.fromEntries(Object.entries(value || {}).filter(([key]) => key !== 'period'))));
  const same = row => keys.every(key => row[key] === reference[key]) && row.headway_upload_sha256 === reference.headway_upload_sha256 && parameters(row.parameters) === parameters(reference.parameters);
  return Object.fromEntries(COMPARISON_PERIODS.map(period => [period, period === reference.period ? reference : runs.find(row => row.period === period && same(row)) || null]));
}
export function comparisonCsv(group) {
  const fields = ['period','run_id','poi_count','hourly_demand_pax','default_headway_min','candidate_paths','total_seconds','relative_gap','converged','served_demand_share','unserved_demand_pax_per_hour','demand_run_id'];
  const cell = value => '"'+String(value ?? '').replace(/"/g,'""')+'"';
  return '\ufeff'+[fields,...COMPARISON_PERIODS.map(period => fields.map(key => group[period]?.[key] ?? ''))].map(row => row.map(cell).join(',')).join('\r\n');
}
