import {boundsView,featureBounds} from './mapViewport';
test('几何范围忽略无效坐标并覆盖多折线',()=>{
 expect(featureBounds([{geometry:{coordinates:[[[116,39],[117,40]],[[118,41],[NaN,1]]]}}])).toEqual([116,39,118,41]);
 expect(featureBounds([])).toBeNull();
});
test('城市范围适配视口，不再缩小至全省',()=>{
 const view=boundsView([117.8,34.8,118.8,36],1000,800);
 expect(view.zoom).toBeGreaterThan(8);expect(view.zoom).toBeLessThan(10);
 expect(view.longitude).toBeCloseTo(118.3);expect(view.latitude).toBeCloseTo(35.4);
});
