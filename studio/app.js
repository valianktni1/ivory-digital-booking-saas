const $ = (q) => document.querySelector(q);
const csrf = () => document.cookie.split('; ').find(v => v.startsWith('ivory_booking_csrf='))?.split('=')[1] || '';
let dashboard;

async function api(path, options = {}) {
  const headers = {...(options.body ? {'Content-Type':'application/json'} : {}), ...(options.headers || {})};
  if (!['GET','HEAD'].includes((options.method || 'GET').toUpperCase())) headers['X-CSRF-Token'] = csrf();
  const response = await fetch(path, {...options, headers, credentials:'same-origin'});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || 'Something went wrong. Please try again.');
  return body;
}

function toast(message, bad=false){const el=$('#toast');el.textContent=message;el.classList.toggle('error',bad);el.classList.remove('hidden');setTimeout(()=>el.classList.add('hidden'),3500)}
function initials(name){return name.split(/\s+/).filter(Boolean).slice(0,2).map(x=>x[0]).join('').toUpperCase()}
function greeting(){const h=new Date().getHours();return h<12?'Good morning':h<17?'Good afternoon':'Good evening'}
function daysLeft(value){return Math.max(0,Math.ceil((new Date(value)-new Date())/86400000))}

async function showInvitation(token){
  try{
    const invite=await api(`/api/invitations/${encodeURIComponent(token)}`);
    $('#auth-card').innerHTML=`<div class="eyebrow">YOU'RE INVITED</div><h1>Welcome to ${escapeHtml(invite.business_name)}</h1><p class="lead">Create your private login and your 30-day trial will be ready to explore.</p><form id="invite-form"><label>Your name<input id="invite-name" autocomplete="name" required minlength="2"></label><label>Email address<input value="${escapeHtml(invite.email)}" disabled></label><label>Create a password<input id="invite-password" type="password" autocomplete="new-password" required minlength="14"><small>At least 14 characters, including upper and lowercase letters, a number and a symbol.</small></label><p id="auth-error" class="error"></p><button class="button primary wide" type="submit">Create my studio</button></form><p class="secure-note">This personal invitation expires automatically and can only be used once.</p>`;
    $('#invite-form').addEventListener('submit',async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;button.textContent='Preparing your studio…';try{await api(`/api/invitations/${encodeURIComponent(token)}/accept`,{method:'POST',body:JSON.stringify({full_name:$('#invite-name').value,password:$('#invite-password').value})});history.replaceState({},'',location.pathname);await openStudio()}catch(err){$('#auth-error').textContent=err.message;button.disabled=false;button.textContent='Create my studio'}});
  }catch(err){$('#auth-card').innerHTML=`<div class="eyebrow">INVITATION HELP</div><h1>That link is no longer available</h1><p class="lead">${escapeHtml(err.message)}. Ask Ivory Digital for a fresh setup invitation.</p>`}
}

function escapeHtml(value=''){const d=document.createElement('div');d.textContent=value;return d.innerHTML}

function render(){
  const {user,tenant,onboarding}=dashboard;
  $('#profile-name').textContent=user.full_name;$('#profile-role').textContent=user.role;$('#initials').textContent=initials(user.full_name);
  $('#top-business').textContent=tenant.display_name;$('#greeting').textContent=`${greeting()}, ${user.full_name.split(' ')[0]}`;
  $('#today').textContent=new Intl.DateTimeFormat('en-GB',{weekday:'long',day:'numeric',month:'long'}).format(new Date());
  const days=daysLeft(tenant.trial_ends_at);$('#trial-days').textContent=days;$('#trial-chip').textContent=`${days} day${days===1?'':'s'} left in trial`;
  const steps=[
    ['business','Business details','Add your trading name and the essentials couples should see.','✦',true],
    ['branding','Brand and welcome','Choose your colour and write a warm client welcome.','◈',true],
    ['packages','Packages and pricing','Build the services couples can choose from.','£',false],
    ['templates','Emails and workflow','Create your own timings and message style.','✉',false],
    ['calendar','Connect your calendar','Keep every confirmed date together.','□',false],
    ['ready','Review and go live','We will check everything with you before anything sends.','✓',false]
  ];
  const complete=steps.filter(s=>onboarding[s[0]]).length;const pct=Math.round(complete/steps.length*100);
  $('#progress-bar').style.width=`${pct}%`;$('#progress-label').textContent=`${pct}% ready`;
  $('#checklist').innerHTML=steps.map(([key,title,copy,icon,available])=>`<div class="setup-item ${onboarding[key]?'done':''}"><span class="setup-icon">${onboarding[key]?'✓':icon}</span><div><strong>${title}</strong><small>${onboarding[key]?'Complete':copy}</small></div><button data-step="${key}" ${available?'':'disabled'}>${onboarding[key]?'Review':available?'Set up':'Coming soon'}</button></div>`).join('');
  document.querySelectorAll('[data-step="business"],[data-step="branding"]').forEach(b=>b.addEventListener('click',()=>showSection('brand')));
  const brand=tenant.branding||{};$('#business-name').value=brand.display_name||tenant.display_name;$('#accent').value=brand.accent_colour||'#a9782e';$('#accent-text').value=brand.accent_colour||'#a9782e';$('#welcome-message').value=brand.welcome_message||'Welcome to your private booking area.';updatePreview();
}

function updatePreview(){const colour=$('#accent-text').value;$('#preview-name').textContent=$('#business-name').value||'Your business';$('#preview-message').textContent=$('#welcome-message').value||'Welcome to your private booking area.';if(/^#[0-9a-f]{6}$/i.test(colour))document.documentElement.style.setProperty('--preview',colour);$('.portal-preview button').style.background=colour}
function showSection(name){$('#home-section').classList.toggle('hidden',name!=='home');$('#brand-section').classList.toggle('hidden',name!=='brand');document.querySelectorAll('[data-section]').forEach(b=>b.classList.toggle('active',b.dataset.section===name));closeMenu();scrollTo({top:0,behavior:'smooth'})}
function closeMenu(){$('#sidebar').classList.remove('open');$('#scrim').classList.add('hidden')}

async function openStudio(){
  try{dashboard=await api('/api/studio/dashboard');$('#auth-view').classList.add('hidden');$('#studio-view').classList.remove('hidden');render()}
  catch{$('#auth-view').classList.remove('hidden');$('#studio-view').classList.add('hidden')}
}

$('#login-form').addEventListener('submit',async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;button.textContent='Opening your studio…';$('#auth-error').textContent='';try{const result=await api('/api/auth/login',{method:'POST',body:JSON.stringify({email:$('#email').value,password:$('#password').value})});if(result.destination!=='studio')throw new Error('Please use Ivory Digital Manager for this account.');await openStudio()}catch(err){$('#auth-error').textContent=err.message;button.disabled=false;button.textContent='Open my studio'}});
$('#brand-form').addEventListener('input',e=>{if(e.target.id==='accent')$('#accent-text').value=e.target.value;if(e.target.id==='accent-text'&&/^#[0-9a-f]{6}$/i.test(e.target.value))$('#accent').value=e.target.value;updatePreview()});
$('#brand-form').addEventListener('submit',async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;b.textContent='Saving…';try{await api('/api/studio/branding',{method:'PATCH',body:JSON.stringify({display_name:$('#business-name').value,accent_colour:$('#accent-text').value,welcome_message:$('#welcome-message').value})});dashboard=await api('/api/studio/dashboard');render();toast('Your business details are saved.');showSection('home')}catch(err){$('#brand-error').textContent=err.message}finally{b.disabled=false;b.textContent='Save business details'}});
$('#logout').addEventListener('click',async()=>{try{await api('/api/auth/logout',{method:'POST'})}finally{location.reload()}});$('#menu').addEventListener('click',()=>{$('#sidebar').classList.add('open');$('#scrim').classList.remove('hidden')});$('#scrim').addEventListener('click',closeMenu);$('#back-home').addEventListener('click',()=>showSection('home'));document.querySelectorAll('[data-section]').forEach(b=>b.addEventListener('click',()=>showSection(b.dataset.section)));

const invite=new URLSearchParams(location.search).get('invite');if(invite)showInvitation(invite);else openStudio();
