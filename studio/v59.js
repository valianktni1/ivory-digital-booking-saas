// Phase 5.9: explicit business context and owner payment controls.
let seatBusinesses=[];
async function loadBusinessAccess(){
  const seat=await api('/api/studio/businesses');
  businessSelection=seat.selected;seatBusinesses=seat.businesses;
  const chosen=seatBusinesses.find(b=>b.id===businessSelection);
  return chosen;
}
function businessOptions(){return seatBusinesses.map(b=>`<option value="${escapeAttr(b.id)}" ${b.id===businessSelection?'selected':''}>${escapeHtml(b.name)}${b.status==='suspended'?' — payment/access required':''}</option>`).join('')}
async function switchBusiness(id){
  if(id===businessSelection)return;
  if(!confirm('Switch business? Save any unfinished edits before continuing.'))return;
  try{await api('/api/studio/businesses/switch',{method:'POST',body:JSON.stringify({tenant_id:id})});location.assign('/')}catch(err){toast(err.message,true)}
}
function mountBusinessControls(){
  let bar=document.querySelector('#business-controls');
  if(!bar){bar=document.createElement('div');bar.id='business-controls';document.querySelector('#studio-view .topbar').insertAdjacentElement('afterend',bar)}
  bar.innerHTML=`<label>Current business <select id="business-switch" aria-label="Current business">${businessOptions()}</select></label>${seatBusinesses.find(b=>b.id===businessSelection)?.role==='owner'?'<button type="button" class="button quiet" id="business-payments">Payments & subscription</button>':''}`;
  bar.querySelector('select').onchange=e=>switchBusiness(e.target.value);
  bar.querySelector('button')?.addEventListener('click',()=>openPayments());
}
async function showBusinessGate(){
  $('#auth-view').classList.add('hidden');$('#studio-view').classList.add('hidden');
  let gate=$('#business-access');if(!gate){gate=document.createElement('main');gate.id='business-access';document.body.append(gate)}
  const b=seatBusinesses.find(b=>b.id===businessSelection);
  gate.innerHTML=`<span class="eyebrow">YOUR BUSINESSES</span><h1>${escapeHtml(b?.name||'Business access')}</h1><p>This business needs an active subscription or access granted by Ivory Digital. Your saved work is retained.</p><label>Choose business<select id="gate-business">${businessOptions()}</select></label>${b?.role==='owner'?'<button class="button primary" id="gate-pay">Payments & subscription</button>':'<p>Ask your business owner to arrange access.</p>'}<button class="button quiet" id="gate-logout">Sign out</button>`;
  gate.querySelector('select').onchange=e=>switchBusiness(e.target.value);
  gate.querySelector('#gate-pay')?.addEventListener('click',()=>openPayments());
  gate.querySelector('#gate-logout').onclick=async()=>{await api('/api/auth/logout',{method:'POST'});location.reload()};
}
async function openPayments(){
  try{
    const data=await api('/api/studio/payments/settings'),s=data.subscription;
    let dialog=$('#stripe-settings');if(!dialog){dialog=document.createElement('dialog');dialog.id='stripe-settings';dialog.className='editor';document.body.append(dialog)}
    dialog.innerHTML=`<div class="payment-settings-body"><header><div><span class="eyebrow">${escapeHtml(seatBusinesses.find(b=>b.id===businessSelection)?.name||'YOUR BUSINESS')}</span><h2>Payments & subscription</h2></div><button type="button" id="close-stripe" aria-label="Close">×</button></header><section><h3>Your Ivory subscription</h3><p>${escapeHtml(s.plan_name)} · ${money(s.price_pence)} / ${escapeHtml(s.billing_cycle)}<br>Status: ${escapeHtml(s.billing_status.replaceAll('_',' '))}</p>${s.provider==='complimentary'?'<p>Ivory Digital has granted this business free access.</p>':data.has_stripe_subscription&&!['canceled','incomplete_expired'].includes(s.billing_status)?'<button type="button" class="button primary" data-stripe-action="subscription-portal">Manage subscription in Stripe</button>':s.price_pence>0?`<button type="button" class="button primary" data-stripe-action="subscription-checkout" ${data.configured?'':'disabled'}>Subscribe — ${money(s.price_pence)} / ${escapeHtml(s.billing_cycle)}</button><p>You will review and authorise the recurring charge in Stripe.</p>`:'<p>No card subscription charge is configured. Contact Ivory Digital for your plan.</p>'}${data.subscription_pending&&data.has_stripe_subscription?'<button type="button" class="button quiet" data-stripe-action="subscription-checkout">Resume pending subscription checkout</button>':''}<p>You can also arrange subscription payment with Ivory Digital by bank transfer.</p></section><hr><section><h3>Payments from couples</h3><p>Bank transfer remains available using the bank details saved with each invoice. Connect this business’s own Stripe account to offer card payment for booking fees and balances.</p>${!data.configured?'<p>Stripe setup is awaiting Ivory Digital. Bank transfer continues as usual.</p>':`<p>${data.test_mode?'Stripe is in TEST MODE. No real card payments will be taken.':'Stripe is in live mode.'}</p>${data.connected?`<button type="button" class="button quiet" data-stripe-action="connect">Reconnect original Stripe account</button><p>${data.enabled?'Card payments enabled':'Card payments disabled'}${!data.charges_enabled?' — complete Stripe account setup first':''}</p><button type="button" class="button primary" id="toggle-card">${data.enabled?'Disable':'Enable'} card payments</button>`:`<button type="button" class="button primary" data-stripe-action="connect" ${data.connect_available?'':'disabled'}>Connect this business’s Stripe</button>`}`}</section><p class="error" id="stripe-error" role="alert"></p></div>`;
    dialog.querySelector('#close-stripe').onclick=()=>dialog.close();
    dialog.querySelectorAll('[data-stripe-action]').forEach(button=>button.onclick=async()=>{button.disabled=true;try{const r=await api('/api/studio/payments/'+button.dataset.stripeAction,{method:'POST'});location.assign(r.url)}catch(err){dialog.querySelector('#stripe-error').textContent=err.message;button.disabled=false}});
    dialog.querySelector('#toggle-card')?.addEventListener('click',async e=>{e.target.disabled=true;try{await api('/api/studio/payments/enabled',{method:'POST',body:JSON.stringify({enabled:!data.enabled})});dialog.close();await openPayments()}catch(err){dialog.querySelector('#stripe-error').textContent=err.message;e.target.disabled=false}});
    if(!dialog.open)dialog.showModal();
  }catch(err){toast(err.message,true)}
}
const renderBefore59=render;
render=function(){renderBefore59();mountBusinessControls()};

if(typeof guidedTours!=='undefined')guidedTours.home.push(['#business-controls','Your businesses and payments','Choose the current business here. Owners can open Payments & subscription for card payments and their Ivory plan. Bank transfer remains available.']);
