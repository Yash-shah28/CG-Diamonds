import {createService,ApiError} from '../../lib/inventory-service.js';
import {blobStorage} from '../../lib/inventory-blob.js';
export default async function handler(req,res){
 res.setHeader('Cache-Control','private, no-store');
 res.setHeader('X-Content-Type-Options','nosniff');
 res.setHeader('Access-Control-Allow-Origin','*');
 res.setHeader('Access-Control-Allow-Methods','GET, POST, OPTIONS');
 res.setHeader('Access-Control-Allow-Headers','Content-Type, x-api-key, Authorization');

 if(req.method==='OPTIONS')return res.status(204).end();

 const key=req.headers['x-api-key']||(req.headers.authorization?.startsWith('Bearer ')?req.headers.authorization.slice(7):null);
 const clientIp=req.headers['x-forwarded-for']?.split(',')[0].trim()||req.headers['x-real-ip']||req.socket?.remoteAddress||'unknown';
 const userAgent=req.headers['user-agent']||'unknown';

 if(req.method==='POST'){
  try{
   if(Number(req.headers['content-length']||0)>524288)throw new ApiError(413,'Feedback payload is too large');
   let body=req.body;
   if(typeof body==='string'){try{body=JSON.parse(body);}catch{throw new ApiError(400,'Invalid JSON body');}}
   const service=createService(blobStorage(),process.env.INVENTORY_ADMIN_TOKEN_SHA256);
   const result=await service.feedback(key,body);
   const companyLabel=result.company_name?`"${result.company_name}" (${result.client_id})`:`ID: ${result.client_id}`;
   console.log(`[API FEEDBACK] Company: ${companyLabel} | IP: ${clientIp} | Succeeded: ${result.summary.success_count} | Failed: ${result.summary.failed_count} | Tool: ${userAgent}`);
   return res.status(200).json(result);
  }catch(e){
   console.warn(`[API FEEDBACK ERROR] IP: ${clientIp} | Tool: ${userAgent} | Error: ${e.message}`);
   const status=e instanceof ApiError?e.status:503;
   return res.status(status).json({ok:false,error:e instanceof ApiError?e.message:'Inventory service temporarily unavailable'});
  }
 }

 if(req.method!=='GET'){res.setHeader('Allow','GET, POST, OPTIONS');return res.status(405).end();}
 try{
  const service=createService(blobStorage(),process.env.INVENTORY_ADMIN_TOKEN_SHA256);
  const result=await service.inventory(key);
  const companyLabel=result.company_name?`"${result.company_name}" (${result.client_id})`:`ID: ${result.client_id}`;
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
  let naturalCount=0;
  let labGrownCount=0;
  if(data&&Array.isArray(data.diamonds)){
   const natural=[];
   const lab_grown=[];
   for(const d of data.diamonds){
    if(d&&d['Diamond Type']==='LAB_GROWN')lab_grown.push(d);
    else natural.push(d);
   }
   naturalCount=natural.length;
   labGrownCount=lab_grown.length;
   data={natural,lab_grown};
   console.log(`[INVENTORY FETCH] Company: ${companyLabel} | IP: ${clientIp} | Tool: ${userAgent} | Natural: ${naturalCount} | Lab Grown: ${labGrownCount} | Total: ${naturalCount+labGrownCount}`);
   service.recordAccess(result.client_id,{
    ip:clientIp,
    userAgent,
    company_name:result.company_name,
    naturalCount,
    labGrownCount,
    totalCount:naturalCount+labGrownCount
   }).catch(()=>{});
   return res.status(200).send(JSON.stringify(data));
  }else if(data&&(Array.isArray(data.natural)||Array.isArray(data.lab_grown))){
   naturalCount=Array.isArray(data.natural)?data.natural.length:0;
   labGrownCount=Array.isArray(data.lab_grown)?data.lab_grown.length:0;
   console.log(`[INVENTORY FETCH] Company: ${companyLabel} | IP: ${clientIp} | Tool: ${userAgent} | Natural: ${naturalCount} | Lab Grown: ${labGrownCount} | Total: ${naturalCount+labGrownCount}`);
   service.recordAccess(result.client_id,{
    ip:clientIp,
    userAgent,
    company_name:result.company_name,
    naturalCount,
    labGrownCount,
    totalCount:naturalCount+labGrownCount
   }).catch(()=>{});
   return res.status(200).send(raw);
  }
  service.recordAccess(result.client_id,{
   ip:clientIp,
   userAgent,
   company_name:result.company_name,
   naturalCount:0,
   labGrownCount:0,
   totalCount:result.records
  }).catch(()=>{});
  console.log(`[INVENTORY FETCH] Company: ${companyLabel} | IP: ${clientIp} | Tool: ${userAgent} | Total: ${result.records}`);
  return res.status(200).send(raw);
 }catch(e){
  console.warn(`[INVENTORY FETCH FAILED] IP: ${clientIp} | Tool: ${userAgent} | Error: ${e.message}`);
  if(res.headersSent)return res.destroy();
  const status=e instanceof ApiError?e.status:503;
  return res.status(status).json({ok:false,error:e instanceof ApiError?e.message:'Inventory is temporarily unavailable'});
 }
}
