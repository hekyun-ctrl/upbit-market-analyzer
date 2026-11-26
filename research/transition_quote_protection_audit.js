// Research only: sampled quote comparison. No orders or alerts.
function quoteAudit(data){
 const fee=.0006,slip=.0002,reserve=.0001,ts=x=>Date.parse(x)/1000;
 const quotes=[...data.quotes].sort((a,b)=>ts(a.at)-ts(b.at));
 function net(e,exit){const p=exit*(1-e.direction*slip);return e.direction*(p-e.entry)-fee*(e.entry+p)-reserve*e.entry}
 function targetNet(e,exit){return e.direction*(exit-e.entry)-fee*(e.entry+exit)-reserve*e.entry}
 function replay(e,minutes,protect){
  const at=ts(e.observed_at),deadline=at+minutes*60,limit=deadline+120;
  let peak=0,last=at,pending=false,trigger=null;
  for(const q of quotes){
   const t=ts(q.at);if(t<=at)continue;if(t>limit||t-last>120)return {status:"incomplete",peak,trigger};
   last=t;const v=net(e,q.price);
   const close=(status)=>({status,at:q.at,price:q.price,net_per_btc:at<1795680000&&t>=1795680000?null:v,peak,trigger});
   if(e.direction*(q.price-e.stop)<=0)return close("stop");
   if(e.direction*(q.price-e.target1)>=0)return close("target");
   if(pending)return close("protect_next_quote");
   if(t>=deadline)return close("timeout");
   peak=Math.max(peak,v);
   if(protect&&peak>0&&v<=.5*peak){pending=true;trigger=q.at}
  }
  return {status:"incomplete",peak,trigger}
 }
 return data.events.map(e=>({id:e.id,observed_at:e.observed_at,entry:e.entry,direction:e.direction,planned_net_rr:targetNet(e,e.target1)/(-net(e,e.stop)),baseline30:replay(e,30,false),protect30:replay(e,30,true),baseline90:replay(e,90,false),protect90:replay(e,90,true)}));
}
if(require.main===module){const fs=require('node:fs');console.log(JSON.stringify(quoteAudit(JSON.parse(fs.readFileSync(process.argv[2],'utf8'))),null,2));}
module.exports={quoteAudit};
