import {api} from './client.js';
export const signoffsApi={
  list:(transferCode)=>api('/signoffs'+(transferCode?`?transfer_code=${encodeURIComponent(transferCode)}`:'')),
  register:data=>api('/signoffs','POST',data),
};
export const reconApi={
  jobs:()=>api('/recon/jobs'),
  retry:id=>api(`/recon/jobs/${id}/retry`,'POST'),
};
