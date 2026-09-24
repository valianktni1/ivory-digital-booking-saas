const {JSDOM}=require('jsdom'),fs=require('fs'),assert=require('node:assert/strict');
const R=require('path').resolve(__dirname,'../..');
async function studio(){
 const dom=new JSDOM(fs.readFileSync(R+'/studio/index.html','utf8'),{url:'https://studio.test/',runScripts:'outside-only'}),w=dom.window;
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false};w.fetch=async()=>({ok:false,status:401,json:async()=>({})});w.confirm=()=>false;
 let code=['app.js','v54.js','v55.js','v58.js','v59.js'].map(f=>fs.readFileSync(R+'/studio/'+f,'utf8')).join('\n').replaceAll('window.startIvoryStudio?.();','');
 code+=`\nwindow.qa=(async()=>{
  api=async path=>path.endsWith('/businesses')?{selected:'one',businesses:[{id:'one',name:'One & Co',role:'owner',status:'active'},{id:'two',name:'Second',role:'owner',status:'suspended'}]}:{configured:false,subscription:{plan_name:'Second business',price_pence:0,billing_cycle:'monthly',billing_status:'complimentary',provider:'complimentary'}};
  await loadBusinessAccess();mountBusinessControls();
  if(document.querySelector('#business-switch').options.length!==2)throw Error('Business switch missing');
  await openPayments();if(!document.querySelector('#stripe-settings').textContent.includes('free access'))throw Error('Free grant missing');
  if(!document.querySelector('#stripe-settings').textContent.includes('Bank transfer remains available'))throw Error('Bank option missing');
  await showBusinessGate();if(!document.querySelector('#gate-pay'))throw Error('Paywall blocks payment settings');
  return true;
 })();`;
 w.eval(code);await w.qa;dom.window.close();
}
async function client(){
 const dom=new JSDOM(fs.readFileSync(R+'/client/index.html','utf8'),{url:'https://client.test/portal/private',runScripts:'outside-only'}),w=dom.window;
 w.fetch=async()=>({ok:false,json:async()=>({})});w.eval(fs.readFileSync(R+'/client/app.js','utf8'));
 const out=w.eval(`cardButtons({booking_fee_pence:10000},{id:'invoice',card_available:true,total_pence:100000,paid_pence:0,outstanding_pence:100000})`);
 assert(out.includes('Pay booking fee £100.00'));assert(out.includes('Pay remaining balance £1,000.00'));
 assert.equal(w.eval(`cardButtons({read_only:true},{card_available:true})`),'');
 assert.equal(w.eval(`cardButtons({},{card_available:false})`),'');
 dom.window.close();
}
async function manager(){
 const dom=new JSDOM(fs.readFileSync(R+'/manager/index.html','utf8'),{url:'https://manager.test/',runScripts:'outside-only'}),w=dom.window;
 w.fetch=async()=>({ok:false,status:401,json:async()=>({})});
 let code=fs.readFileSync(R+'/manager/app.js','utf8')+'\n'+fs.readFileSync(R+'/manager/v59.js','utf8');
 code+=`\nwindow.qa=(async()=>{api=async()=>({data:{primary_id:'one',businesses:[{id:'one',name:'One',status:'active',subscription:{price_pence:1000,billing_cycle:'monthly'}}]}});await openSeat('one');return document.querySelector('#seat-price').disabled&&document.querySelector('#seat-form').textContent.includes('Grant free access')})();`;
 w.eval(code);assert(await w.qa);dom.window.close();
}
Promise.all([studio(),client(),manager()]).then(()=>console.log('Business switch, free grant, suspended-owner payment access, bank option, client fee/balance and Manager seat form passed')).catch(e=>{console.error(e);process.exit(1)});
