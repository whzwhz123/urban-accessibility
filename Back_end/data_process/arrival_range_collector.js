(async function () {
  const config = __ARRIVAL_CONFIG__;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const results = new Map();
  let stopped = false;
  let database;
  const completed = new Set(config.completed_keys || []);
  const statistics = { requests: 0, cache_hits: 0 };
  if (typeof AMap === 'undefined') throw new Error('Open the official AMap ArrivalRange demo before running this script.');
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('ArrivalRange plugin load timeout')), config.timeout_ms);
    if (AMap.ArrivalRange) { clearTimeout(timer); resolve(); }
    else AMap.plugin('AMap.ArrivalRange', () => { clearTimeout(timer); resolve(); });
  });
  try {
    database = await new Promise((resolve, reject) => {
      const request = indexedDB.open('urban-arrival-range-v1', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('records', { keyPath: 'id' });
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } catch (error) { console.warn('Persistent cache unavailable; export before closing this page.', error); }
  function storage(mode, operation) {
    if (!database) return Promise.resolve(null);
    return new Promise((resolve, reject) => {
      const transaction = database.transaction('records', mode);
      const request = operation(transaction.objectStore('records'));
      transaction.oncomplete = () => resolve(request.result);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  }
  function point(value) {
    if (Array.isArray(value) && value.length === 2 && value.every(v => (typeof v === 'number' || typeof v === 'string') && v !== '' && Number.isFinite(Number(v)))) return value.map(Number);
    if (value && typeof value.getLng === 'function') return [value.getLng(), value.getLat()];
    if (value && Number.isFinite(value.lng) && Number.isFinite(value.lat)) return [value.lng, value.lat];
    return null;
  }
  function coordinates(value) {
    const pair = point(value);
    if (pair) return pair;
    if (Array.isArray(value)) return value.map(coordinates);
    throw new Error('Unknown ArrivalRange bounds representation');
  }
  function validBounds(value) {
    if (!Array.isArray(value) || !value.length) return false;
    if (value.every(item => point(item))) {
      return value.length >= 3 && new Set(value.map(item => point(item).join(','))).size >= 3
        && value.every(item => { const [lon, lat] = point(item); return Number.isFinite(lon) && Number.isFinite(lat) && Math.abs(lon) <= 180 && Math.abs(lat) <= 90; });
    }
    return value.every(validBounds);
  }
  function snapshot() {
    const features = Array.from(results.values()).map(row => row.feature);
    const output = { type: 'FeatureCollection', metadata: {
      city_id: config.city_id, demand_run_id: config.demand_run_id, sampling: config.sampling, collection_id: config.collection_id, collector_version: 2, source: 'AMap.ArrivalRange', policy: 'BUS',
      origin_crs: 'WGS84', bounds_crs: 'GCJ-02', minutes: config.minutes,
      expected_records: config.points.length * config.minutes.length,
      source_origin_count: config.source_origin_count, subset_only: config.subset_only || false,
      statistics, exported_at: new Date().toISOString(), complete: features.length === config.points.length * config.minutes.length && features.every(f => f.properties.status === 'ok')
    }, features };
    return output;
  }
  function exportResults() {
    const output = snapshot();
    const url = URL.createObjectURL(new Blob([JSON.stringify(output)], { type: 'application/geo+json' }));
    const link = document.createElement('a'); link.href = url;
    link.download = config.collection_id + '_arrival_range.geojson'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return output;
  }
  window.urbanArrivalCollector = { snapshot, stop: () => { stopped = true; }, export: exportResults, config, results, statistics };
  const service = new AMap.ArrivalRange();
  const started = Date.now();
  for (const origin of config.points) {
    for (const minutes of config.minutes) {
      if (stopped) break;
      const id = config.collection_id + ':' + origin.grid_id + ':' + minutes;
      if (completed.has(origin.grid_id + ':' + minutes)) { statistics.cache_hits++; window.urbanArrivalProgress?.({done:results.size+statistics.cache_hits,total:config.points.length*config.minutes.length,...statistics}); continue; }
      let previous;
      try { previous = await storage('readonly', store => store.get(id)); }
      catch (error) { console.warn('Cache read failed', error); }
      if (!config.force_refresh && previous?.feature.properties.status === 'ok') { results.set(id, previous); await window.urbanArrivalRecord?.(previous.feature); statistics.cache_hits++; window.urbanArrivalProgress?.({done:results.size,total:config.points.length*config.minutes.length,...statistics}); continue; }
      let answer = { ok: false, bounds: [], error: '' }, attempts = 0;
      while (attempts < config.max_attempts && !stopped && statistics.requests < (config.request_budget ?? Infinity)) {
        attempts++;
        answer = await new Promise(resolve => {
          let finished = false;
          const done = value => { if (!finished) { finished = true; clearTimeout(timer); resolve(value); } };
          const timer = setTimeout(() => done({ ok: false, bounds: [], error: 'timeout' }), config.timeout_ms);
          try {
            statistics.requests++;
            service.search(new AMap.LngLat(origin.gcj_lon, origin.gcj_lat), minutes, (status, response) => {
              try {
                const bounds = coordinates(response?.bounds || []);
                const geometryReady = validBounds(bounds);
                // ArrivalRange parses polylines, but its SDK completion check uses polygon/poi.
                const compatibilityAccepted = status === 'no_data' && response?.info === 'OK'
                  && String(response?.infocode) === '10000' && geometryReady;
                const ok = geometryReady && (status === 'complete' || compatibilityAccepted);
                done({ service_status: status, info: response?.info, infocode: response?.infocode, ok, bounds,
                  compatibilityAccepted, error: ok ? '' : (!geometryReady ? 'empty_or_invalid_bounds' : `${status}: ${response?.info || 'no details'}`) });
              } catch (error) { done({ ok: false, bounds: [], error: error.message }); }
            }, { policy: 'BUS', resultType: 'polygon' });
          } catch (error) { done({ ok: false, bounds: [], error: error.message }); }
        });
        await sleep(config.interval_ms);
        if (answer.ok) break;
        if (/LIMIT|INVALID_USER|SCODE|AUTH|DENIED/i.test(answer.info || answer.error || '')) { stopped = true; break; }
      }
      const record = { id, feature: { type: 'Feature', geometry: { type: 'Point', coordinates: [origin.wgs_lon, origin.wgs_lat] }, properties: {
        grid_id: origin.grid_id, grid_idx: origin.grid_idx, wgsLon: origin.wgs_lon, wgsLat: origin.wgs_lat,
        gcjLon: origin.gcj_lon, gcjLat: origin.gcj_lat, minute: minutes, policy: 'BUS', bounds_crs: 'GCJ-02',
        status: answer.ok ? 'ok' : 'fail', bounds: answer.bounds || [], error: answer.error,
        service_status: answer.service_status || null, service_info: answer.info || null, service_infocode: answer.infocode || null,
        sdk_status_compatibility: answer.compatibilityAccepted ? 'polylines_status_bug' : null,
        attempts, collected_at: new Date().toISOString()
      } } };
      results.set(id, record);
      await window.urbanArrivalRecord?.(record.feature);
      if (statistics.requests >= config.request_budget) stopped = true;
      window.urbanArrivalProgress?.({done:results.size,total:config.points.length*config.minutes.length,...statistics});
      try { await storage('readwrite', store => store.put(record)); }
      catch (error) { console.warn('Cache write failed; use urbanArrivalCollector.export()', error); }
      console.log('ArrivalRange', results.size, '/', config.points.length * config.minutes.length, record.feature.properties.status, 'elapsed(s)', Math.round((Date.now() - started) / 1000));
    }
    if (stopped) break;
  }
  await window.urbanArrivalFlush?.();
  if (config.auto_export !== false) exportResults();
  window.urbanArrivalComplete?.(snapshot());
  if (database) database.close();
  console.log(stopped ? 'Stopped. Rerun the same script to resume.' : 'Finished. Failed records will be retried on the next run.');
})();
