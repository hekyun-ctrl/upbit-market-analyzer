// Finite metadata/actual wire qualification after locating official conversion.
import {createHash} from 'node:crypto';
import {inflateSync} from 'node:zlib';
const sha=(b:any)=>createHash('sha256').update(b).digest('hex');
const emit=(kind:string,data:object)=>console.log(JSON.stringify({kind,at:new Date().toISOString(),...data}));
const hard=setTimeout(()=>{emit('END',{reason:'55s_deadline'});process.exit(0)},55000);
function parts(kind:string,b:Buffer,meta:object){const id=sha(b);emit(kind,{...meta,id,bytes:b.length,sha256:id,parts:Math.ceil(b.length/2100)});for(let j=0;j<b.length;j+=2100)emit('PART',{id,index:j/2100,base64:b.subarray(j,j+2100).toString('base64')});}
async function request(url:string,kind:string){const requested_at=new Date().toISOString(),r=await fetch(url,{redirect:'error',signal:AbortSignal.timeout(12000)}),b=Buffer.from(await r.arrayBuffer()),received_at=new Date().toISOString();if(b.length>8000000)throw Error('CAP');return{url,requested_at,received_at,status:r.status,bytes:b.length,sha256:sha(b),raw:b}}
try{
const u='https://www.lbank.com/static-client/lbank-front-trade/_next/static/chunks/pages/_app-dd35e0ebacb61017.js',d=await request(u,'ASSET'),t=d.raw.toString();emit('ASSET',{...d,raw:undefined});let seq=0;
for(const needle of ['cfdAddress:','ssrCfdAddress:','Gc:()=>','function Gc','ssrCfdAddress']){let pos=-1,n=0;while((pos=t.indexOf(needle,pos+1))>=0&&n++<2){const offset=Math.max(0,pos-220),b=Buffer.from(t.slice(offset,pos+1800));parts('EXCERPT',b,{seq:++seq,url:u,needle,offset})}}
const url='https://lbkperp.lbank.com/cfd/openApi/v1/pub/instrument?productGroup=SwapU',r=await request(url,'HTTP');
parts('HTTP',r.raw,{...r,raw:undefined});
await new Promise<void>((resolve)=>{let seen=0,done=false;const url='wss://uuws.rerrkvifj.com/ws/v3',ws=new WebSocket(url);ws.binaryType='arraybuffer';const finish=(reason:string)=>{if(done)return;done=true;emit('WS_END',{reason,frames:seen});ws.close();resolve()};const timer=setTimeout(()=>finish('25s_finite_window'),25000);
ws.onopen=()=>{const q={x:2,y:'2000000001',z:1,a:{i:'BTCUSDT_5m'},e:JSON.stringify({bvc:'202',isUsd:1})};emit('WS_OPEN',{url,session:'zb-source-20261106-actual',request:q});ws.send(JSON.stringify(q))};
ws.onmessage=(e)=>{const received_at=new Date().toISOString(),wire=Buffer.from(typeof e.data==='string'?e.data:new Uint8Array(e.data));if(wire.length>1000000){finish('wire_cap');return}try{const decoded=wire[0]===123?wire:inflateSync(wire,{maxOutputLength:1000000}),f=JSON.parse(decoded.toString());if(f.x===2){parts('WIRE',wire,{seq:++seen,session:'zb-source-20261106-actual',received_at,url,transport:typeof e.data==='string'?'utf8_text':'binary',frame:f});if(seen>=15)finish('15_frame_cap')}}catch(e){emit('WS_ERROR',{error:String(e)})}};
ws.onerror=(e)=>{emit('WS_ERROR',{error:String(e)});clearTimeout(timer);finish('transport_error')};ws.onclose=()=>{clearTimeout(timer);finish('closed')};});
emit('END',{reason:'finite_metadata_wire_complete'});
}catch(e){emit('ERROR',{error:String(e)})}clearTimeout(hard);
