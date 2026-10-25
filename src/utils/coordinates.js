// WGS84 is the application's display/data CRS; AMap geocoding returns GCJ-02.
export function gcjToWgs(lon, lat) {
  if (lon < 72.004 || lon > 137.8347 || lat < .8293 || lat > 55.8271) return [lon,lat];
  const x=lon-105,y=lat-35,pi=Math.PI;
  let dy=-100+2*x+3*y+.2*y*y+.1*x*y+.2*Math.sqrt(Math.abs(x));
  dy+=(20*Math.sin(6*x*pi)+20*Math.sin(2*x*pi))*2/3;
  dy+=(20*Math.sin(y*pi)+40*Math.sin(y*pi/3))*2/3;
  dy+=(160*Math.sin(y*pi/12)+320*Math.sin(y*pi/30))*2/3;
  let dx=300+x+2*y+.1*x*x+.1*x*y+.1*Math.sqrt(Math.abs(x));
  dx+=(20*Math.sin(6*x*pi)+20*Math.sin(2*x*pi))*2/3;
  dx+=(20*Math.sin(x*pi)+40*Math.sin(x*pi/3))*2/3;
  dx+=(150*Math.sin(x*pi/12)+300*Math.sin(x*pi/30))*2/3;
  const rad=lat/180*pi,sin=Math.sin(rad),magic=1-.006693421622965943*sin*sin,sqrt=Math.sqrt(magic);
  dy=dy*180/((6378245*(1-.006693421622965943))/(magic*sqrt)*pi);
  dx=dx*180/(6378245/sqrt*Math.cos(rad)*pi);
  return [lon-dx,lat-dy];
}

// Overlay imports explicitly declare their source CRS; calculation imports use the backend wizard.
export function overlayFeatures(value, coordinateSystem) {
  const features=value.type==='FeatureCollection'?value.features:value.type==='Feature'?[value]:null;
  if(!Array.isArray(features))throw new Error('请选择GeoJSON Feature或FeatureCollection');
  const coordinates=points=>{
    if(!Array.isArray(points))throw new Error('几何坐标格式错误');
    if(typeof points[0]==='number'){
      const [lon,lat,...rest]=points;
      if(!Number.isFinite(lon)||!Number.isFinite(lat)||Math.abs(lon)>180||Math.abs(lat)>90)throw new Error('叠加图层需要经纬度；投影数据请先转换');
      return [...(coordinateSystem==='GCJ02'?gcjToWgs(lon,lat):[lon,lat]),...rest];
    }
    return points.map(coordinates);
  };
  const geometry=value=>value?.type==='GeometryCollection'?{...value,geometries:value.geometries.map(geometry)}:value?{...value,coordinates:coordinates(value.coordinates)}:null;
  return features.map(feature=>{
    if(feature.type!=='Feature')throw new Error('图层包含非Feature记录');
    return {...feature,geometry:geometry(feature.geometry)};
  });
}
