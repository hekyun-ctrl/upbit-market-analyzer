// Research diagnostic only; exact structural-break logic transcribed from frozen PR65 Python module.
function diagnose(data){
 const ts=x=>Date.parse(x)/1000, start=Date.parse("2026-11-25T17:00:00Z")/1000,end=Date.parse("2026-11-25T20:00:00Z")/1000;
 const bars=[...data.bars].sort((a,b)=>a.start-b.start), by=new Map(bars.map(b=>[b.start,b])),routes=[...data.routes].sort((a,b)=>ts(a.observed_at)-ts(b.observed_at));const rows=[];
 const valid=b=>b&&b.quality==="stream_closed"&&b.start%300===0&&ts(b.observed_at)>=b.start+300&&[b.open,b.high,b.low,b.close].every(x=>Number.isFinite(x)&&x>0)&&b.high>=Math.max(b.open,b.close,b.low)&&b.low<=Math.min(b.open,b.close,b.high);
 for(let close=start;close<end;close+=300){
   const bar=by.get(close-300), tail=[-1200,-900,-600,-300].map(x=>by.get(close+x));
   if(!tail.every(valid)){rows.push({close,status:"missing_input"});continue}
   const r=routes.find(x=>x.ready&&ts(x.observed_at)>=ts(bar.observed_at)&&ts(x.observed_at)<=close+90&&[-1,0,1].includes(x.direction));
   if(!r){rows.push({close,status:"no_route"});continue}
   const at=Math.max(ts(bar.observed_at),ts(r.observed_at));
   if(tail.some(b=>ts(b.observed_at)>at)){rows.push({close,status:"late_input"});continue}
   const h=tail.map(b=>b.high),l=tail.map(b=>b.low),c=tail.map(b=>b.close);
   const up=Math.min(...l.slice(2))>Math.min(...l.slice(0,2))&&c[3]>c[2]&&c[2]>c[1];
   const down=Math.max(...h.slice(2))<Math.max(...h.slice(0,2))&&c[3]<c[2]&&c[2]<c[1];
   const direction=up?1:down?-1:0;
   const broken=direction===1&&c[3]>Math.max(h[1],h[2])||direction===-1&&c[3]<Math.min(l[1],l[2]);
   const structural=broken?direction:0,old=r.direction,augmented=old||structural;
   const first=(Math.floor(at/300)+1)*300, future=Array.from({length:6},(_,i)=>by.get(first+i*300));
   if(!future.every(valid)){rows.push({close,status:"missing_outcome",old,augmented});continue}
   const entry=future[0].open,exit=future[5].close,move=(exit-entry)/entry*100;
   rows.push({close,status:"scored",observed_at:r.observed_at,old,structural,augmented,entry,exit,move_pct:move,old_net_pct:old?old*move-.15:0,augmented_net_pct:augmented?augmented*move-.15:0});
 }
 return rows;
}
if(require.main===module){const fs=require('node:fs');console.log(JSON.stringify(diagnose(JSON.parse(fs.readFileSync(process.argv[2],'utf8'))),null,2))}
module.exports={diagnose};
