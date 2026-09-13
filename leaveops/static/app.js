'use strict';
const $ = id => document.getElementById(id);
const labels = {draft:'Черновик',submitted:'На рассмотрении',approved:'Согласовано',rejected:'Отклонено',cancelled:'Отменено',reschedule_submitted:'Перенос предложен',reschedule_approved:'Перенос согласован',reschedule_rejected:'Перенос отклонён'};
const roles = {analyst:'Аналитики',developer:'Разработчики',manager:'Руководитель'};
const state = {actor:'manager',actors:[],team:null,requests:[],summary:null,selected:null,busy:false,epoch:0,integrationName:'Bitrix24'};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const parseDate = value => new Date(value+'T12:00:00Z');
const iso = date => date.toISOString().slice(0,10);
const addDays = (value,n) => {const date=parseDate(value);date.setUTCDate(date.getUTCDate()+n);return iso(date);};
const shortDate = value => parseDate(value).toLocaleDateString('ru-RU',{day:'numeric',month:'short',timeZone:'UTC'});
const name = id => state.actors.find(a=>a.id===id)?.name || id;
const badge = status => `<span class="badge ${esc(status)}">${esc(labels[status])}</span>`;
function notify(message,error=false){$('feedback').hidden=false;$('feedback').className=error?'error':'';$('feedback').textContent=message;}
async function api(path,body){
  const response = await fetch(path,{method:body?'POST':'GET',headers:{'X-Demo-Actor':state.actor,...(body?{'Content-Type':'application/json','X-LeaveOps':'local-demo'}:{})},...(body?{body:JSON.stringify(body)}:{})});
  const data = await response.json();
  if(!response.ok){const error=new Error(data.message || 'Не удалось выполнить запрос');error.conflicts=data.conflicts||[];throw error;}
  return data;
}
function showTab(tab){
  for(const key of ['calendar','requests','hr']){
    const active=key===tab;$(`${key}-view`).hidden=!active;$(`tab-${key}`).classList.toggle('active',active);$(`tab-${key}`).setAttribute('aria-pressed',String(active));
  }
}
function dates(){return Array.from({length:7},(_,i)=>addDays($('week').value,i));}
function headerDays(days){return '<th scope="col">Сотрудник</th>'+days.map(d=>`<th scope="col" class="${parseDate(d).getUTCDay()===0||parseDate(d).getUTCDay()===6?'weekend':''}">${esc(parseDate(d).toLocaleDateString('ru-RU',{weekday:'short',timeZone:'UTC'}))}<strong>${parseDate(d).getUTCDate()}</strong></th>`).join('');}
function renderCalendar(){
  const days=dates(),team=state.team;
  $('range').textContent=`${shortDate(days[0])} — ${shortDate(days[6])} ${parseDate(days[6]).getUTCFullYear()}`;
  $('calendar').innerHTML=`<table aria-label="Отсутствия команды"><thead><tr>${headerDays(days)}</tr></thead><tbody>${team.employees.map(e=>`<tr><td class="employee">${esc(e.name)}<small>${esc(roles[e.role]||e.role)}</small></td>${days.map(d=>{
    const items=team.absences.filter(a=>a.employee_id===e.id&&a.start_date<=d&&d<=a.end_date);
    const weekend=[0,6].includes(parseDate(d).getUTCDay());
    return `<td class="${weekend?'weekend':''}">${items.length?items.map(a=>{
      const readable=state.actor===a.employee_id||state.actor===team.manager_id;
      const label=a.status==='approved'?'Отсутствует':'Ожидает решения';
      return readable?`<button class="day-state day-button ${a.status}" data-request="${esc(a.id)}" aria-label="${esc(e.name)}, ${esc(shortDate(d))}: ${label}">${label}</button>`:`<span class="day-state ${a.status}">${label}</span>`;
    }).join(''):`<span class="free" aria-label="${weekend?'Выходной':'Доступен'}">${weekend?'·':'—'}</span>`}</td>`;
  }).join('')}</tr>`).join('')}</tbody></table>`;
  const roleKeys=[...new Set(team.coverage.map(c=>c.role))];
  $('coverage').innerHTML=`<table aria-label="Доступные сотрудники и минимальное покрытие"><thead><tr>${headerDays(days).replace('Сотрудник','Роль')}</tr></thead><tbody>${roleKeys.map(role=>`<tr><td class="employee">${esc(roles[role]||role)}</td>${days.map(d=>{const c=team.coverage.find(c=>c.role===role&&c.date===d);return `<td class="${!c.working?'coverage-neutral weekend':c.available<c.required?'coverage-bad':'coverage-good'}">${c.working?`${c.available} / ${c.required}`:'Выходной'}</td>`;}).join('')}</tr>`).join('')}</tbody></table>`;
}
function renderRequests(){
  $('scope').textContent=state.actor===state.team.manager_id?'Заявки вашего отдела':'Ваши заявки';
  $('pending-count').textContent=state.requests.filter(r=>r.status==='submitted').length;
  const filtered=state.requests.filter(r=>$('filter').value==='all'||r.status===$('filter').value).sort((a,b)=>(a.status==='submitted'?-1:0)-(b.status==='submitted'?-1:0)||a.start_date.localeCompare(b.start_date));
  $('requests').innerHTML=filtered.length?filtered.map(r=>`<button class="request-row" data-request="${esc(r.id)}"><span><strong>${esc(name(r.employee_id))}</strong><small>${esc(shortDate(r.start_date))} — ${esc(shortDate(r.end_date))} ${parseDate(r.end_date).getUTCFullYear()}</small></span>${badge(r.status)}</button>`).join(''):'<div class="empty">Заявок с таким статусом пока нет.</div>';
}
function renderHrSummary(){
  const summary=state.summary;
  if(!summary)return;
  $('hr-as-of').textContent=`Расчёт на ${shortDate(summary.as_of)} ${parseDate(summary.as_of).getUTCFullYear()} · синтетическая демонстрационная модель`;
  $('hr-metrics').innerHTML=`<article class="metric"><span>Низкий остаток</span><strong>${summary.low_balance_employee_ids.length}</strong><small>меньше 5 дней</small></article><article class="metric"><span>Ожидают решения</span><strong>${summary.pending_requests}</strong><small>заявок отдела</small></article><article class="metric"><span>Ближайшие отсутствия</span><strong>${summary.upcoming_absences.length}</strong><small>после расчётной даты</small></article>`;
  $('hr-balances').innerHTML=`<table aria-label="Отпускные балансы сотрудников"><thead><tr><th scope="col">Сотрудник</th><th scope="col">Доступно</th><th scope="col">Использовано</th><th scope="col">Запланировано</th><th scope="col">Начислено</th></tr></thead><tbody>${summary.employees.map(row=>`<tr><td class="employee">${esc(row.name)}<small>${esc(roles[row.role]||row.role)}</small></td><td class="${row.available_days<5?'balance-low':'balance-good'}">${esc(row.available_days)}</td><td>${esc(row.used_days)}</td><td>${esc(row.planned_days)}</td><td>${esc(row.accrued_days)}</td></tr>`).join('')}</tbody></table>`;
  $('upcoming-absences').innerHTML=summary.upcoming_absences.length?summary.upcoming_absences.map(row=>`<div class="upcoming"><strong>${esc(row.name)}</strong><span>${esc(shortDate(row.start_date))} — ${esc(shortDate(row.end_date))} ${parseDate(row.end_date).getUTCFullYear()}</span></div>`).join(''):'<div class="empty">После выбранной даты согласованных отсутствий нет.</div>';
}
async function refresh(){
  const epoch=++state.epoch;
  const start=$('week').value;
  const [team,requests]=await Promise.all([api(`/api/calendar?start=${encodeURIComponent(start)}&end=${encodeURIComponent(addDays(start,6))}`),api('/api/requests')]);
  const summary=state.actor===team.manager_id?await api(`/api/hr-summary?as_of=${encodeURIComponent(addDays(start,6))}`):null;
  if(epoch!==state.epoch)return;
  state.team=team;state.requests=requests;state.summary=summary;$('tab-hr').hidden=!summary;if(!summary&&$('hr-view').hidden===false)showTab('calendar');renderCalendar();renderRequests();renderHrSummary();
}
function conflictText(c){if(c.code==='coverage')return `${shortDate(c.date)}: ${roles[c.role]||c.role} — доступно ${c.available}, необходимо ${c.required}.`;if(c.code==='balance')return `Недостаточно дней: доступно ${c.available}, требуется ${c.requested}.`;return `Пересечение с согласованным отсутствием: ${shortDate(c.start_date)} — ${shortDate(c.end_date)}.`;}
function integrationHtml(events,manager){
  if(!events.length)return '';
  const latest=events[events.length-1],operation={upsert:'создание',update:'обновление',cancel:'отмена'}[latest.operation]||latest.operation;
  const text=latest.status==='delivered'?`${state.integrationName}: ${operation} доставлена${latest.external_id?` · ${latest.external_id}`:''}`:`${state.integrationName}: ${operation} ожидает отправки${latest.last_error?` · предыдущая попытка: ${latest.last_error}`:''}`;
  return `<div class="integration ${latest.status}"><strong>Интеграция</strong><p>${esc(text)}</p>${manager&&latest.status!=='delivered'?'<button data-sync="true">Повторить синхронизацию</button>':''}</div>`;
}
async function openRequest(id){
  const epoch=state.epoch;
  const r=await api('/api/request?id='+encodeURIComponent(id));
  if(epoch!==state.epoch)return;
  state.selected=r;$('detail-title').textContent=name(r.employee_id);$('detail-error').textContent='';
  const owner=state.actor===r.employee_id,manager=!owner&&state.actor===state.team.manager_id;
  const proposal=r.reschedule;
  const actions=[];
  if(owner&&r.status==='draft')actions.push('<button class="primary" data-action="submitted">Отправить руководителю</button>');
  if(manager&&r.status==='submitted')actions.push(`<button class="primary" data-action="approved" ${r.conflicts.length?'disabled':''}>Согласовать</button>`,'<button class="danger" data-action="rejected">Отклонить</button>');
  if(owner&&r.status==='approved'&&proposal?.status!=='submitted')actions.push('<button data-reschedule-propose="true">Предложить перенос</button>');
  if(manager&&proposal?.status==='submitted')actions.push(`<button class="primary" data-reschedule-decision="approved" ${r.reschedule_conflicts.length?'disabled':''}>Согласовать перенос</button>`,'<button class="danger" data-reschedule-decision="rejected">Отклонить перенос</button>');
  if(owner&&['draft','submitted','approved'].includes(r.status))actions.push('<button data-action="cancelled">Отменить заявку</button>');
  const projection=r.balance_projection?`<div class="balance-projection"><strong>Прогноз отпуска</strong><p>До согласования: ${esc(r.balance_projection.before_approval.available_days)} дн. · Заявка: ${esc(r.balance_projection.request_days)} дн. · После согласования: ${esc(r.balance_projection.after_approval_days)} дн.</p></div>`:'';
  const proposalHtml=proposal?`<div class="reschedule ${proposal.status}"><strong>${proposal.status==='submitted'?'Предложен перенос':labels['reschedule_'+proposal.status]}</strong><p><span>Действующий период</span> ${esc(shortDate(proposal.current_start_date))} — ${esc(shortDate(proposal.current_end_date))}</p><p><span>Предложенный период</span> ${esc(shortDate(proposal.start_date))} — ${esc(shortDate(proposal.end_date))}</p>${proposal.reason?`<p class="muted">Комментарий сотрудника: ${esc(proposal.reason)}</p>`:''}${proposal.status==='submitted'?(r.reschedule_conflicts.length?`<div class="conflict"><strong>Есть препятствия для переноса</strong><ul>${r.reschedule_conflicts.map(c=>`<li>${esc(conflictText(c))}</li>`).join('')}</ul></div>`:'<div class="clear">Для нового периода нет пересечений, нехватки покрытия и баланса. При решении проверим ещё раз.</div>'):(proposal.decision_reason?`<p class="muted">Решение руководителя: ${esc(proposal.decision_reason)}</p>`:'')}</div>`:'';
  $('detail').innerHTML=`${badge(r.status)}<p class="detail-dates">${esc(shortDate(r.start_date))} — ${esc(shortDate(r.end_date))} ${parseDate(r.end_date).getUTCFullYear()}</p>${projection}${proposalHtml}${['draft','submitted'].includes(r.status)?r.conflicts.length?`<div class="conflict"><strong>Есть препятствия для согласования</strong><ul>${r.conflicts.map(c=>`<li>${esc(conflictText(c))}</li>`).join('')}</ul></div>`:'<div class="clear">Пересечений, нехватки покрытия и баланса нет. При согласовании проверим ещё раз.</div>':''}${integrationHtml(r.integration,manager)}${owner&&state.actor===state.team.manager_id?'<p class="muted">Для собственной заявки руководителя в демо нет отдельного согласующего.</p>':''}${actions.length?'<label class="reason">Комментарий <span class="muted">Обязателен при отклонении</span><textarea id="reason" maxlength="1000"></textarea></label>':''}<div class="actions">${actions.join('')}</div><h2 class="history-title">История действий</h2><ol class="history">${r.history.map(h=>`<li><strong>${esc(labels[h.to_status]||h.to_status)}</strong> · ${esc(name(h.actor_id))}<small>${esc(new Date(h.created_at).toLocaleString('ru-RU'))}</small>${h.reason?`<p>${esc(h.reason)}</p>`:''}</li>`).join('')}</ol>`;
  if(!$('detail-dialog').open)$('detail-dialog').showModal();
}
async function transition(target){
  if(state.busy)return;
  const reason=$('reason')?.value||'';
  if(target==='rejected'&&!reason.trim()){$('detail-error').textContent='Укажите причину отклонения.';$('reason').focus();return;}
  state.busy=true;document.querySelectorAll('[data-action]').forEach(b=>b.disabled=true);
  const id=state.selected.id;
  try{await api('/api/transition',{id,target,reason});await refresh();await openRequest(id);notify(`Заявка: ${labels[target].toLowerCase()}.`);}
  catch(error){try{await refresh();await openRequest(id);}catch{}$('detail-error').textContent=[error.message,...(error.conflicts||[]).map(conflictText)].join(' ');}
  finally{state.busy=false;}
}
async function decideReschedule(target){
  if(state.busy)return;
  const reason=$('reason')?.value||'';
  if(target==='rejected'&&!reason.trim()){$('detail-error').textContent='Укажите причину отклонения переноса.';$('reason').focus();return;}
  state.busy=true;document.querySelectorAll('[data-reschedule-decision]').forEach(b=>b.disabled=true);
  const id=state.selected.id;
  try{await api('/api/reschedule/decision',{id,target,reason});await refresh();await openRequest(id);notify(`Перенос: ${target==='approved'?'согласован':'отклонён'}.`);}
  catch(error){try{await refresh();await openRequest(id);}catch{}$('detail-error').textContent=[error.message,...(error.conflicts||[]).map(conflictText)].join(' ');}
  finally{state.busy=false;}
}
function openReschedule(){
  const r=state.selected;$('reschedule-error').textContent='';$('reschedule-start').value=r.start_date;$('reschedule-end').value=r.end_date;$('reschedule-reason').value='';$('reschedule-dialog').showModal();
}
async function syncIntegration(){
  if(state.busy)return;
  state.busy=true;
  try{const results=await api('/api/sync',{});await openRequest(state.selected.id);notify(results.length?'Синхронизация выполнена.':'Нет событий для синхронизации.');}
  catch(error){$('detail-error').textContent=error.message;}
  finally{state.busy=false;}
}
$('tab-calendar').onclick=()=>showTab('calendar');$('tab-requests').onclick=()=>showTab('requests');$('tab-hr').onclick=()=>showTab('hr');$('filter').onchange=renderRequests;
document.addEventListener('click',event=>{
  const close=event.target.closest('[data-close]');if(close&&!state.busy)$(close.dataset.close).close();
  const request=event.target.closest('[data-request]');if(request)openRequest(request.dataset.request).catch(e=>notify(e.message,true));
  const action=event.target.closest('[data-action]');if(action)transition(action.dataset.action);
  const proposal=event.target.closest('[data-reschedule-propose]');if(proposal)openReschedule();
  const decision=event.target.closest('[data-reschedule-decision]');if(decision)decideReschedule(decision.dataset.rescheduleDecision);
  const sync=event.target.closest('[data-sync]');if(sync)syncIntegration();
});
for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('cancel',event=>{if(state.busy)event.preventDefault();});
$('actor').onchange=async()=>{state.actor=$('actor').value;state.selected=null;state.summary=null;$('feedback').hidden=true;for(const dialog of document.querySelectorAll('dialog'))dialog.close();$('calendar').textContent='Загрузка…';$('coverage').textContent='';$('requests').textContent='Загрузка…';try{await refresh();}catch(e){notify(e.message,true);}};
async function changeWeek(offset=0){if(!$('week').value||!$('week').checkValidity()){notify('Выберите корректную неделю.',true);return;}if(offset)$('week').value=addDays($('week').value,offset);try{await refresh();}catch(e){notify(e.message,true);}}
$('week').onchange=()=>changeWeek();$('prev').onclick=()=>changeWeek(-7);$('next').onclick=()=>changeWeek(7);
$('new').onclick=()=>{$('create-error').textContent='';$('create-owner').textContent=name(state.actor);$('start').value=$('week').value;$('end').value=$('week').value;$('create-dialog').showModal();};
$('create-form').onsubmit=async event=>{
  event.preventDefault();if(state.busy)return;
  if($('start').value>$('end').value){$('create-error').textContent='Последний день не может быть раньше первого.';return;}
  state.busy=true;const button=event.submitter;button.disabled=true;
  try{const row=await api('/api/requests',{start:$('start').value,end:$('end').value});$('create-dialog').close();await refresh();showTab('requests');await openRequest(row.id);notify('Черновик создан. Проверьте даты и отправьте заявку руководителю.');}
  catch(error){if($('create-dialog').open)$('create-error').textContent=error.message;else notify(error.message,true);}
  finally{state.busy=false;button.disabled=false;}
};
$('reschedule-form').onsubmit=async event=>{
  event.preventDefault();if(state.busy)return;
  const start=$('reschedule-start').value,end=$('reschedule-end').value;
  if(start>end){$('reschedule-error').textContent='Последний день не может быть раньше первого.';return;}
  state.busy=true;const button=event.submitter;button.disabled=true;const id=state.selected.id;
  try{await api('/api/reschedule',{id,start,end,reason:$('reschedule-reason').value});$('reschedule-dialog').close();await refresh();await openRequest(id);notify('Новый период отправлен руководителю на повторное согласование.');}
  catch(error){$('reschedule-error').textContent=error.message;}
  finally{state.busy=false;button.disabled=false;}
};
async function init(){
  try{const [actors,config]=await Promise.all([api('/api/actors'),api('/api/config')]);state.actors=actors;state.integrationName=config.integration_name;$('integration-mode').textContent=state.integrationName;if(!state.actors.length)throw new Error('База пуста. Выполните python -m leaveops init и обновите страницу.');if(!state.actors.some(a=>a.id===state.actor))state.actor=state.actors[0].id;
    $('actor').innerHTML=state.actors.map(a=>`<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');$('actor').value=state.actor;await refresh();$('actor').disabled=false;$('new').disabled=false;
  }catch(error){$('calendar').textContent='Не удалось загрузить календарь.';notify(error.message,true);}
}
init();
