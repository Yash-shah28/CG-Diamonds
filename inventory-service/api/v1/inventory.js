import {createService,ApiError} from '../../lib/inventory-service.js';
import {blobStorage} from '../../lib/inventory-blob.js';
export default async function handler(req,res){
 res.setHeader('Cache-Control','private, no-store');res.setHeader('X-Content-Type-Options','nosniff');
 if(req.method!=='GET'){res.setHeader('Allow','GET');return res.status(405).end();}
 try{
  const result=await createService(blobStorage(),process.env.INVENTORY_ADMIN_TOKEN_SHA256).inventory(req.headers['x-api-key']);
  res.setHeader('Content-Type','application/json; charset=utf-8');
  res.setHeader('X-Inventory-Records',String(result.records));
  res.setHeader('X-Inventory-Snapshot',result.snapshot_id);
  res.setHeader('X-Inventory-Published-At',result.published_at);
  const reader=result.stream.getReader();
  const chunks=[];
  while(true){
   const {done,value}=await reader.read();
   if(done)break;
   chunks.push(value);
  }
  const raw=Buffer.concat(chunks).toString('utf8');
  let data;
  try{data=JSON.parse(raw);}catch{data=null;}
  if(data&&Array.isArray(data.diamonds)){
   const natural=[];
   const lab_grown=[];
   for(const d of data.diamonds){
    if(d&&d['Diamond Type']==='LAB_GROWN')lab_grown.push(d);
    else natural.push(d);
   }
   data={natural,lab_grown};
   return res.status(200).send(JSON.stringify(data));
  }
  return res.status(200).send(raw);
 }catch(e){if(res.headersSent)return res.destroy();const status=e instanceof ApiError?e.status:503;return res.status(status).json({ok:false,error:e instanceof ApiError?e.message:'Inventory is temporarily unavailable'});}
}
