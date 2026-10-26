export function featureBounds(features) {
  const bounds=[Infinity,Infinity,-Infinity,-Infinity];
  const visit=coordinates=>{
    if(!Array.isArray(coordinates))return;
    if(typeof coordinates[0]==='number'){
      const [x,y]=coordinates;
      if(Number.isFinite(x)&&Number.isFinite(y)){bounds[0]=Math.min(bounds[0],x);bounds[1]=Math.min(bounds[1],y);bounds[2]=Math.max(bounds[2],x);bounds[3]=Math.max(bounds[3],y);}
    }else coordinates.forEach(visit);
  };
  (features || []).forEach(feature=>visit(feature.geometry?.coordinates));
  return Number.isFinite(bounds[0])?bounds:null;
}

export function boundsView(bounds,width,height) {
  if(!bounds)return null;
  const [west,south,east,north]=bounds;
  const mercator=lat=>Math.log(Math.tan(Math.PI/4+Math.max(-85,Math.min(85,lat))*Math.PI/360));
  const zx=Math.log2(Math.max(240,width)*360/(512*Math.max(east-west,.001)));
  const zy=Math.log2(Math.max(240,height)*2*Math.PI/(512*Math.max(mercator(north)-mercator(south),.00001)));
  return {longitude:(west+east)/2,latitude:(south+north)/2,zoom:Math.max(4,Math.min(14,zx,zy)),pitch:0,bearing:0};
}
