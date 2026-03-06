let token = localStorage.getItem('gmf_token') || '';
let currentUser = null;

function el(id){ return document.getElementById(id); }
function status(msg, isErr=false){ const s=el('status-box'); if(!s) return; s.textContent=msg; s.className = isErr ? 'notice error' : 'notice'; }
function esc(v){
  return String(v ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function setVisible(node, show){ if(node) node.style.display = show ? '' : 'none'; }

function card(k,v){ return `<div class="item"><h4>${esc(k)}</h4><p>${esc(v)}</p></div>`; }
function renderObject(containerId, obj){ const c=el(containerId); if(!c) return; c.innerHTML=''; Object.entries(obj||{}).forEach(([k,v])=>{ c.innerHTML += card(k, typeof v==='object' ? JSON.stringify(v) : v); }); if(Object.keys(obj||{}).length===0) c.innerHTML='<p class="muted">No data.</p>'; }
function renderArray(containerId, arr, titleKey='title'){ const c=el(containerId); if(!c) return; c.innerHTML=''; (arr||[]).forEach((x)=>{ const t=x[titleKey] || x.name || x.slug || 'Item'; let html=`<div class="item"><h4>${esc(t)}</h4>`; Object.entries(x).forEach(([k,v])=>{ if(k===titleKey) return; html += `<p><b>${esc(k)}:</b> ${esc(typeof v==='object'? JSON.stringify(v): v)}</p>`; }); html += '</div>'; c.innerHTML += html; }); if((arr||[]).length===0){ c.innerHTML='<p class="muted">No data.</p>'; } }
function renderFeatures(arr){ const c=el('features-list'); if(!c) return; c.innerHTML=''; (arr||[]).forEach((f)=>{ c.innerHTML += `<div class="item"><h4>Feature</h4><p>${esc(f)}</p></div>`; }); }
function renderBlogs(arr){ const c=el('blogs-list'); if(!c) return; c.innerHTML=''; (arr||[]).forEach((b)=>{ c.innerHTML += `<div class="item"><h4>${esc(b.title)}</h4><p><b>Summary:</b> ${esc(b.summary)}</p><p>${esc(b.content)}</p></div>`; }); if((arr||[]).length===0) c.innerHTML='<p class="muted">No blogs yet.</p>'; }
function renderProducts(arr){
  const c = el('products-list');
  if(!c) return;
  c.innerHTML='';
  (arr||[]).forEach((x)=>{
    const img = x.image_url ? `<img src="${esc(x.image_url)}" alt="${esc(x.name)}" style="width:100%;max-height:180px;object-fit:cover;border-radius:10px;margin-bottom:8px;">` : '';
    c.innerHTML += `<div class="item">${img}<h4>${esc(x.name)}</h4><p><b>Price:</b> ${esc(x.price)}</p><p><b>Stock:</b> ${esc(x.stock)}</p><p>${esc(x.description||'')}</p></div>`;
  });
  if((arr||[]).length===0) c.innerHTML='<p class="muted">No products yet.</p>';
}
function renderPaymentMethods(methods){ const c=el('methods-list'); if(!c) return; c.innerHTML=''; (methods||[]).forEach((m)=>{ const ok=!!m.configured; c.innerHTML += `<div class="item"><h4>${esc((m.name||'').toUpperCase())}</h4><p>${ok?'Configured':'Not configured'}</p></div>`; }); }
function renderLaunchReadiness(data){ const c=el('launch-readiness-list'); if(!c) return; c.innerHTML=''; c.innerHTML += `<div class="item"><h4>Live Payment Launch</h4><p>${data.ready_for_live_payments?'READY':'NOT READY'}</p></div>`; c.innerHTML += `<div class="item"><h4>Payout Destination</h4><p>${data.payout_destination_set ? 'Set' : 'Missing'}</p></div>`; c.innerHTML += `<div class="item"><h4>Missing Providers</h4><p>${(data.missing_live_providers||[]).join(', ')||'None'}</p></div>`; }

async function api(path, method='GET', body=null, auth=true){
  const headers={'Content-Type':'application/json'};
  if(auth && token) headers.Authorization=`Bearer ${token}`;
  const res=await fetch(path,{method,headers,body:body?JSON.stringify(body):undefined});
  const data=await res.json().catch(()=>({}));
  if(!res.ok) throw new Error(data.error||`HTTP ${res.status}`);
  return data;
}

function referralLinkFromCode(code){ return `${window.location.origin}/auth?ref=${encodeURIComponent(code||'')}`; }
async function copyText(value){
  const v=String(value||'');
  if(!v) return false;
  try{ await navigator.clipboard.writeText(v); return true; }
  catch{
    const t=document.createElement('textarea'); t.value=v; document.body.appendChild(t); t.select();
    const ok=document.execCommand('copy'); document.body.removeChild(t); return ok;
  }
}

function updateReferralWidgets(){
  const code = currentUser?.referral_code || '';
  const link = code ? referralLinkFromCode(code) : '';
  if(el('ref-code-output')) el('ref-code-output').value = code;
  if(el('ref-link-output')) el('ref-link-output').value = link;
  if(el('dash-ref-link-output')) el('dash-ref-link-output').value = link;
}

function activatePanel(panelId){
  document.querySelectorAll('.dashboard-content .panel').forEach((p)=>p.classList.remove('active'));
  document.querySelectorAll('.side-btn').forEach((b)=>b.classList.remove('active'));
  const panel = el(panelId); if(panel) panel.classList.add('active');
  const btn = document.querySelector(`.side-btn[data-panel="${panelId}"]`); if(btn) btn.classList.add('active');
}

function setupSidebarByRole(role){
  const roleInfo = el('dashboard-role-info'); if(roleInfo) roleInfo.textContent = `Role: ${role || 'Guest'}`;
  const ownerBtn = document.querySelector('.side-btn[data-panel="owner-panel"]');
  const sellerBtn = document.querySelector('.side-btn[data-panel="seller-panel"]');
  const freelancerBtn = document.querySelector('.side-btn[data-panel="freelancer-panel"]');
  const earningsBtn = document.querySelector('.side-btn[data-panel="earnings-panel"]');
  const ownerAdminBtn = el('owner-admin-btn');

  const showOwner = role === 'owner';
  const showSeller = role === 'seller' || role === 'owner';
  const showFreelancer = role === 'freelancer' || role === 'owner';
  const showEarnings = role === 'seller' || role === 'freelancer' || role === 'owner' || !role;
  const showOwnerAdmin = role === 'owner';

  setVisible(ownerBtn, showOwner); setVisible(sellerBtn, showSeller); setVisible(freelancerBtn, showFreelancer); setVisible(earningsBtn, showEarnings); setVisible(ownerAdminBtn, showOwnerAdmin);
  setVisible(el('owner-panel'), showOwner); setVisible(el('seller-panel'), showSeller); setVisible(el('freelancer-panel'), showFreelancer); setVisible(el('earnings-panel'), showEarnings); setVisible(el('owner-admin-panel'), showOwnerAdmin);
  if(showEarnings) activatePanel('earnings-panel');
}

function initSidebar(){ document.querySelectorAll('.side-btn[data-panel]').forEach((btn)=>btn.addEventListener('click', ()=>{ const panelId = btn.getAttribute('data-panel'); if(panelId) activatePanel(panelId); })); }

async function refreshMe(){
  const meBox=el('me-box');
  if(!token){ currentUser=null; if(meBox) meBox.textContent='Not logged in'; setupSidebarByRole(''); updateReferralWidgets(); return; }
  try{
    const me=await api('/api/auth/me');
    currentUser=me;
    if(meBox) meBox.textContent=`Logged in as ${me.name} (${me.role}) | Referral Code: ${me.referral_code}`;
    setupSidebarByRole(me.role);
    updateReferralWidgets();
  }catch{
    token=''; localStorage.removeItem('gmf_token'); currentUser=null;
    if(meBox) meBox.textContent='Not logged in';
    setupSidebarByRole(''); updateReferralWidgets();
  }
}

async function loadEarningsSummary(){
  const box = el('earnings-summary-list'); if(!box) return;
  if(!token){ box.innerHTML = '<div class="item"><h4>Login Needed</h4><p>Please login first to see earnings, pending withdrawals, and hours.</p></div>'; return; }
  try{
    const me = await api('/api/auth/me');
    if(me.role === 'seller'){ const d=await api('/api/dashboard/seller'); return renderObject('earnings-summary-list', {role:'Seller', gross_earnings:d.gross_revenue, pending_withdrawals:d.pending_withdrawals, available_balance:d.available_balance, working_hours:d.working_hours}); }
    if(me.role === 'freelancer'){ const d=await api('/api/dashboard/freelancer'); return renderObject('earnings-summary-list', {role:'Freelancer', gross_earnings:d.gross_earnings, pending_withdrawals:d.pending_withdrawals, available_balance:d.available_balance, working_hours:d.working_hours}); }
    if(me.role === 'owner'){ const d=await api('/api/dashboard/owner'); return renderObject('earnings-summary-list', {role:'Owner', total_commission:d.total_commission, pending_withdrawals:d.withdrawals_pending, total_withdrawals:d.withdrawals_total, working_hours:d.total_work_hours_logged}); }
  }catch(e){ status(e.message,true); }
}

function bind(id, fn){ const n=el(id); if(n) n.addEventListener('click', fn); }

bind('logout-btn', ()=>{ token=''; localStorage.removeItem('gmf_token'); refreshMe(); status('Logged out.'); });
bind('quick-owner-login-btn', async ()=>{ try{ const out=await api('/api/auth/login','POST',{ email:'keithmukonga@gmail.com', password:'Owner@12345' }, false); token=out.token; localStorage.setItem('gmf_token', token); await refreshMe(); await loadEarningsSummary(); status('Owner login successful.'); }catch(e){ status(e.message,true); } });

bind('register-btn', async ()=>{ try{ const out=await api('/api/auth/register','POST',{ name:el('reg-name').value, email:el('reg-email').value, password:el('reg-password').value, role:el('reg-role').value, referral_code: el('reg-ref') ? el('reg-ref').value : '' }, false); token=out.token; localStorage.setItem('gmf_token', token); await refreshMe(); status('Registration successful.'); if(window.location.pathname === '/auth') window.location.href='/dashboard'; }catch(e){ status(e.message,true); } });
bind('login-btn', async ()=>{ try{ const ref = el('login-ref') ? el('login-ref').value : ''; await api('/api/auth/login-click','POST',{referral_code:ref}, false); const out=await api('/api/auth/login','POST',{ email:el('login-email').value, password:el('login-password').value }, false); token=out.token; localStorage.setItem('gmf_token', token); await refreshMe(); status('Login successful.'); if(window.location.pathname === '/auth') window.location.href='/dashboard'; }catch(e){ status(e.message,true); } });

bind('load-ref-link-btn', async ()=>{ try{ const d=await api('/api/referrals/link'); if(el('ref-code-output')) el('ref-code-output').value=d.referral_code||''; if(el('ref-link-output')) el('ref-link-output').value=d.referral_link||''; if(el('dash-ref-link-output')) el('dash-ref-link-output').value=d.referral_link||''; status('Referral link ready.'); }catch(e){ status(e.message,true); } });
bind('copy-ref-link-btn', async ()=>{ const ok=await copyText(el('ref-link-output')?.value||''); status(ok?'Referral link copied.':'Copy failed.', !ok); });
bind('copy-ref-code-btn', async ()=>{ const ok=await copyText(el('ref-code-output')?.value||''); status(ok?'Referral code copied.':'Copy failed.', !ok); });
bind('dash-copy-ref-link-btn', async ()=>{ const ok=await copyText(el('dash-ref-link-output')?.value||''); status(ok?'Referral link copied.':'Copy failed.', !ok); });

bind('ref-summary-btn', async ()=>{ try{ renderObject('ref-summary-list', await api('/api/referrals/summary')); }catch(e){ status(e.message,true);} });
bind('load-features-btn', async ()=>{ try{ const d=await api('/api/features','GET',null,false); renderFeatures(d.features||[]); }catch(e){ status(e.message,true);} });
bind('create-job-btn', async ()=>{ try{ await api('/api/jobs','POST',{title:el('job-title').value,description:el('job-description').value,budget:Number(el('job-budget').value),currency:(el('job-currency')?el('job-currency').value:'KES')}); status('Job posted.'); el('refresh-jobs-btn')?.click(); }catch(e){ status(e.message,true);} });
bind('apply-job-btn', async ()=>{ try{ await api(`/api/jobs/${Number(el('apply-job-id').value)}/apply`,'POST',{cover_note:el('apply-note').value,proposed_amount:Number(el('apply-amount').value)}); status('Application submitted.'); }catch(e){ status(e.message,true);} });
bind('refresh-jobs-btn', async ()=>{ try{ renderArray('jobs-list', await api('/api/jobs','GET',null,false)); }catch(e){ status(e.message,true);} });

bind('create-product-btn', async ()=>{ try{ await api('/api/products','POST',{ name:el('product-name').value, description:el('product-description').value, price:Number(el('product-price').value), stock:Number(el('product-stock').value), image_url:(el('product-image')?el('product-image').value:'') }); status('Product added.'); el('refresh-products-btn')?.click(); }catch(e){ status(e.message,true); } });
bind('order-product-btn', async ()=>{ try{ await api(`/api/products/${Number(el('order-product-id').value)}/order`,'POST',{quantity:Number(el('order-qty').value)}); status('Order placed.'); el('refresh-products-btn')?.click(); }catch(e){ status(e.message,true);} });
bind('refresh-products-btn', async ()=>{ try{ renderProducts(await api('/api/products','GET',null,false)); }catch(e){ status(e.message,true);} });

bind('load-methods-btn', async ()=>{ try{ const d=await api('/api/payments/methods','GET',null,false); renderPaymentMethods(d.providers||[]); }catch(e){ status(e.message,true);} });
bind('launch-readiness-btn', async ()=>{ try{ const d=await api('/api/launch/readiness','GET',null,false); renderLaunchReadiness(d); status('Launch check complete.'); }catch(e){ status(e.message,true);} });
bind('pay-btn', async ()=>{ try{ const out=await api('/api/payments/initiate','POST',{provider:el('pay-provider').value,amount:Number(el('pay-amount').value),currency:el('pay-currency').value,phone:el('pay-phone').value}); renderObject('payment-result', out); status('Payment initiated.'); }catch(e){ status(e.message,true);} });
bind('load-payments-btn', async ()=>{ try{ renderArray('payments-list', await api('/api/payments')); }catch(e){ status(e.message,true);} });

bind('save-payout-btn', async ()=>{ try{ const out=await api('/api/owner/payout-settings','POST',{ bitcoin_wallet: el('payout-bitcoin').value, paypal_email: el('payout-paypal').value, bank_name: el('payout-bank-name').value, bank_account_name: el('payout-bank-account-name').value, bank_account_number: el('payout-bank-account-number').value, mpesa_number: el('payout-mpesa').value }); renderObject('payment-result', out.settings || out); status('Payout destination saved.'); }catch(e){ status(e.message,true); } });
bind('load-payout-btn', async ()=>{ try{ renderObject('payment-result', await api('/api/owner/payout-settings')); }catch(e){ status(e.message,true);} });

bind('load-blogs-btn', async ()=>{ try{ renderBlogs(await api('/api/blogs','GET',null,false)); }catch(e){ status(e.message,true);} });
bind('create-blog-btn', async ()=>{ try{ await api('/api/blogs','POST',{title:el('blog-title').value,summary:el('blog-summary').value,content:el('blog-content').value}); status('Blog published.'); el('load-blogs-btn')?.click(); }catch(e){ status(e.message,true);} });

bind('owner-dashboard-btn', async ()=>{ try{ renderObject('owner-list', await api('/api/dashboard/owner')); }catch(e){ status(e.message,true);} });
bind('seller-dashboard-btn', async ()=>{ try{ renderObject('seller-list', await api('/api/dashboard/seller')); }catch(e){ status(e.message,true);} });
bind('freelancer-dashboard-btn', async ()=>{ try{ renderObject('freelancer-list', await api('/api/dashboard/freelancer')); }catch(e){ status(e.message,true);} });
bind('load-earnings-summary-btn', async ()=>{ await loadEarningsSummary(); });
bind('load-monetization-btn', async ()=>{ try{ renderObject('monetization-list', await api('/api/owner/monetization-summary')); }catch(e){ status(e.message,true);} });

bind('request-withdrawal-btn', async ()=>{ try{ const out = await api('/api/withdrawals/request','POST',{ amount: Number(el('withdrawal-amount').value), method: el('withdrawal-method').value, destination: el('withdrawal-destination').value }); status(out.message || 'Withdrawal requested.'); }catch(e){ status(e.message,true); } });
bind('load-withdrawals-btn', async ()=>{ try{ renderArray('withdrawals-list', await api('/api/withdrawals')); }catch(e){ status(e.message,true); } });
bind('load-all-withdrawals-btn', async ()=>{ try{ renderArray('all-withdrawals-list', await api('/api/withdrawals')); }catch(e){ status(e.message,true); } });
bind('update-withdrawal-status-btn', async ()=>{ try{ const withdrawalId = Number(el('admin-withdrawal-id').value || 0); const statusValue = (el('admin-withdrawal-status').value || '').trim(); if(!withdrawalId){ status('Enter a valid withdrawal ID', true); return; } await api(`/api/withdrawals/${withdrawalId}/status`, 'POST', { status: statusValue }); status('Withdrawal status updated.'); el('load-all-withdrawals-btn')?.click(); }catch(e){ status(e.message,true); } });
bind('start-work-btn', async ()=>{ try{ const out = await api('/api/work-sessions/start','POST',{note: (el('work-note')?el('work-note').value:'')}); status(out.message || 'Work started.'); }catch(e){ status(e.message,true); } });
bind('stop-work-btn', async ()=>{ try{ const out = await api('/api/work-sessions/stop','POST',{}); status((out.message || 'Work stopped.') + ' Hours: ' + (out.hours ?? 0)); }catch(e){ status(e.message,true); } });
bind('load-work-btn', async ()=>{ try{ renderArray('work-list', await api('/api/work-sessions')); }catch(e){ status(e.message,true); } });

initSidebar();
refreshMe();

try{
  const refCode = new URLSearchParams(window.location.search).get('ref') || '';
  if(refCode){
    if(el('reg-ref')) el('reg-ref').value = refCode;
    if(el('login-ref')) el('login-ref').value = refCode;
  }
}catch{}

if(el('features-list')) el('load-features-btn')?.click();
if(el('jobs-list')) el('refresh-jobs-btn')?.click();
if(el('products-list')) el('refresh-products-btn')?.click();
if(el('blogs-list')) el('load-blogs-btn')?.click();
if(el('launch-readiness-list')) el('launch-readiness-btn')?.click();
if(el('earnings-summary-list')) el('load-earnings-summary-btn')?.click();
if(el('quick-owner-login-btn') && !token) el('quick-owner-login-btn')?.click();

if(el('login-email') && !el('login-email').value) el('login-email').value = 'keithmukonga@gmail.com';
if(el('login-password') && !el('login-password').value) el('login-password').value = 'Owner@12345';
