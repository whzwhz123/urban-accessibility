const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const cache = new Map();
let calls = 0;
let serviceStatus = 'complete';
let serviceInfo = 'OK';
let serviceCode = '10000';
let serviceBounds = [[[['116','39'],['116.1','39'],['116','39.1'],['116','39']]]];
const indexedDB = { open() {
  const request = {};
  queueMicrotask(() => {
    request.result = { close() {}, transaction() {
      const transaction = { objectStore() { return {
        get: id => ({ result: cache.get(id) }),
        put: value => { cache.set(value.id, value); return { result: value.id }; }
      }; } };
      queueMicrotask(() => transaction.oncomplete());
      return transaction;
    } };
    request.onsuccess();
  });
  return request;
} };
const context = vm.createContext({ window: {}, indexedDB, console: { log() {}, warn() {} },
  setTimeout, clearTimeout, Blob, URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} },
  document: { createElement: () => ({ click() {} }) },
  AMap: { LngLat: function(x,y) { this.x=x; this.y=y; }, ArrivalRange: function() {
    this.search = (origin, minutes, callback) => { calls++; callback(serviceStatus, {
      info: serviceInfo, infocode: serviceCode, bounds: serviceBounds
    }); };
  } }
});
const config = { collection_id: 'fixture', points: [{grid_id: 'a',wgs_lon:116,wgs_lat:39,gcj_lon:116,gcj_lat:39}],
  minutes:[45], interval_ms:0, timeout_ms:100, max_attempts:2 };
const template = fs.readFileSync('Back_end/data_process/arrival_range_collector.js', 'utf8');
const run = () => vm.runInContext(template.replace('__ARRIVAL_CONFIG__', JSON.stringify(config)), context);
(async () => {
  await run();
  assert.equal(context.window.urbanArrivalCollector.export().features[0].properties.status, 'ok');
  assert.equal(typeof context.window.urbanArrivalCollector.export().features[0].properties.bounds[0][0][0][0], 'number');
  await run();
  assert.equal(calls, 1);
  assert.equal(context.window.urbanArrivalCollector.statistics.cache_hits, 1);
  serviceStatus='no_data';config.collection_id='compatibility';
  await run();
  let properties=context.window.urbanArrivalCollector.export().features[0].properties;
  assert.equal(properties.status,'ok');assert.equal(properties.service_status,'no_data');
  assert.equal(properties.sdk_status_compatibility,'polylines_status_bug');assert.equal(calls,2);
  serviceCode='10001';serviceInfo='INVALID_USER_KEY';config.collection_id='failure';
  await run();
  properties=context.window.urbanArrivalCollector.export().features[0].properties;
  assert.equal(properties.status,'fail');assert.equal(calls,3); // Permanent credential errors stop immediately.
  serviceCode='10000';serviceInfo='OK';serviceBounds=[];config.collection_id='empty';
  await run();
  assert.equal(context.window.urbanArrivalCollector.export().features[0].properties.status,'fail');
  serviceBounds=[[[['116','39'],['116','39'],['116','39']]]];config.collection_id='degenerate';
  await run();
  assert.equal(context.window.urbanArrivalCollector.export().features[0].properties.status,'fail');
  console.log('PASS: coordinate parsing, cache reuse, SDK compatibility; key errors, empty and degenerate bounds rejected');
})().catch(error => { console.error(error); process.exitCode=1; });
