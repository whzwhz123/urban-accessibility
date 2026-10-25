export function compositeDisplay(data) {
  const groups = new Map();
  for (const feature of data.features || []) {
    const key = JSON.stringify(feature.geometry);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(feature);
  }
  let conflictCells = 0;
  const features = Array.from(groups.values(), records => {
    const properties = { ...records[0].properties };
    const scores = records.map(record => Number(record.properties.composite)).filter(Number.isFinite);
    const low = Math.min(...scores), high = Math.max(...scores);
    if (high - low > 1e-8) conflictCells++;
    for (const field of ['composite', 'd_norm', 'c_norm', 'i_norm', 'supply']) {
      const values = records.map(record => record.properties[field]).filter(value => value != null && Number.isFinite(Number(value))).map(Number);
      properties[field] = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
    }
    properties.source_records = records.length;
    properties.composite_min = low;
    properties.composite_max = high;
    return { ...records[0], properties };
  });
  return { ...data, features, diagnostics: { original_records: data.features.length, cells: features.length, conflict_cells: conflictCells } };
}
