import {signoffsApi,reconApi} from '../api/signoffs.js';
export const signoffsStore={
  items:[],jobs:[],
  async refresh(){
    [this.items,this.jobs]=await Promise.all([signoffsApi.list(),reconApi.jobs()]);
    return this.items;
  },
};
