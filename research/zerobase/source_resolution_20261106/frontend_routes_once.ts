// Second finite inspection: exact chart request and volume conversion definitions.
import {createHash} from 'node:crypto';
const origin='https://www.lbank.com',sha=(b:any)=>createHash('sha256').update(b).digest('hex');
const emit=(kind:string,data:object)=>console.log(JSON.stringify({kind,at:new Date().toISOString(),...data}));
const hard=setTimeout(()=>{emit('END',{reason:'120s_deadline'});process.exit(0)},120000);
let next=0,excerpts=0;
async function get(url:string){const requested_at=new Date().toISOString(),r=await fetch(url,{signal:AbortSignal.timeout(10000),redirect:'error'}),b=new Uint8Array(await r.arrayBuffer());if(b.length>8000000)throw Error('CAP');return{requested_at,received_at:new Date().toISOString(),status:r.status,bytes:b.length,sha256:sha(b),text:new TextDecoder().decode(b)}}
function out(url:string,t:string,needle:string,pos:number){if(excerpts>=45)return;const offset=Math.max(0,pos-300),b=Buffer.from(t.slice(offset,pos+2300)),id=++excerpts;emit('EXCERPT',{id,url,needle,offset,bytes:b.length,sha256:sha(b),parts:Math.ceil(b.length/2100)});for(let j=0;j<b.length;j+=2100)emit('PART',{id,index:j/2100,base64:b.subarray(j,j+2100).toString('base64')});}
try{
const p=await get(origin+'/futures/btcusdt');emit('PAGE',{url:origin+'/futures/btcusdt',...p,text:undefined});
const urls=[...new Set([...p.text.matchAll(/<script[^>]+src="([^"]+)"/g)].map(m=>new URL(m[1],origin).href))].filter(u=>u.includes('/lbank-front-trade/')&&u.includes('/chunks/')&&!/polyfills|framework|main-|webpack/.test(u));
async function worker(){while(next<urls.length){const url=urls[next++];try{const d=await get(url);emit('ASSET',{url,...d,text:undefined});for(const needle of ['KLinelst','startDatetime','B_:()=>','adaptKLineData','/funding','/Funding']){let pos=-1,n=0;while((pos=d.text.indexOf(needle,pos+1))>=0&&n++<2)out(url,d.text,needle,pos);}}catch(e){emit('ERROR',{url,error:String(e)})}}}
await Promise.all([worker(),worker(),worker(),worker()]);emit('END',{reason:'finite_inspection_complete',assets:urls.length,excerpts});
}catch(e){emit('ERROR',{error:String(e)})}clearTimeout(hard);
