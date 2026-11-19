import {api,download} from './api/client.js';
import {samplesStore} from './stores/samples.js';import {transfersStore} from './stores/transfers.js';import {trialsStore} from './stores/trials.js';import {signoffsStore} from './stores/signoffs.js';
import {session} from './hooks/session.js';import {feedback} from './hooks/feedback.js';import {formValues} from './utils/forms.js';
import {esc,date} from './utils/html.js';
import {frame} from './components/common/frame.js';import {table} from './components/common/table.js';import {loginPage} from './pages/login.js';import {samplesPage} from './pages/samples.js';import {transfersPage} from './pages/transfers.js';import {trialsPage} from './pages/trials.js';import {evidencePage} from './pages/evidence.js';import {titles} from './router/routes.js';
import {reconApi} from './api/signoffs.js';
import {transfersApi} from './api/transfers.js';
let timer=null;const app=document.querySelector('#app');
async function submitForm(id,path,numbers,after){document.querySelector(id)?.addEventListener('submit',async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{const data=await api(path,'POST',formValues(e.target,numbers));await after(data);feedback('操作完成，记录已保存。');}catch(err){feedback(err.message,true);}finally{button.disabled=false;}})}
async function render(){clearTimeout(timer);if(!session.token){app.innerHTML=loginPage();submitForm('#login','/auth/login',[],async data=>{session.save(data);location.hash='transfers';await render()});return;}
try{
  await api('/auth/me');
  await Promise.all([samplesStore.refresh(),transfersStore.refresh(),trialsStore.refresh(),signoffsStore.refresh()]);
  const [page='samples',id]=location.hash.slice(1).split('/');
  let content;const s=samplesStore.items,t=transfersStore.items,r=trialsStore.items;let trial=null;
  if(page==='transfers')content=transfersPage(t,s,signoffsStore.items,signoffsStore.jobs);
  else if(page==='trials')content=trialsPage(r,t);
  else if(page==='evidence'){trial=id?await api('/trials/'+id):r[0];content=evidencePage(trial);}
  else if(page==='audit'){const logs=await api('/audit');content=table(['时间','操作人','动作'],logs.map(x=>[date(x.created_at),esc(x.actor),esc(x.action)]));}
  else content=samplesPage(s);
  const [title,subtitle]=titles[page]||titles.samples;app.innerHTML=frame(page,title,subtitle,content);
  document.querySelector('#logout').onclick=()=>{session.clear();render();};
  submitForm('#sample-form','/samples',['quantity'],render);
  submitForm('#transfer-form','/transfers',['sample_id','quantity'],render);
  submitForm('#signoff-form','/signoffs',['quantity'],render);
  submitForm('#trial-form','/trials',['transfer_id','timeout_ms'],async data=>{location.hash='evidence/'+data.id;await render();});
  document.querySelector('#correction-form')?.addEventListener('submit',async e=>{
    e.preventDefault();const v=formValues(e.target,['transfer_id','quantity']);const btn=e.target.querySelector('button');btn.disabled=true;
    try{await transfersApi.correct(v.transfer_id,{destination:v.destination,quantity:v.quantity,note:v.note});
      feedback('更正已提交，按最新版本重新对账。');await render();}
    catch(err){feedback(err.message,true);}finally{btn.disabled=false;}
  });
  document.querySelectorAll('[data-retry]').forEach(btn=>btn.addEventListener('click',async()=>{
    btn.disabled=true;
    try{await reconApi.retry(btn.dataset.retry);feedback('已重新排队，对账结果幂等不会重复核销。');await render();}
    catch(err){feedback(err.message,true);}finally{btn.disabled=false;}
  }));
  document.querySelector('#export')?.addEventListener('click',async e=>{try{await download(e.target.dataset.id);feedback('证据 JSON 已下载');}catch(err){feedback(err.message,true);}});
  const reconActive=(page==='transfers')&&signoffsStore.jobs.some(x=>['queued','running'].includes(x.status));
  if((page==='evidence'&&trial&&['queued','running'].includes(trial.status))||(page==='trials'&&r.some(x=>['queued','running'].includes(x.status)))||reconActive)timer=setTimeout(render,1500);
}catch(err){if(err.message.includes('登录')||err.message.includes('Not authenticated')){session.clear();await render();}else{if(!document.querySelector('#feedback'))app.innerHTML='<main><div id="feedback"></div><button onclick="location.reload()">重新加载</button></main>';feedback(err.message,true);}}}
window.addEventListener('hashchange',render);render();
