// Finite, public-data-only source investigation. No orders, keys or alerts.
import { createHash } from 'node:crypto';
const origin = 'https://www.lbank.com';
const sha = (b: Uint8Array|string) => createHash('sha256').update(b).digest('hex');
const emit = (kind:string, data:object) => console.log(JSON.stringify({kind,at:new Date().toISOString(),...data}));
const hardStop = setTimeout(() => { emit('END',{reason:'90s_deadline'}); process.exit(0); },90000);
async function get(url:string) {
  const requested_at=new Date().toISOString();
  const r=await fetch(url,{signal:AbortSignal.timeout(12000),redirect:'error'});
  const b=new Uint8Array(await r.arrayBuffer());
  if(b.length>8000000) throw new Error('BODY_CAP');
  return {requested_at,received_at:new Date().toISOString(),status:r.status,bytes:b.length,sha256:sha(b),text:new TextDecoder().decode(b)};
}
try {
  const page=await get(origin+'/futures/btcusdt');
  emit('PAGE',{url:origin+'/futures/btcusdt',...page,text:undefined});
  if(page.status!==200) throw new Error('OFFICIAL_PAGE_UNAVAILABLE');
  const urls=[...new Set([...page.text.matchAll(/<script[^>]+src="([^"]+)"/g)].map(m=>new URL(m[1],origin).href))].filter(u=>u.includes('/lbank-front-trade/')&&u.includes('/chunks/'));
  emit('ASSETS',{urls});
  const selected=urls.filter(u=>/pages\/(?:_app|futures\/)|\/(?:1212|7724|7996)-/.test(u));
  let seq=0;
  for(const url of selected) {
    const d=await get(url);emit('ASSET',{url,...d,text:undefined});
    const needles=['93127:', '97202:', '68796:', 'SendQryKLine', 'SendQryHistoryKLine', 'JB:()=>', 'B_:()=>', 'volumeMultiple'];
    for(const needle of needles) {
      let pos=-1,n=0;
      while((pos=d.text.indexOf(needle,pos+1))>=0&&n++<2) {
        const start=Math.max(0,pos-300),s=d.text.slice(start,pos+12500),id=++seq;
        const raw=Buffer.from(s);emit('EXCERPT',{id,url,needle,offset:start,bytes:raw.length,sha256:sha(raw),parts:Math.ceil(raw.length/2100)});
        for(let j=0;j<raw.length;j+=2100) emit('PART',{id,index:j/2100,base64:raw.subarray(j,j+2100).toString('base64')});
      }
    }
  }
  emit('END',{reason:'finite_inspection_complete',selected:selected.length,excerpts:seq});
}catch(e){emit('ERROR',{error:String(e)});}
clearTimeout(hardStop);
