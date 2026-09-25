"""Outbound accounting sync. Uncertain writes stop for review instead of being repeated."""
import hashlib
import html
import json
import secrets
import time
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, quote

import httpx
from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from .config import get_settings
from .database import get_db, SessionLocal
from .models import (AccountingConnection as Connection, AccountingOAuthState as OAuthState,
    AccountingExport as Export, Tenant, TenantStatus, Membership, MembershipRole, UserSession,
    BookingInvoice, BookingPayment, Booking, Client)
from .security import encrypt_secret, decrypt_secret, token_hash, utcnow
from .tenant_context import membership_for, set_database_tenant

settings = get_settings()
PROVIDERS = {
    'xero': {'name':'Xero', 'authorize':'https://login.xero.com/identity/connect/authorize',
        'token':'https://identity.xero.com/connect/token',
        'scope':'offline_access accounting.invoices accounting.payments accounting.contacts accounting.settings.read'},
    'quickbooks': {'name':'QuickBooks Online', 'authorize':'https://appcenter.intuit.com/connect/oauth2',
        'token':'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer',
        'scope':'com.intuit.quickbooks.accounting'},
}


def configured(provider):
    return provider in PROVIDERS and settings.accounting_enabled and bool(
        getattr(settings, provider+'_client_id') and getattr(settings, provider+'_client_secret'))


def callback_url(provider):
    return settings.studio_url.rstrip('/')+'/api/accounting/'+provider+'/callback'


def owner(db, session):
    if session.user.is_platform_admin:
        raise HTTPException(403, 'Sign into the photographer’s Studio')
    row = membership_for(db, session.user)
    if row.role != MembershipRole.OWNER:
        raise HTTPException(403, 'Only the business owner can manage accounting')
    return db.get(Tenant, row.tenant_id)


def conn_for(db, tenant_id):
    row = db.scalar(select(Connection).where(Connection.tenant_id == tenant_id).with_for_update())
    if not row or not row.tokens_encrypted:
        raise HTTPException(409, 'Connect your accounting account first')
    return row


def token_request(provider, data):
    if not configured(provider):
        raise HTTPException(503, 'Ivory Digital has not configured this accounting provider yet')
    try:
        r = httpx.post(PROVIDERS[provider]['token'], data=data,
            auth=(getattr(settings,provider+'_client_id'),getattr(settings,provider+'_client_secret')),
            headers={'Accept':'application/json'}, timeout=20)
        r.raise_for_status(); body=r.json()
        if not body.get('access_token') or not body.get('refresh_token'):
            raise ValueError()
        return {**body, 'expires_at':time.time()+int(body.get('expires_in',1800))}
    except (httpx.HTTPError,ValueError):
        raise HTTPException(502, 'Accounting authorisation failed. Reconnect the original account and try again.') from None


def refresh(db, conn):
    if not configured(conn.provider):
        raise HTTPException(503, 'Accounting integration is disabled by Ivory Digital')
    tokens=json.loads(decrypt_secret(conn.tokens_encrypted))
    if tokens['expires_at'] < time.time()+120:
        tokens=token_request(conn.provider, {'grant_type':'refresh_token','refresh_token':tokens['refresh_token']})
        conn.tokens_encrypted=encrypt_secret(json.dumps(tokens))
        # Persist rotated refresh token before any accounting request can fail.
        db.commit(); set_database_tenant(db,conn.tenant_id)
        db.refresh(conn,with_for_update=True)
    return tokens['access_token']


def api(db, conn, method, path, body=None, key=None):
    access=refresh(db, conn)
    headers={'Authorization':'Bearer '+access,'Accept':'application/json'}
    if conn.provider=='xero':
        base='https://api.xero.com/api.xro/2.0'
        headers['xero-tenant-id']=conn.company_id
        if key:headers['Idempotency-Key']=key
    else:
        host='sandbox-quickbooks.api.intuit.com' if settings.quickbooks_sandbox else 'quickbooks.api.intuit.com'
        base='https://'+host+'/v3/company/'+quote(conn.company_id,safe='')
        if key:path+=('&' if '?' in path else '?')+urlencode({'requestid':key})
    try:
        r=httpx.request(method,base+path,headers=headers,json=body,timeout=20)
        if r.status_code>=400:
            exc=HTTPException(502, f'Accounting provider returned HTTP {r.status_code}. Check the account and mapping; no automatic retry was made.')
            exc.definite = r.status_code in {400,401,403,404,422,429}
            raise exc
        return r.json()
    except (httpx.HTTPError,ValueError):
        raise HTTPException(502, 'Accounting provider did not confirm the result. Check its records before attempting another export.') from None


def qb_query(db,conn,entity,where=''):
    query='select * from '+entity+(' where '+where if where else '')+' maxresults 1000'
    return api(db,conn,'GET','/query?'+urlencode({'query':query})).get('QueryResponse',{}).get(entity,[])


def options(db,conn):
    if conn.provider=='xero':
        accounts=api(db,conn,'GET','/Accounts').get('Accounts',[])
        taxes=api(db,conn,'GET','/TaxRates').get('TaxRates',[])
        return {'sales':[{'id':x['Code'],'name':x['Name']} for x in accounts if x.get('Type') in {'REVENUE','SALES'} and x.get('Status')=='ACTIVE'],
            'tax':[{'id':x['TaxType'],'name':x['Name']} for x in taxes if x.get('Status')=='ACTIVE' and x.get('CanApplyToRevenue',True)],
            'bank':[{'id':x['AccountID'],'name':x['Name']} for x in accounts if x.get('Status')=='ACTIVE' and (x.get('Type')=='BANK' or x.get('EnablePaymentsToAccount'))]}
    return {'sales':[{'id':x['Id'],'name':x['Name']} for x in qb_query(db,conn,'Item','Active = true') if x.get('Type')=='Service'],
        'tax':[{'id':x['Id'],'name':x['Name']} for x in qb_query(db,conn,'TaxCode','Active = true')],
        'bank':[{'id':x['Id'],'name':x['Name']} for x in qb_query(db,conn,'Account','Active = true') if x.get('AccountType') in {'Bank','Other Current Asset'}]}


def money_equal(left,right):
    try:
        return Decimal(str(left))==Decimal(str(right))
    except (InvalidOperation,ValueError,TypeError):
        return False


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def invoice_snapshot(invoice):
    return {'number':invoice.number,'date':str(invoice.issue_date),'due':str(invoice.due_date or invoice.issue_date),
        'total':invoice.total_pence,'lines':invoice.line_items,'status':'void' if invoice.status=='void' else 'issued'}


def job(db,tenant_id,kind,source_id):
    row=db.scalar(select(Export).where(Export.tenant_id==tenant_id,Export.kind==kind,Export.source_id==source_id))
    if not row:
        row=Export(tenant_id=tenant_id,kind=kind,source_id=source_id);db.add(row);db.flush()
    return row


def write_remote(db,conn,row,path,payload,result_key,id_key):
    if row.state=='synced':return row.remote_id
    if row.state in {'sending','review'}:
        raise HTTPException(409,'This export needs review. An uncertain write will not be repeated automatically.')
    # Refresh BEFORE saving the durable sending flag. Locks serialize each business.
    db.flush()
    refresh(db,conn)
    db.refresh(row,with_for_update=True)
    if row.state=='synced':return row.remote_id
    if row.state in {'sending','review'}:raise HTTPException(409,'An export is already in progress or awaiting review')
    row.payload=payload;row.state='sending';row.message='Awaiting provider confirmation'
    db.commit();set_database_tenant(db,conn.tenant_id)
    db.refresh(conn,with_for_update=True)
    if not conn.enabled or not conn.tokens_encrypted:
        row.state='error';row.message='Sync stopped because the accounting connection was disabled';db.commit()
        raise HTTPException(409,row.message)
    try:
        method='PUT' if conn.provider=='xero' and path=='/Payments' else 'POST'
        data=api(db,conn,method,path,payload,key=row.id.replace('-','')+'-'+digest(payload)[:16])
        result=data[result_key]
        if isinstance(result,list):result=result[0]
        if result.get('HasErrors') or not result.get(id_key):raise ValueError()
        row.remote_id=str(result[id_key])
        if row.kind=='invoice':
            expected=payload['Invoices'][0]['LineItems'][0]['UnitAmount'] if conn.provider=='xero' else payload['Line'][0]['Amount']
            actual=result.get('Total') if conn.provider=='xero' else result.get('TotalAmt')
            if actual is None or Decimal(str(actual))!=Decimal(str(expected)):
                raise ValueError('Invoice total mismatch')
        row.state='synced';row.message=''
    except (HTTPException,KeyError,IndexError,ValueError,InvalidOperation,TypeError) as exc:
        row.state='error' if getattr(exc,'definite',False) else 'review';row.message=exc.detail if isinstance(exc,HTTPException) else 'The provider did not confirm creation. Review its records.'
        db.commit();set_database_tenant(db,conn.tenant_id)
        raise HTTPException(409,row.message) from None
    db.commit();set_database_tenant(db,conn.tenant_id)
    return row.remote_id


def export_invoice(db,conn,invoice):
    if not conn.enabled or not conn.mapping or not conn.company_id:
        raise HTTPException(409,'Choose the company and save accounting settings first')
    row=job(db,conn.tenant_id,'invoice',invoice.id)
    snapshot=invoice_snapshot(invoice); fingerprint=digest(snapshot)
    if row.fingerprint and row.fingerprint!=fingerprint:
        row.state='review';row.message='Invoice changed after export began. Review it in your accounts; it has not been overwritten.'
        db.commit();raise HTTPException(409,row.message)
    if sum(int(x.get('price_pence',0)) for x in invoice.line_items)!=invoice.total_pence:
        raise HTTPException(422,'Invoice lines do not match the total; review the invoice first')
    if invoice.status=='void' or invoice.total_pence<=0:
        row.state='review';row.message='Void and zero-value invoices need accountant review.';db.commit();raise HTTPException(409,row.message)
    if row.state=='synced':return row
    if row.state in {'sending','review'}:raise HTTPException(409,'This invoice needs accounting review before another export')
    booking=db.scalar(select(Booking).where(Booking.id==invoice.booking_id,Booking.tenant_id==conn.tenant_id))
    client=db.scalar(select(Client).where(Client.id==booking.client_id,Client.tenant_id==conn.tenant_id))
    contact=job(db,conn.tenant_id,'contact',client.id)
    name=(client.first_name+' '+client.last_name).strip()
    if conn.provider=='xero':
        contact_id=write_remote(db,conn,contact,'/Contacts',{'Contacts':[{'Name':name+' · '+client.id[:8],
            'ContactNumber':'IVORY-'+client.id,'EmailAddress':client.email}]},'Contacts','ContactID')
    else:
        contact_id=write_remote(db,conn,contact,'/customer',{'DisplayName':name+' · Ivory '+client.id,
            'PrimaryEmailAddr':{'Address':client.email}},'Customer','Id')
    # One gross service line preserves exact totals (including discounts). Original breakdown is in description.
    description='Ivory invoice '+invoice.number+'\n'+'\n'.join(str(x.get('label','Service'))+' — £'+format(Decimal(x['price_pence'])/100,'.2f') for x in invoice.line_items)
    if len(description)>3900:raise HTTPException(422,'Invoice description is too long for export; review its package wording')
    gross=float(Decimal(invoice.total_pence)/100);mapping=conn.mapping
    ref='IVORY:'+invoice.id
    if conn.provider=='xero':
        payload={'Invoices':[{'Type':'ACCREC','Contact':{'ContactID':contact_id},'Date':str(invoice.issue_date),
            'DueDate':str(invoice.due_date or invoice.issue_date),'Reference':ref,'CurrencyCode':'GBP',
            'LineAmountTypes':'Inclusive','Status':'AUTHORISED',
            'LineItems':[{'Description':description,'Quantity':1,'UnitAmount':gross,'AccountCode':mapping['sales'],'TaxType':mapping['tax']}]}]}
        path,result,id_key='/Invoices','Invoices','InvoiceID'
    else:
        payload={'CustomerRef':{'value':contact_id},'CurrencyRef':{'value':'GBP'},'TxnDate':str(invoice.issue_date),
            'DueDate':str(invoice.due_date or invoice.issue_date),'PrivateNote':ref,'GlobalTaxCalculation':'TaxInclusive',
            'EmailStatus':'NotSet','AllowOnlineCreditCardPayment':False,'AllowOnlineACHPayment':False,
            'Line':[{'Amount':gross,'Description':description,'DetailType':'SalesItemLineDetail',
                'SalesItemLineDetail':{'ItemRef':{'value':mapping['sales']},'TaxCodeRef':{'value':mapping['tax']},'Qty':1,'UnitPrice':gross}}]}
        path,result,id_key='/invoice','Invoice','Id'
    row.fingerprint=fingerprint
    write_remote(db,conn,row,path,payload,result,id_key)
    return row


def export_payment(db,conn,payment,invoice,invoice_export):
    row=job(db,conn.tenant_id,'payment',payment.id)
    if payment.amount_pence<=0:
        row.state='review';row.message='Refund or adjustment: review in your accounting software. No automatic credit note was issued.'
        db.commit();set_database_tenant(db,conn.tenant_id);return
    fingerprint=digest({'amount':payment.amount_pence,'date':str(payment.paid_date),'type':payment.payment_type,'invoice':invoice.id})
    if row.fingerprint and row.fingerprint!=fingerprint:
        row.state='review';row.message='Payment changed after export. Review your accounting records.';db.commit();return
    if row.state=='synced':return
    if payment.payment_type not in {'stripe','bank_transfer'}:
        row.state='review';row.message='Choose how to record this cash/card/other payment in your accounts.';db.commit();return
    account=conn.mapping['stripe_bank' if payment.payment_type=='stripe' else 'bank']
    amount=float(Decimal(payment.amount_pence)/100)
    booking=db.get(Booking,invoice.booking_id);contact=job(db,conn.tenant_id,'contact',booking.client_id)
    if conn.provider=='xero':
        payload={'Payments':[{'Invoice':{'InvoiceID':invoice_export.remote_id},'Account':{'AccountID':account},
            'Date':str(payment.paid_date),'Amount':amount,'Reference':'IVORY:'+payment.id}]}
        path,result,key='/Payments','Payments','PaymentID'
    else:
        payload={'CustomerRef':{'value':contact.remote_id},'TotalAmt':amount,'TxnDate':str(payment.paid_date),
            'DepositToAccountRef':{'value':account},'PrivateNote':'IVORY:'+payment.id,'CurrencyRef':{'value':'GBP'},
            'Line':[{'Amount':amount,'LinkedTxn':[{'TxnId':invoice_export.remote_id,'TxnType':'Invoice'}]}]}
        path,result,key='/payment','Payment','Id'
    row.fingerprint=fingerprint
    write_remote(db,conn,row,path,payload,result,key)


def sync_one(db,tenant_id,invoice_id):
    conn=conn_for(db,tenant_id)
    invoice=db.scalar(select(BookingInvoice).where(BookingInvoice.id==invoice_id,BookingInvoice.tenant_id==tenant_id))
    if not invoice:raise HTTPException(404,'Invoice not found')
    try:
        exp=export_invoice(db,conn,invoice)
    except HTTPException as exc:
        set_database_tenant(db,tenant_id)
        pending=job(db,tenant_id,'invoice',invoice.id)
        if pending.state=='ready':
            pending.state='error';pending.message=exc.detail;db.commit()
        raise
    for payment in db.scalars(select(BookingPayment).where(BookingPayment.invoice_id==invoice.id,
            BookingPayment.tenant_id==tenant_id).order_by(BookingPayment.created_at)).all():
        # A preceding refund/review blocks subsequent automatic changes on this invoice.
        prior=db.scalar(select(Export).where(Export.tenant_id==tenant_id,Export.kind=='payment',
            Export.source_id==payment.id))
        if prior and prior.state=='review':break
        conn=conn_for(db,tenant_id)
        export_payment(db,conn,payment,invoice,exp)
        set_database_tenant(db,tenant_id)
    db.commit()


class ProviderIn(BaseModel):
    provider: str


class CompanyIn(BaseModel):
    company_id: str = Field(max_length=100)


class MappingIn(BaseModel):
    sales: str = Field(min_length=1,max_length=100)
    tax: str = Field(min_length=1,max_length=100)
    bank: str = Field(min_length=1,max_length=100)
    stripe_bank: str = Field(min_length=1,max_length=100)
    auto_sync: bool = False
    confirmed: bool = False


class LinkIn(BaseModel):
    remote_id: str = Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9-]+$')


def register_routes(m):
    app=m.app

    @app.get('/api/studio/accounting')
    def status(session: UserSession=Depends(m.session_dependency), db:Session=Depends(get_db)):
        tenant=owner(db,session);conn=db.get(Connection,tenant.id)
        exports=db.scalars(select(Export).where(Export.tenant_id==tenant.id).order_by(Export.updated_at.desc()).limit(100)).all()
        invoices=db.scalars(select(BookingInvoice).where(BookingInvoice.tenant_id==tenant.id).order_by(BookingInvoice.created_at.desc()).limit(100)).all()
        return {'providers':[{'id':k,'name':v['name'],'configured':configured(k)} for k,v in PROVIDERS.items()],
            'connection':{'provider':conn.provider,'company_id':conn.company_id,'company_name':conn.company_name,
                'choices':conn.choices,'mapping':conn.mapping,'enabled':conn.enabled,'auto_sync':conn.auto_sync,
                'authorised':bool(conn.tokens_encrypted)} if conn else None,
            'invoices':[{'id':x.id,'number':x.number,'total_pence':x.total_pence,'status':x.status} for x in invoices],
            'exports':[{'id':x.id,'kind':x.kind,'source_id':x.source_id,'state':x.state,'remote_id':x.remote_id,'message':x.message} for x in exports]}

    @app.post('/api/studio/accounting/connect')
    def connect(payload:ProviderIn, session:UserSession=Depends(m.require_csrf),db:Session=Depends(get_db)):
        tenant=owner(db,session);provider=payload.provider
        if not configured(provider):raise HTTPException(503,'This provider is not configured by Ivory Digital yet')
        existing=db.get(Connection,tenant.id)
        if existing and existing.provider!=provider:raise HTTPException(409,'This business already has a different accounting provider. Existing export history must be reconciled before changing provider.')
        raw=secrets.token_urlsafe(36)
        db.add(OAuthState(id=token_hash(raw),tenant_id=tenant.id,user_id=session.user_id,provider=provider,expires_at=utcnow()+timedelta(minutes=10)))
        db.commit()
        return {'url':PROVIDERS[provider]['authorize']+'?'+urlencode({'client_id':getattr(settings,provider+'_client_id'),
            'redirect_uri':callback_url(provider),'response_type':'code','scope':PROVIDERS[provider]['scope'],'state':raw})}

    @app.get('/api/accounting/{provider}/callback')
    def handoff(provider:str,state:str='',code:str='',realmId:str=''):
        if provider not in PROVIDERS:raise HTTPException(404)
        target='/api/accounting/'+provider+'/finish?'+urlencode({'state':state,'code':code,'realmId':realmId})
        return HTMLResponse('<!doctype html><title>Connect accounting</title><p>Continue to your secure Studio.</p><a href="'+html.escape(target,quote=True)+'">Continue</a><script src="/api/accounting/return.js"></script>')

    @app.get('/api/accounting/return.js')
    def return_script():
        return Response("location.replace(location.pathname.replace('/callback','/finish')+location.search)",media_type='application/javascript')

    @app.get('/api/accounting/{provider}/finish')
    def finish(provider:str,state:str='',code:str='',realmId:str='',session:UserSession=Depends(m.session_dependency),db:Session=Depends(get_db)):
        set_database_tenant(db,platform_admin=True)
        row=db.scalar(select(OAuthState).where(OAuthState.id==token_hash(state)).with_for_update())
        if not row or row.provider!=provider or row.user_id!=session.user_id or row.used_at or (row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at)<=utcnow() or not code:
            raise HTTPException(400,'Accounting connection link is invalid or expired. Start again in Studio.')
        membership=db.scalar(select(Membership).where(Membership.user_id==session.user_id,Membership.tenant_id==row.tenant_id,Membership.role==MembershipRole.OWNER))
        if not membership:raise HTTPException(403,'Business owner access required')
        membership_for(db,session.user,row.tenant_id)
        tenant_id=row.tenant_id;row.used_at=utcnow();db.commit();set_database_tenant(db,tenant_id)
        conn=db.scalar(select(Connection).where(Connection.tenant_id==tenant_id).with_for_update())
        if conn and conn.provider!=provider:raise HTTPException(409,'Provider changed while connecting. Start again.')
        tokens=token_request(provider,{'grant_type':'authorization_code','code':code,'redirect_uri':callback_url(provider)})
        headers={'Authorization':'Bearer '+tokens['access_token'],'Accept':'application/json'}
        try:
            if provider=='xero':
                r=httpx.get('https://api.xero.com/connections',headers=headers,timeout=20);r.raise_for_status()
                choices=[{'id':x['tenantId'],'name':x['tenantName']} for x in r.json() if x.get('tenantType')=='ORGANISATION']
            else:
                if not realmId.isdigit():raise ValueError()
                host='sandbox-quickbooks.api.intuit.com' if settings.quickbooks_sandbox else 'quickbooks.api.intuit.com'
                r=httpx.get('https://'+host+'/v3/company/'+realmId+'/companyinfo/'+realmId,headers=headers,timeout=20);r.raise_for_status()
                info=r.json()['CompanyInfo']
                if info.get('Country') not in {'GB','UK'}:raise ValueError()
                choices=[{'id':realmId,'name':info['CompanyName']}]
        except (httpx.HTTPError,ValueError,KeyError):
            raise HTTPException(502,'Could not verify the UK accounting company. Reconnect and try again.') from None
        if conn and conn.company_id and conn.company_id not in {x['id'] for x in choices}:
            raise HTTPException(409,'Reconnect the original accounting company to retain correct export history')
        if not conn:conn=Connection(tenant_id=tenant_id,provider=provider);db.add(conn)
        conn.tokens_encrypted=encrypt_secret(json.dumps(tokens));conn.choices=choices
        m.audit(db,'accounting_connected','tenant',tenant_id,tenant_id=tenant_id,actor=session.user,detail={'provider':provider})
        db.commit();return RedirectResponse(settings.studio_url.rstrip('/')+'/?accounting=connected',status_code=303)

    @app.post('/api/studio/accounting/company')
    def company(payload:CompanyIn,session:UserSession=Depends(m.require_csrf),db:Session=Depends(get_db)):
        tenant=owner(db,session);conn=conn_for(db,tenant.id)
        if conn.company_id and conn.company_id!=payload.company_id:raise HTTPException(409,'The receiving accounting company cannot be changed after selection')
        choice=next((x for x in conn.choices if x['id']==payload.company_id),None)
        if not choice:raise HTTPException(422,'Choose a company from your authorised accounts')
        refresh(db,conn)
        conn.company_id=choice['id'];conn.company_name=choice['name']
        if conn.provider=='xero':
            orgs=api(db,conn,'GET','/Organisation').get('Organisations',[])
            if not orgs or orgs[0].get('CountryCode')!='GB' or orgs[0].get('BaseCurrency')!='GBP':
                raise HTTPException(422,'This release supports UK companies with GBP as their base currency')
        # One remote company per Ivory business; avoid cross-business accounting merges.
        set_database_tenant(db,platform_admin=True)
        if db.bind.dialect.name=='postgresql':
            from sqlalchemy import text
            db.execute(text('SELECT pg_advisory_xact_lock(590510)'))
        clash=db.scalar(select(Connection).where(Connection.provider==conn.provider,Connection.company_id==conn.company_id,Connection.tenant_id!=tenant.id))
        if clash:raise HTTPException(409,'This accounting company is already assigned to another business')
        set_database_tenant(db,tenant.id);db.commit();return {'ok':True}

    @app.get('/api/studio/accounting/options')
    def get_options(session:UserSession=Depends(m.session_dependency),db:Session=Depends(get_db)):
        tenant=owner(db,session);conn=conn_for(db,tenant.id)
        if not conn.company_id:raise HTTPException(409,'Select your company first')
        return options(db,conn)

    @app.put('/api/studio/accounting/settings')
    def save(payload:MappingIn,session:UserSession=Depends(m.require_csrf),db:Session=Depends(get_db)):
        tenant=owner(db,session);conn=conn_for(db,tenant.id)
        if not payload.confirmed:raise HTTPException(422,'Confirm the tax and payment account choices first')
        opts=options(db,conn)
        for key,group in [('sales','sales'),('tax','tax'),('bank','bank'),('stripe_bank','bank')]:
            if getattr(payload,key) not in {x['id'] for x in opts[group]}:raise HTTPException(422,'Select valid accounts and a tax code from the connected company')
        conn.mapping={k:getattr(payload,k) for k in ['sales','tax','bank','stripe_bank']}
        conn.enabled=True;conn.auto_sync=payload.auto_sync
        if not conn.sync_from:conn.sync_from=utcnow()
        m.audit(db,'accounting_settings_saved','tenant',tenant.id,tenant_id=tenant.id,actor=session.user,detail={'auto_sync':payload.auto_sync})
        db.commit();return {'ok':True}

    @app.post('/api/studio/accounting/disconnect')
    def disconnect(session:UserSession=Depends(m.require_csrf),db:Session=Depends(get_db)):
        tenant=owner(db,session);conn=conn_for(db,tenant.id)
        conn.enabled=False;conn.auto_sync=False;conn.tokens_encrypted='';conn.choices=[]
        m.audit(db,'accounting_disconnected','tenant',tenant.id,tenant_id=tenant.id,actor=session.user)
        db.commit();return {'ok':True}

    @app.post('/api/studio/accounting/exports/{export_id}/link')
    def link(export_id:str,payload:LinkIn,session:UserSession=Depends(m.require_csrf),db:Session=Depends(get_db)):
        tenant=owner(db,session);conn=conn_for(db,tenant.id)
        row=db.scalar(select(Export).where(Export.id==export_id,Export.tenant_id==tenant.id).with_for_update())
        if not row or row.state not in {'sending','review'}:raise HTTPException(409,'Choose an export awaiting review')
        resource={'invoice':('Invoices','invoice','Invoice','InvoiceID'),'contact':('Contacts','customer','Customer','ContactID'),'payment':('Payments','payment','Payment','PaymentID')}[row.kind]
        data=api(db,conn,'GET','/'+(resource[0] if conn.provider=='xero' else resource[1])+'/'+quote(payload.remote_id,safe=''))
        try:
            remote=data[resource[0]][0] if conn.provider=='xero' else data[resource[2]]
            if not isinstance(remote,dict):raise ValueError()
        except (KeyError,IndexError,TypeError,ValueError):
            raise HTTPException(422,'The provider did not return a matching accounting record') from None
        expected=row.payload
        valid=False
        if row.kind=='invoice':
            original=expected['Invoices'][0] if conn.provider=='xero' else expected
            ref=remote.get('Reference') if conn.provider=='xero' else remote.get('PrivateNote')
            total=remote.get('Total') if conn.provider=='xero' else remote.get('TotalAmt')
            gross=original['LineItems'][0]['UnitAmount'] if conn.provider=='xero' else original['Line'][0]['Amount']
            if conn.provider=='xero':
                customer_ok=remote.get('Contact',{}).get('ContactID')==original['Contact']['ContactID'] and remote.get('CurrencyCode')=='GBP'
            else:
                customer_ok=remote.get('CustomerRef',{}).get('value')==original['CustomerRef']['value'] and remote.get('CurrencyRef',{}).get('value')=='GBP'
            valid=customer_ok and ref=='IVORY:'+row.source_id and money_equal(total,gross)
        elif row.kind=='contact':
            valid=(remote.get('ContactNumber')=='IVORY-'+row.source_id) if conn.provider=='xero' else (remote.get('DisplayName')==expected.get('DisplayName') and remote.get('PrimaryEmailAddr')==expected.get('PrimaryEmailAddr'))
        else:
            original=expected['Payments'][0] if conn.provider=='xero' else expected
            if conn.provider=='xero':
                valid=remote.get('Reference')=='IVORY:'+row.source_id and remote.get('Invoice',{}).get('InvoiceID')==original['Invoice']['InvoiceID'] and money_equal(remote.get('Amount'),original['Amount'])
            else:
                valid=remote.get('PrivateNote')=='IVORY:'+row.source_id and remote.get('Line') and remote['Line'][0].get('LinkedTxn')==original['Line'][0]['LinkedTxn'] and money_equal(remote.get('TotalAmt'),original['TotalAmt'])
        if not valid:raise HTTPException(422,'This remote record does not match the original export')
        row.remote_id=payload.remote_id;row.state='synced';row.message='Matched to the provider record after review'
        m.audit(db,'accounting_export_reconciled','accounting_export',row.id,tenant_id=tenant.id,actor=session.user)
        db.commit();return {'ok':True}

    @app.post('/api/studio/accounting/invoices/{invoice_id}/sync')
    def sync(invoice_id:str,session:UserSession=Depends(m.require_csrf),db:Session=Depends(get_db)):
        tenant=owner(db,session);sync_one(db,tenant.id,invoice_id)
        set_database_tenant(db,tenant.id);m.audit(db,'accounting_invoice_synced','invoice',invoice_id,tenant_id=tenant.id,actor=session.user);db.commit()
        return {'ok':True}


def process_accounting(heartbeat=lambda: None):
    if not settings.accounting_enabled:return
    with SessionLocal() as scan:
        set_database_tenant(scan,platform_admin=True)
        ids=list(scan.scalars(select(Connection.tenant_id).join(Tenant,Tenant.id==Connection.tenant_id).where(
            Connection.enabled.is_(True),Connection.auto_sync.is_(True),Tenant.status.in_([TenantStatus.ACTIVE,TenantStatus.TRIAL]))))
    for tenant_id in ids:
        heartbeat()
        with SessionLocal() as db:
            set_database_tenant(db,tenant_id);conn=db.get(Connection,tenant_id)
            if not conn.sync_from:continue
            exports={(x.kind,x.source_id):x for x in db.scalars(select(Export).where(Export.tenant_id==tenant_id))}
            invoices=list(db.scalars(select(BookingInvoice).where(BookingInvoice.tenant_id==tenant_id).order_by(BookingInvoice.created_at)))
            payment_ids={p.invoice_id for p in db.scalars(select(BookingPayment).where(BookingPayment.tenant_id==tenant_id))
                if ('payment',p.id) not in exports}
            candidates=[]
            for inv in invoices:
                exp=exports.get(('invoice',inv.id))
                if exp and exp.state in {'review','sending','error'}:continue
                created=inv.created_at.replace(tzinfo=timezone.utc) if inv.created_at.tzinfo is None else inv.created_at
                since=conn.sync_from.replace(tzinfo=timezone.utc) if conn.sync_from.tzinfo is None else conn.sync_from
                if (not exp and created>=since) or (exp and (exp.fingerprint!=digest(invoice_snapshot(inv)) or inv.id in payment_ids)):
                    candidates.append(inv.id)
            # One invoice per business per cycle. Existing synced invoices still receive later payments.
            candidates=candidates[:1]
        for invoice_id in candidates:
            heartbeat()
            with SessionLocal() as db:
                set_database_tenant(db,tenant_id)
                try:sync_one(db,tenant_id,invoice_id)
                except HTTPException:db.rollback()
