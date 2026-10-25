import { matchingPeriods, comparisonCsv } from './assignmentComparison';
const base = {city_id:'Linyi',demand_run_id:'d1',shared_demand_signature:'s1',poi_count:500,hourly_demand_pax:50000,
 algorithm:'same',headway_basis:'default_fallback_only',default_headway_min:15,parameters:{period:'am_peak',bus_speed_kmh:18},period:'am_peak',run_id:'am'};
test('只匹配真实同条件记录，不同需求/参数不进入三时段表',()=>{
 const ok={...base,period:'offpeak',run_id:'flat',parameters:{bus_speed_kmh:18,period:'offpeak'}};
 const bad={...base,period:'pm_peak',run_id:'pm',hourly_demand_pax:863000};
 const result=matchingPeriods(base,[ok,bad]);expect(result.am_peak).toBe(base);expect(result.offpeak).toBe(ok);expect(result.pm_peak).toBeNull();
});
test('历史参数缺失时不默认认为可比',()=>{expect(matchingPeriods({...base,shared_demand_signature:null},[])).toEqual({});});
test('导出包含真实实验ID和BOM，缺少的时段不补造结果',()=>{
 const csv=comparisonCsv(matchingPeriods(base,[]));expect(csv.startsWith('\ufeff')).toBe(true);expect(csv).toContain('"am"');expect(csv).toContain('"50000"');
});
