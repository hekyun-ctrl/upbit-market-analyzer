// Read-only research. No order/notification calls. Run: node transition_candidate_audit.js evidence.json
function evaluate(data) {
  const fee=.0006, slip=.0002, reserve=.0001, split=Date.parse(data.split_at)/1000;
  const byStart=new Map(data.bars.map(b=>[b.start,b]));
  function net(e,price,kind){const exit=kind==="target"?price:price*(1-e.direction*slip);return e.direction*(exit-e.entry)-fee*(e.entry+exit)-reserve*e.entry}
  function model(e,n,protect) {
    const at=Date.parse(e.observed_at)/1000, first=Math.ceil(at/300)*300;
    let peak=0, pending=false, trigger=null;
    const risk=-net(e,e.stop,"stop"), rr=net(e,e.target1,"target")/risk;
    for(let i=0;i<n;i++){
      const start=first+300*i,b=byStart.get(start);
      if(!b||b.quality!=="stream_closed"||Date.parse(b.observed_at)/1000<start+300||
        !(b.low<=Math.min(b.open,b.close)&&b.high>=Math.max(b.open,b.close)))return {status:"incomplete",bars:i,rr};
      const t=e.direction===1?b.high>=e.target1:b.low<=e.target1,s=e.direction===1?b.low<=e.stop:b.high>=e.stop;
      const result=(status,p,kind)=>{const fundingCrossed=[1795680000].some(f=>at<f&&f<=start+300);return {status,bars:i+1,exit:p,net_per_btc:fundingCrossed?null:net(e,p,kind),rr,peak_close_net:peak,trigger,fundingCrossed}};
      if(t&&s)return {status:"ambiguous",bars:i+1,rr};
      if(s)return result("stop",e.direction===1?Math.min(e.stop,b.open):Math.max(e.stop,b.open),"stop");
      if(t)return result("target",e.target1,"target");
      if(pending)return result("protect_next_close",b.close,"stop");
      const v=net(e,b.close,"stop");peak=Math.max(peak,v);
      if(protect&&peak>0&&v<=peak*.5&&i<n-1){pending=true;trigger=start+300}
      if(i===n-1)return result("timeout",b.close,"stop");
    }
  }
  return data.events.map(e=>({id:e.id,observed_at:e.observed_at,split:Date.parse(e.observed_at)/1000<split?"incident":"post_feature_freeze",direction:e.direction,entry:e.entry,stop:e.stop,target:e.target1,actual:"rejected",baseline30:model(e,6,false),protection30:model(e,6,true),baseline90:model(e,18,false)}));
}
if(require.main===module){const fs=require('node:fs');console.log(JSON.stringify(evaluate(JSON.parse(fs.readFileSync(process.argv[2],'utf8'))),null,2));}
module.exports={evaluate};
