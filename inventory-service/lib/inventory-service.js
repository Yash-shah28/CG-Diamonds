import { createHash, timingSafeEqual } from 'node:crypto';
export const MAX_SNAPSHOT=64*1024*1024;
export class ApiError extends Error {constructor(status,message){super(message);this.status=status;}}
export const sha256=value=>createHash('sha256').update(value).digest('hex');
const idOK=value=>typeof value==='string'&&/^[a-f0-9]{32}$/.test(value);
const hashOK=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const same=(a,b)=>hashOK(a)&&hashOK(b)&&timingSafeEqual(Buffer.from(a,'hex'),Buffer.from(b,'hex'));
export function metadata(body){
 if(!body||!idOK(body.client_id)||!Number.isSafeInteger(body.revision)||body.revision<1)throw new ApiError(400,'Invalid company or revision');
 if(!hashOK(body.key_hash)||!hashOK(body.sha256)||!Number.isSafeInteger(body.size)||body.size<1||body.size>MAX_SNAPSHOT||!Number.isSafeInteger(body.records)||body.records<1)throw new ApiError(400,'Invalid feed metadata');
 if(!Array.isArray(body.kinds)||!body.kinds.length||new Set(body.kinds).size!==body.kinds.length||body.kinds.some(k=>!['Natural','Lab Grown'].includes(k)))throw new ApiError(400,'Invalid inventory scope');
 return {...body,kinds:[...body.kinds].sort()};
}
const controlPath=id=>'inventory/control/'+id+'.json';
const keyPath=hash=>'inventory/keys/'+hash+'.json';
export const snapshotPath=m=>`inventory/snapshots/${m.client_id}/${m.revision}/${m.sha256}.json`;
function equivalent(c,m){return c?.enabled&&c.revision===m.revision&&same(c.key_hash,m.key_hash)&&c.sha256===m.sha256&&JSON.stringify(c.kinds)===JSON.stringify(m.kinds);}
function assertNew(c,revision){if(c&&revision<=c.revision)throw new ApiError(409,'A newer or conflicting revision is already active');}
export function createService(storage,adminHash){
 function authenticate(header){
  if(!hashOK(adminHash))throw new ApiError(503,'Inventory hosting is not configured');
  if(typeof header!=='string'||!header.startsWith('Bearer ')||!same(sha256(header.slice(7)),adminHash))throw new ApiError(401,'Unauthorized');
 }
 async function admin(header,body){
  authenticate(header);
  if(body===null){await storage.ready();return {ok:true,mode:'pull-only'};}
  if(!body||typeof body!=='object'||!idOK(body.client_id))throw new ApiError(400,'Invalid management request');
  const existing=await storage.json(controlPath(body.client_id));const c=existing?.value;
  if(body.action==='status')return {ok:true,enabled:Boolean(c?.enabled),revision:c?.revision||0,key_hash:c?.key_hash||null,records:c?.records||0};
  if(body.action==='revoke'||body.action==='suspend'){
   if(!Number.isSafeInteger(body.revision)||body.revision<1)throw new ApiError(400,'Invalid revision');
   if(c?.revision===body.revision&&!c.enabled&&c.operation===body.action)return {ok:true,revision:c.revision};
   assertNew(c,body.revision);
   await storage.putJson(controlPath(body.client_id),{...(c||{}),client_id:body.client_id,revision:body.revision,enabled:false,operation:body.action},existing?.etag);
   return {ok:true,revision:body.revision};
  }
  if(!['authorize','commit'].includes(body.action))throw new ApiError(400,'Unknown management action');
  const m=metadata(body);
  if(equivalent(c,m))return {ok:true,revision:m.revision,already_published:true};
  assertNew(c,m.revision);
  const path=snapshotPath(m);
  if(body.action==='authorize')return {ok:true,revision:m.revision,upload_url:await storage.authorize(path,m.size)};
  const raw=await storage.bytes(path,MAX_SNAPSHOT);
  if(raw.length!==m.size||!same(sha256(raw),m.sha256))throw new ApiError(400,'Snapshot integrity check failed');
  let payload;try{payload=JSON.parse(raw.toString('utf8'));}catch{throw new ApiError(400,'Snapshot must contain JSON');}
  if(!payload||typeof payload!=='object')throw new ApiError(400,'Snapshot must contain JSON');
  let diamonds;
  if(Array.isArray(payload.diamonds)){
   diamonds=payload.diamonds;
  }else if(payload.natural!==undefined||payload.lab_grown!==undefined){
   if(payload.natural!==undefined&&!Array.isArray(payload.natural))throw new ApiError(400,'Snapshot natural field must be an array');
   if(payload.lab_grown!==undefined&&!Array.isArray(payload.lab_grown))throw new ApiError(400,'Snapshot lab_grown field must be an array');
   const natural=Array.isArray(payload.natural)?payload.natural:[];
   const lab_grown=Array.isArray(payload.lab_grown)?payload.lab_grown:[];
   for(const d of natural){if(d&&typeof d==='object'&&!d['Diamond Type'])d['Diamond Type']='NATURAL';}
   for(const d of lab_grown){if(d&&typeof d==='object'&&!d['Diamond Type'])d['Diamond Type']='LAB_GROWN';}
   diamonds=[...natural,...lab_grown];
  }else{
   throw new ApiError(400,'Snapshot record count mismatch');
  }
  if(diamonds.length!==m.records)throw new ApiError(400,'Snapshot record count mismatch');
  const allowed=new Set(m.kinds.map(k=>k==='Natural'?'NATURAL':'LAB_GROWN'));const identities=new Set();
  for(const diamond of diamonds){
   if(!diamond||typeof diamond!=='object'||!allowed.has(diamond['Diamond Type'])||!String(diamond.StockId??'').trim())throw new ApiError(400,'Snapshot contains an invalid record or inventory type');
   const identity=diamond['Diamond Type']+'\0'+diamond.StockId;if(identities.has(identity))throw new ApiError(400,'Snapshot contains duplicate stock identifiers');identities.add(identity);
  }
  const binding=await storage.json(keyPath(m.key_hash));
  if(binding&&binding.value.client_id!==m.client_id)throw new ApiError(409,'Key is already assigned');
  if(!binding)await storage.putJson(keyPath(m.key_hash),{client_id:m.client_id});
  const updated={client_id:m.client_id,revision:m.revision,key_hash:m.key_hash,kinds:m.kinds,enabled:true,snapshot:path,sha256:m.sha256,size:m.size,records:m.records,published_at:new Date().toISOString(),operation:'publish'};
  await storage.putJson(controlPath(m.client_id),updated,existing?.etag);
  return {ok:true,revision:m.revision,records:m.records};
 }
 async function inventory(key){
  if(typeof key!=='string'||!/^cg_read_[A-Za-z0-9_-]{30,100}$/.test(key))throw new ApiError(401,'Unauthorized');
  const hash=sha256(key);const binding=await storage.json(keyPath(hash));
  if(!binding||!idOK(binding.value.client_id))throw new ApiError(401,'Unauthorized');
  const record=await storage.json(controlPath(binding.value.client_id));const c=record?.value;
  if(!c?.enabled||!same(c.key_hash,hash))throw new ApiError(401,'Unauthorized');
  if(!c.snapshot||c.snapshot!==snapshotPath(c))throw new ApiError(503,'No complete inventory is available');
  const stream=await storage.stream(c.snapshot);
  return {stream,records:c.records,snapshot_id:c.client_id+':'+c.revision,published_at:c.published_at};
 }
 return {admin,inventory};
}
