import {api} from './client.js';
export const transfersApi={
  list:()=>api('/transfers'),
  create:data=>api('/transfers','POST',data),
  correct:(id,data)=>api('/transfers/'+id,'PUT',data),
};
