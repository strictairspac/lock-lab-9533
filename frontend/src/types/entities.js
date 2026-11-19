/** Sample:{id,code,title,quantity,location};
 *  Transfer:{id,code,sample_id,destination,quantity,note,version,verified_qty,signoff_qty,pending_count,pending_reasons};
 *  Signoff:{id,code,transfer_code,quantity,verified_qty,status,pending_reason,pending_reason_label,has_transfer};
 *  ReconJob:{id,transfer_code,reason,status,attempts,error};
 *  Trial:{id,name,transfer_id,timeout_ms,status,result,error} */
export const entityNames=['samples','transfers','signoffs','recon_jobs','trials'];
