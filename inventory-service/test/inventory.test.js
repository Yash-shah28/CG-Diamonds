import test from 'node:test';import assert from 'node:assert/strict';
import {createService,sha256,ApiError,snapshotPath} from '../lib/inventory-service.js';
class Memory {
 constructor(){this.objects=new Map();this.etag=0;}
 async ready(){}
 async json(path){const o=this.objects.get(path);return o?{value:JSON.parse(o.bytes),etag:o.etag}:null;}
 async putJson(path,value,etag){const old=this.objects.get(path);if((!etag&&old)||(etag&&old?.etag!==etag))throw new ApiError(409,'Concurrent');this.objects.set(path,{bytes:Buffer.from(JSON.stringify(value)),etag:String(++this.etag)});}
 async authorize(path){this.authorized=path;return 'https://blob.vercel-storage.com/private-upload';}
 async bytes(path){const x=this.objects.get(path);if(!x)throw new ApiError(503,'Missing');return x.bytes;}
 async stream(path){const body=await this.bytes(path);return new ReadableStream({start(c){c.enqueue(body);c.close();}});}
 snapshot(m,body){this.objects.set(snapshotPath(m),{bytes:body,etag:String(++this.etag)});}
}
const token='cg_admin_'+'a'.repeat(43),header='Bearer '+token;
const key1='cg_read_'+'b'.repeat(43),key2='cg_read_'+'c'.repeat(43);
const record=(id='A1',kind='NATURAL')=>({StockId:id,'Diamond Type':kind,PricePerCarat:'100'});
function meta(client_id='a'.repeat(32),revision=1,key=key1,records=[record()]){const raw=Buffer.from(JSON.stringify({diamonds:records}));return {raw,m:{client_id,revision,key_hash:sha256(key),sha256:sha256(raw),size:raw.length,records:records.length,kinds:['Natural']}};}
async function publish(s,storage,data){storage.snapshot(data.m,data.raw);return s.admin(header,{action:'commit',...data.m});}
test('no admin authorization or incomplete setup fails closed',async()=>{const storage=new Memory();const s=createService(storage,sha256(token));await assert.rejects(()=>s.admin('Bearer wrong',null),{status:401});await assert.rejects(()=>createService(storage,'').admin(header,null),{status:503});});
test('private publication, company selection and complete response',async()=>{const storage=new Memory(),s=createService(storage,sha256(token));const data=meta();await publish(s,storage,data);const result=await s.inventory(key1);assert.equal(result.records,1);assert.deepEqual(JSON.parse(await new Response(result.stream).text()).diamonds,[record()]);await assert.rejects(()=>s.inventory(key2),{status:401});});
test('upload authorization cannot activate uncommitted data',async()=>{const storage=new Memory(),s=createService(storage,sha256(token));const data=meta();const r=await s.admin(header,{action:'authorize',...data.m});assert.match(r.upload_url,/https:/);await assert.rejects(()=>s.inventory(key1),{status:401});await assert.rejects(()=>s.admin(header,{action:'commit',...data.m}),{status:503});});
test('idempotent commit, stale rejection and last good snapshot',async()=>{const storage=new Memory(),s=createService(storage,sha256(token));const data=meta();await publish(s,storage,data);assert.equal((await publish(s,storage,data)).already_published,true);const fresh=meta(data.m.client_id,3,key1,[record('NEW')]);await publish(s,storage,fresh);await assert.rejects(()=>publish(s,storage,meta(data.m.client_id,2)),{status:409});const result=await s.inventory(key1);assert.equal(JSON.parse(await new Response(result.stream).text()).diamonds[0].StockId,'NEW');});
test('rotation invalidates old key immediately and keeps new key working',async()=>{const storage=new Memory(),s=createService(storage,sha256(token));await publish(s,storage,meta());await publish(s,storage,meta('a'.repeat(32),2,key2));await assert.rejects(()=>s.inventory(key1),{status:401});assert.equal((await s.inventory(key2)).records,1);});
test('revocation and suspension cannot be undone by stale jobs',async()=>{for(const action of ['revoke','suspend']){const storage=new Memory(),s=createService(storage,sha256(token));const data=meta();await publish(s,storage,data);await s.admin(header,{action,client_id:data.m.client_id,revision:3});await assert.rejects(()=>s.inventory(key1),{status:401});await assert.rejects(()=>publish(s,storage,meta(data.m.client_id,2)),{status:409});assert.equal((await s.admin(header,{action,client_id:data.m.client_id,revision:3})).revision,3);}});
test('integrity and scope mismatch leave old feed active',async()=>{const storage=new Memory(),s=createService(storage,sha256(token));await publish(s,storage,meta());const bad=meta('a'.repeat(32),2,key1,[record('LAB','LAB_GROWN')]);await assert.rejects(()=>publish(s,storage,bad),{status:400});const corrupt=meta('a'.repeat(32),2);storage.snapshot(corrupt.m,Buffer.from('bad'));await assert.rejects(()=>s.admin(header,{action:'commit',...corrupt.m}),{status:400});assert.equal((await s.inventory(key1)).records,1);});
test('different companies cannot share key or read each other snapshots',async()=>{const storage=new Memory(),s=createService(storage,sha256(token));await publish(s,storage,meta());await assert.rejects(()=>publish(s,storage,meta('d'.repeat(32),1,key1)),{status:409});await publish(s,storage,meta('d'.repeat(32),1,key2,[record('B1')]));const r=await s.inventory(key2);assert.equal(JSON.parse(await new Response(r.stream).text()).diamonds[0].StockId,'B1');});
test('partial or duplicate inventory is rejected',async()=>{const storage=new Memory(),s=createService(storage,sha256(token));for(const records of [[record(),record()],[{...record(),StockId:''}]])await assert.rejects(()=>publish(s,storage,meta('a'.repeat(32),1,key1,records)),{status:400});});

test('two array split natural and lab_grown publication and response', async () => {
  const storage = new Memory(), s = createService(storage, sha256(token));
  const nat = record('NAT-1', 'NATURAL');
  const lab = record('LAB-1', 'LAB_GROWN');
  const raw = Buffer.from(JSON.stringify({ natural: [nat], lab_grown: [lab] }));
  const m = {
    client_id: 'c'.repeat(32),
    revision: 1,
    key_hash: sha256(key1),
    sha256: sha256(raw),
    size: raw.length,
    records: 2,
    kinds: ['Natural', 'Lab Grown']
  };
  storage.snapshot(m, raw);
  await s.admin(header, { action: 'commit', ...m });
  const result = await s.inventory(key1);
  assert.equal(result.records, 2);
  const body = JSON.parse(await new Response(result.stream).text());
  assert.deepEqual(body.natural, [nat]);
  assert.deepEqual(body.lab_grown, [lab]);
});
