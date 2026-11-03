import {get,put,list,issueSignedToken,presignUrl,BlobNotFoundError} from '@vercel/blob';
import {ApiError} from './inventory-service.js';
export function blobStorage(env=process.env,sdk={get,put,list,issueSignedToken,presignUrl}){
 const storeId=env.INVENTORY_BLOB_STORE_ID;
 // A distinct private store prevents accidental exposure through installer storage.
 if(!storeId||storeId.replace(/^store_/,'')===(env.BLOB_STORE_ID||'').replace(/^store_/,''))throw new ApiError(503,'A separate private inventory store is required');
 const options={storeId};
 async function read(path){try{return await sdk.get(path,{...options,access:'private',useCache:false});}catch(e){if(e instanceof BlobNotFoundError)return null;throw e;}}
 async function collect(path,max){
  const result=await read(path);if(!result?.stream)throw new ApiError(503,'Private inventory is unavailable');
  const reader=result.stream.getReader();const chunks=[];let count=0;
  try{while(true){const {done,value}=await reader.read();if(done)break;count+=value.length;if(count>max){await reader.cancel();throw new ApiError(413,'Stored object is too large');}chunks.push(Buffer.from(value));}}
  finally{reader.releaseLock();}
  return {body:Buffer.concat(chunks),etag:result.blob.etag};
 }
 return {
  async ready(){await sdk.list({...options,prefix:'inventory/control/',limit:1});return true;},
  async json(path){const result=await read(path);if(!result)return null;if(!result.stream)throw new ApiError(503,'Private access record unavailable');const reader=result.stream.getReader();let count=0;const chunks=[];try{while(true){const {done,value}=await reader.read();if(done)break;count+=value.length;if(count>65536){await reader.cancel();throw new ApiError(503,'Access record invalid');}chunks.push(Buffer.from(value));}}finally{reader.releaseLock();}return {value:JSON.parse(Buffer.concat(chunks).toString('utf8')),etag:result.blob.etag};},
  async putJson(path,value,etag){
   try{return await sdk.put(path,JSON.stringify(value),{...options,access:'private',addRandomSuffix:false,allowOverwrite:Boolean(etag),...(etag?{ifMatch:etag}:{}),contentType:'application/json',cacheControlMaxAge:60});}
   catch(e){if(e.constructor.name==='BlobPreconditionFailedError'||e.message?.includes('already exists'))throw new ApiError(409,'Concurrent change; refresh and retry');throw e;}
  },
  async authorize(path,size){const delegation=await sdk.issueSignedToken({...options,pathname:path,operations:['put'],maximumSizeInBytes:size,allowedContentTypes:['application/json'],validUntil:Date.now()+600000});const {presignedUrl}=await sdk.presignUrl(delegation,{operation:'put',pathname:path,access:'private',addRandomSuffix:false,allowOverwrite:false,maximumSizeInBytes:size,allowedContentTypes:['application/json']});return presignedUrl;},
  async bytes(path,max){return (await collect(path,max)).body;},
  async stream(path){const result=await sdk.get(path,{...options,access:'private',useCache:true});if(!result?.stream)throw new ApiError(503,'Private inventory unavailable');return result.stream;}
 };
}
