import {createService,ApiError} from '../../lib/inventory-service.js';
import {blobStorage} from '../../lib/inventory-blob.js';
export default async function handler(req,res){
 res.setHeader('Cache-Control','private, no-store');res.setHeader('X-Content-Type-Options','nosniff');
 if(!['GET','POST'].includes(req.method)){res.setHeader('Allow','GET, POST');return res.status(405).end();}
 try{
  if(Number(req.headers['content-length']||0)>32768)throw new ApiError(413,'Management request is too large');
  const service=createService(blobStorage(),process.env.INVENTORY_ADMIN_TOKEN_SHA256);
  return res.status(200).json(await service.admin(req.headers.authorization,req.method==='GET'?null:req.body));
 }catch(e){const status=e instanceof ApiError?e.status:503;return res.status(status).json({ok:false,error:e instanceof ApiError?e.message:'Inventory hosting is temporarily unavailable'});}
}
