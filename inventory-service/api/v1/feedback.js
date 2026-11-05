import {createService,ApiError} from '../../lib/inventory-service.js';
import {blobStorage} from '../../lib/inventory-blob.js';

export default async function handler(req,res){
 res.setHeader('Cache-Control','private, no-store');
 res.setHeader('X-Content-Type-Options','nosniff');
 res.setHeader('Access-Control-Allow-Origin','*');
 res.setHeader('Access-Control-Allow-Methods','POST, OPTIONS');
 res.setHeader('Access-Control-Allow-Headers','Content-Type, x-api-key, Authorization');

 if(req.method==='OPTIONS'){
  return res.status(204).end();
 }

 if(req.method!=='POST'){
  res.setHeader('Allow','POST, OPTIONS');
  return res.status(405).json({ok:false,error:'Method not allowed. Use POST to submit feedback.'});
 }

 try{
  if(Number(req.headers['content-length']||0)>524288){
   throw new ApiError(413,'Feedback payload is too large (max 512 KB)');
  }
  const key=req.headers['x-api-key']||(req.headers.authorization?.startsWith('Bearer ')?req.headers.authorization.slice(7):null);
  if(!key)throw new ApiError(401,'Unauthorized: missing API key in x-api-key or Authorization header');

  let body=req.body;
  if(typeof body==='string'){
   try{body=JSON.parse(body);}catch{throw new ApiError(400,'Invalid JSON body');}
  }

  const service=createService(blobStorage(),process.env.INVENTORY_ADMIN_TOKEN_SHA256);
  const result=await service.feedback(key,body);
  return res.status(200).json(result);
 }catch(e){
  const status=e instanceof ApiError?e.status:503;
  return res.status(status).json({ok:false,error:e instanceof ApiError?e.message:'Inventory feedback is temporarily unavailable'});
 }
}
