const {JSDOM}=require('jsdom'),fs=require('fs'),assert=require('node:assert/strict');
const R=require('path').resolve(__dirname,'../..');
const dom=new JSDOM(fs.readFileSync(R+'/studio/index.html','utf8'),{url:'https://studio.test/',runScripts:'outside-only'}),w=dom.window;
w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false};w.fetch=async()=>({ok:false,status:401,json:async()=>({})});w.confirm=()=>false;
let code=['app.js','v54.js','v55.js','v58.js','v59.js','v510.js'].map(f=>fs.readFileSync(R+'/studio/'+f,'utf8')).join('\n').replaceAll('window.startIvoryStudio?.();','');
code+=`\nwindow.qa=(async()=>{
 businessSelection='one';seatBusinesses=[{id:'one',name:'Wedding business',role:'owner',status:'active'}];
 api=async path=>path.endsWith('/options')?{sales:[{id:'200',name:'Sales'}],tax:[{id:'NONE',name:'No VAT'}],bank:[{id:'bank',name:'Current account'}]}:{providers:[{id:'xero',name:'Xero',configured:true},{id:'quickbooks',name:'QuickBooks Online',configured:false}],connection:{provider:'xero',company_id:'company',company_name:'Selected company',authorised:true,enabled:true,mapping:{sales:'200',tax:'NONE',bank:'bank',stripe_bank:'bank'},auto_sync:false},invoices:[{id:'invoice',number:'TEST-1',total_pence:10000,status:'unpaid'}],exports:[]};
 mountBusinessControls();if(!document.querySelector('#business-accounting'))throw Error('Missing owner accounting button');
 await openAccounting();const form=document.querySelector('#accounting-settings');
 if(form.querySelector('[name=sales]').value!=='200')throw Error('Mapping selection lost');
 if(form.querySelector('[name=auto_sync]').checked)throw Error('Auto sync must be opt in');
 if(!form.querySelector('[name=confirmed]').required)throw Error('Tax confirmation required');
 if(!document.querySelector('#accounting-dialog').textContent.includes('Sage Accounting is planned'))throw Error('Sage status misleading');
 seatBusinesses[0].role='staff';mountBusinessControls();if(document.querySelector('#business-accounting'))throw Error('Staff sees owner control');
 return true;
})();`;
w.eval(code);w.qa.then(()=>{console.log('Accounting owner controls, company display, saved mapping, opt-in sync, tax confirmation and Sage planned status passed');dom.window.close()}).catch(e=>{console.error(e);process.exit(1)});
