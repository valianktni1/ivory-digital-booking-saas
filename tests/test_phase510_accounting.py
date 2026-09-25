import json
import time
from uuid import uuid4
from datetime import timedelta
from urllib.parse import urlparse,parse_qs
from decimal import Decimal
import pytest
from sqlalchemy import select,func
from fastapi import HTTPException
from test_phase57_experience import studio_case,csrf,accept
from app import main,accounting as a
from app.database import SessionLocal
from app.models import AccountingConnection as Connection,AccountingExport as Export,BookingPayment,BookingInvoice,Membership,MembershipRole
from app.security import encrypt_secret,utcnow


@pytest.fixture
def provider(monkeypatch):
    for k,v in {'accounting_enabled':True,'xero_client_id':'test','xero_client_secret':'secret','quickbooks_client_id':'test','quickbooks_client_secret':'secret'}.items():monkeypatch.setattr(a.settings,k,v)
    calls=[];remote={};fail={'path':None,'definite':False,'wrong_total':False}
    def api(db,conn,method,path,body=None,key=None):
        calls.append((conn.tenant_id,conn.provider,method,path,body,key))
        if fail['path']==path:
            exc=HTTPException(502,'Simulated provider failure');exc.definite=fail['definite'];raise exc
        if method=='GET':
            if path=='/Accounts':return {'Accounts':[{'AccountID':'bank1','Code':'200','Name':'Sales','Type':'REVENUE','Status':'ACTIVE'},{'AccountID':'bank1','Name':'Bank','Type':'BANK','Status':'ACTIVE'}]}
            if path=='/TaxRates':return {'TaxRates':[{'TaxType':'NONE','Name':'No VAT','Status':'ACTIVE'}]}
            if path=='/Organisation':return {'Organisations':[{'CountryCode':'GB','BaseCurrency':'GBP'}]}
            return remote[path]
        id=str(uuid4());data=None
        if conn.provider=='xero':
            name=path.strip('/');id_key={'Contacts':'ContactID','Invoices':'InvoiceID','Payments':'PaymentID'}[name]
            item={**body[name][0],id_key:id}
            if name=='Invoices':item['Total']=item['LineItems'][0]['UnitAmount']+(1 if fail['wrong_total'] else 0)
            data={name:[item]};remote[path+'/'+id]=data
        else:
            name={'customer':'Customer','invoice':'Invoice','payment':'Payment'}[path.strip('/')]
            item={**body,'Id':id}
            if name=='Invoice':item['TotalAmt']=item['Line'][0]['Amount']
            data={name:item};remote[path+'/'+id]=data
        return data
    monkeypatch.setattr(a,'api',api)
    return calls,remote,fail


def connection(c,provider='xero',auto=False):
    with SessionLocal() as db:
        conn=Connection(tenant_id=c.ids.tenant,provider=provider,company_id='company-'+c.ids.tenant,company_name='Selected company',
            choices=[{'id':'company-'+c.ids.tenant,'name':'Selected company'}],
            tokens_encrypted=encrypt_secret(json.dumps({'access_token':'DO-NOT-LEAK','refresh_token':'SECRET','expires_at':time.time()+3600})),
            mapping={'sales':'200','tax':'NONE','bank':'bank1','stripe_bank':'bank1'},enabled=True,auto_sync=auto,sync_from=utcnow()-timedelta(minutes=1))
        db.add(conn);db.commit()


def sync(c,inv):return c.studio.post('/api/studio/accounting/invoices/'+inv['id']+'/sync',headers=csrf(c.studio))


@pytest.mark.parametrize('vendor',['xero','quickbooks'])
def test_invoice_and_payments_once_with_correct_totals(studio_case,provider,vendor):
    c=studio_case;connection(c,vendor);inv=accept(c)['invoice'];calls,remote,fail=provider
    with SessionLocal() as db:
        db.add(BookingPayment(tenant_id=c.ids.tenant,invoice_id=inv['id'],amount_pence=10000,paid_date=utcnow().date(),payment_type='bank_transfer'));db.commit()
    r=sync(c,inv);assert r.status_code==200,r.text
    assert sync(c,inv).status_code==200
    writes=[x for x in calls if x[2] in {'POST','PUT'}];assert len(writes)==3
    assert all(x[0]==c.ids.tenant and x[1]==vendor and 0<len(x[5])<=50 for x in writes)
    body=writes[1][4];assert 'PRIVATE' not in str(body) and c.ids.raw not in str(body)
    if vendor=='xero':
        assert body['Invoices'][0]['LineItems'][0]['UnitAmount']==inv['total_pence']/100
        assert writes[-1][2]=='PUT'  # Xero batch Payments endpoint
    else:assert body['Line'][0]['Amount']==inv['total_pence']/100 and body['EmailStatus']=='NotSet'
    status=c.studio.get('/api/studio/accounting');assert status.status_code==200
    assert 'DO-NOT-LEAK' not in status.text and 'SECRET' not in status.text and 'tokens_encrypted' not in status.text
    assert 'accounting' not in c.public.get(c.path).text


def test_uncertain_invoice_does_not_retry_and_can_match_verified_record(studio_case,provider):
    c=studio_case;connection(c);inv=accept(c)['invoice'];calls,remote,fail=provider
    fail['path']='/Invoices';assert sync(c,inv).status_code==409
    fail['path']=None;assert sync(c,inv).status_code==409
    assert len([x for x in calls if x[3]=='/Invoices'])==1
    with SessionLocal() as db:
        row=db.scalar(select(Export).where(Export.source_id==inv['id'],Export.kind=='invoice'));rid=row.id;payload=row.payload
    remote['/Invoices/remote-1']={'Invoices':[{**payload['Invoices'][0],'InvoiceID':'remote-1','Total':inv['total_pence']/100}]}
    r=c.studio.post('/api/studio/accounting/exports/'+rid+'/link',headers=csrf(c.studio),json={'remote_id':'remote-1'});assert r.status_code==200,r.text
    assert sync(c,inv).status_code==200


def test_definite_rejection_retry_and_total_mismatch_review(studio_case,provider):
    c=studio_case;connection(c);inv=accept(c)['invoice'];calls,remote,fail=provider
    fail.update(path='/Invoices',definite=True);assert sync(c,inv).status_code==409
    fail.update(path=None,wrong_total=True);assert sync(c,inv).status_code==409
    with SessionLocal() as db:
        row=db.scalar(select(Export).where(Export.source_id==inv['id']));assert row.state=='review' and row.remote_id
    assert sync(c,inv).status_code==409


def test_refunds_and_invoice_edits_need_review(studio_case,provider):
    c=studio_case;connection(c);inv=accept(c)['invoice'];calls,remote,fail=provider
    assert sync(c,inv).status_code==200
    with SessionLocal() as db:
        db.add(BookingPayment(tenant_id=c.ids.tenant,invoice_id=inv['id'],amount_pence=-1000,paid_date=utcnow().date(),payment_type='stripe_refund'));db.commit()
    assert sync(c,inv).status_code==200
    assert any(e['state']=='review' and e['kind']=='payment' for e in c.studio.get('/api/studio/accounting').json()['exports'])
    with SessionLocal() as db:
        invoice=db.get(BookingInvoice,inv['id']);invoice.status='void';db.commit()
    assert sync(c,inv).status_code==409
    assert len([x for x in calls if x[2] in {'POST','PUT'}])==2


def test_owner_isolation_company_binding_and_csrf(studio_case,provider):
    c=studio_case;connection(c);inv=accept(c)['invoice']
    assert c.public.get('/api/studio/accounting').status_code==401
    assert c.manager.get('/api/studio/accounting').status_code==403
    assert c.studio.post('/api/studio/accounting/invoices/'+inv['id']+'/sync').status_code==403
    assert c.studio.post('/api/studio/accounting/invoices/not-their-invoice/sync',headers=csrf(c.studio)).status_code==404
    assert c.studio.post('/api/studio/accounting/company',headers=csrf(c.studio),json={'company_id':'other-company'}).status_code==409
    assert c.studio.put('/api/studio/accounting/settings',headers=csrf(c.studio),json={'sales':'200','tax':'WRONG','bank':'bank1','stripe_bank':'bank1','confirmed':True}).status_code==422
    with SessionLocal() as db:
        membership=db.scalar(select(Membership).where(Membership.user_id==c.ids.owner));membership.role=MembershipRole.STAFF;db.commit()
    assert c.studio.get('/api/studio/accounting').status_code==403


def test_disconnect_retains_history_and_prevents_writes(studio_case,provider):
    c=studio_case;connection(c);inv=accept(c)['invoice'];assert sync(c,inv).status_code==200
    assert c.studio.post('/api/studio/accounting/disconnect',headers=csrf(c.studio)).status_code==200
    assert sync(c,inv).status_code==409
    with SessionLocal() as db:
        conn=db.get(Connection,c.ids.tenant);assert not conn.tokens_encrypted and not conn.enabled
        assert db.scalar(select(func.count()).select_from(Export).where(Export.tenant_id==c.ids.tenant))==2


def test_automatic_new_invoices_and_later_payments(studio_case,provider):
    c=studio_case;connection(c,auto=True);inv=accept(c)['invoice'];a.process_accounting()
    with SessionLocal() as db:
        assert db.scalar(select(Export).where(Export.source_id==inv['id'])).state=='synced'
        db.add(BookingPayment(tenant_id=c.ids.tenant,invoice_id=inv['id'],amount_pence=10000,paid_date=utcnow().date(),payment_type='stripe'));db.commit()
    a.process_accounting();a.process_accounting()
    assert len([x for x in provider[0] if x[3]=='/Payments'])==1


def test_oauth_requires_owner_and_single_use(studio_case,provider,monkeypatch):
    c=studio_case
    r=c.studio.post('/api/studio/accounting/connect',headers=csrf(c.studio),json={'provider':'xero'});assert r.status_code==200
    state=parse_qs(urlparse(r.json()['url']).query)['state'][0]
    endpoint='/api/accounting/xero/finish?state='+state+'&code=test'
    assert c.public.get(endpoint).status_code==401
    assert c.manager.get(endpoint).status_code==400
    def fail(*args,**kwargs):raise HTTPException(502,'Failed exchange')
    monkeypatch.setattr(a,'token_request',fail)
    assert c.studio.get(endpoint).status_code==502
    assert c.studio.get(endpoint).status_code==400


@pytest.mark.parametrize('vendor',['xero','quickbooks'])
def test_refresh_rotation_survives_failed_api_request(studio_case,monkeypatch,vendor):
    import httpx
    from app.security import decrypt_secret
    c=studio_case
    monkeypatch.setattr(a.settings,'accounting_enabled',True)
    monkeypatch.setattr(a.settings,vendor+'_client_id','test-client')
    monkeypatch.setattr(a.settings,vendor+'_client_secret','test-secret')
    monkeypatch.setattr(a.settings,'quickbooks_sandbox',True)
    connection(c,vendor)
    with SessionLocal() as db:
        conn=db.get(Connection,c.ids.tenant)
        conn.tokens_encrypted=encrypt_secret(json.dumps({'access_token':'expired','refresh_token':'old','expires_at':0}));db.commit()
    requests=[]
    def token(url,**kwargs):
        assert url==a.PROVIDERS[vendor]['token']
        assert kwargs['data']=={'grant_type':'refresh_token','refresh_token':'old'}
        assert kwargs['auth']==('test-client','test-secret')
        return httpx.Response(200,json={'access_token':'new-access','refresh_token':'rotated','expires_in':1800},request=httpx.Request('POST',url))
    def request(method,url,**kwargs):
        requests.append((method,url,kwargs))
        raise httpx.ReadTimeout('Simulated lost response')
    monkeypatch.setattr(a.httpx,'post',token)
    monkeypatch.setattr(a.httpx,'request',request)
    with SessionLocal() as db:
        conn=a.conn_for(db,c.ids.tenant)
        with pytest.raises(HTTPException) as caught:
            a.api(db,conn,'POST','/Invoices' if vendor=='xero' else '/invoice',{},key='stable-request')
        assert caught.value.status_code==502 and not getattr(caught.value,'definite',False)
        db.rollback()
    with SessionLocal() as db:
        saved=json.loads(decrypt_secret(db.get(Connection,c.ids.tenant).tokens_encrypted))
        assert saved['refresh_token']=='rotated'
    method,url,kwargs=requests[0]
    assert kwargs['headers']['Authorization']=='Bearer new-access'
    if vendor=='xero':
        assert kwargs['headers']['xero-tenant-id']=='company-'+c.ids.tenant
        assert kwargs['headers']['Idempotency-Key']=='stable-request'
    else:
        assert 'sandbox-quickbooks.api.intuit.com' in url and 'requestid=stable-request' in url
