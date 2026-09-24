"""Payment contracts: signed callbacks, business scope, retries, refunds and paywall."""
import hashlib
import hmac
import json
import time
from uuid import uuid4
from datetime import timedelta
import pytest
from sqlalchemy import select, func, create_engine, text, inspect
from sqlalchemy.orm import Session
from fastapi import HTTPException
from test_phase57_experience import studio_case, csrf, accept
from app import main, payments
from app.database import SessionLocal
from app.models import (Tenant, TenantSubscription, StripeCheckout, StripeConnection, BookingInvoice,
    BookingPayment, PlatformBillingPayment, User, Membership, MembershipRole, StripeOAuthState)
from app.security import utcnow


@pytest.fixture
def stripe(monkeypatch):
    for name,value in {'stripe_enabled':True,'stripe_secret_key':'sk_test_fake',
        'stripe_webhook_secret':'whsec_platform','stripe_connect_webhook_secret':'whsec_connect',
        'stripe_connect_client_id':'ca_test','stripe_live_mode':False}.items():
        monkeypatch.setattr(payments.settings,name,value)
    objects={}; calls=[]
    def call(method,path,data=None,account='',key=None):
        calls.append((method,path,data,account,key))
        if method=='POST' and path=='/v1/checkout/sessions':
            # Stripe idempotency: same key returns the same Checkout.
            if key in objects:return objects[key]
            obj={'id':'cs_'+uuid4().hex,'url':'https://checkout.stripe.com/test', 'livemode':False,
                'status':'open','metadata':{'attempt_id':data['metadata[attempt_id]']},
                'amount_total':int(data['line_items[0][price_data][unit_amount]']), 'currency':'gbp',
                'payment_status':'unpaid'}
            objects[key]=obj;objects['/v1/checkout/sessions/'+obj['id']]=obj;return obj
        if method=='POST' and path.endswith('/expire'):
            objects[path[:-7]]['status']='expired';return objects[path[:-7]]
        if path.startswith('/v1/accounts/'):
            return {'charges_enabled':True}
        return objects[path]
    monkeypatch.setattr(payments,'stripe_call',call)
    return objects,calls


def connect(c):
    with SessionLocal() as db:
        db.add(StripeConnection(tenant_id=c.ids.tenant,account_id='acct_'+c.ids.tenant,enabled=True,charges_enabled=True,livemode=False));db.commit()
    return 'acct_'+c.ids.tenant


def event(c,obj,kind='checkout.session.completed',account=None,eid=None,secret=None):
    evt={'id':eid or 'evt_'+uuid4().hex,'livemode':False,'type':kind,'data':{'object':obj}}
    if account:evt['account']=account
    raw=json.dumps(evt).encode();stamp=str(int(time.time()));secret=secret or ('whsec_connect' if account else 'whsec_platform')
    sig=hmac.new(secret.encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
    return c.public.post('/api/stripe/webhook/'+('connect' if account else 'platform'),content=raw,
        headers={'stripe-signature':f't={stamp},v1={sig}','content-type':'application/json'})


def second(c,price=0):
    r=c.manager.post('/api/manager/tenants/'+c.ids.tenant+'/businesses',headers=csrf(c.manager),json={
        'display_name':'Second studio','slug':'second-'+uuid4().hex[:10], 'price_pence':price})
    assert r.status_code==201,r.text
    return r.json()['id']


def test_second_business_grant_switch_isolation_and_stale_tabs(studio_case):
    c=studio_case;tid=second(c)
    r=c.manager.post('/api/manager/tenants/'+c.ids.tenant+'/businesses',headers=csrf(c.manager),json={'display_name':'Third studio','slug':'third-'+uuid4().hex[:10]})
    assert r.status_code==409
    assert len(c.studio.get('/api/studio/businesses').json()['businesses'])==2
    assert c.studio.post('/api/studio/businesses/switch',headers=csrf(c.studio),json={'tenant_id':c.ids.other}).status_code==403
    assert c.studio.post('/api/studio/businesses/switch',headers=csrf(c.studio),json={'tenant_id':tid}).status_code==200
    assert c.studio.get('/api/studio/dashboard').json()['tenant']['id']==tid
    assert c.studio.get('/api/studio/bookings/'+c.ids.booking+'/journey').status_code in {404,405}
    # Old tabs cannot issue writes against the newly selected business.
    r=c.studio.patch('/api/studio/branding',headers={**csrf(c.studio),'X-Ivory-Business':c.ids.tenant},json={'display_name':'Wrong business'})
    assert r.status_code==409
    banks=c.studio.get('/api/studio/bank-accounts').json()
    assert 'Studio account' not in str(banks)
    assert c.studio.get('/api/studio/payments/settings').json()['subscription']['provider']=='complimentary'
    assert c.studio.post('/api/studio/payments/subscription-checkout',headers=csrf(c.studio)).status_code==409


def test_second_paid_business_requires_payment_or_manager_grant(studio_case):
    c=studio_case;tid=second(c,1500)
    c.studio.post('/api/studio/businesses/switch',headers=csrf(c.studio),json={'tenant_id':tid})
    assert c.studio.get('/api/studio/dashboard').status_code==403
    assert c.studio.get('/api/studio/payments/settings').json()['subscription']['price_pence']==1500
    assert c.studio.put('/api/manager/tenants/'+tid+'/business-access',headers=csrf(c.studio),json={'complimentary':True}).status_code==403
    assert c.manager.put('/api/manager/tenants/'+tid+'/business-access',headers=csrf(c.manager),json={'complimentary':True}).status_code==200
    assert c.studio.get('/api/studio/dashboard').status_code==200


def test_card_payment_signed_scoped_idempotent_and_refund(studio_case,stripe):
    c=studio_case;account=connect(c);inv=accept(c)['invoice'];objects,calls=stripe
    endpoint=c.path+'/invoices/'+inv['id']+'/card-checkout'
    assert c.public.post(endpoint,json={'portion':'booking_fee','amount_pence':1}).status_code==200
    obj=next(v for k,v in objects.items() if k.startswith('/v1/checkout/'))
    assert obj['amount_total']==10000
    assert calls[0][3]==account
    assert c.ids.raw not in str(calls)
    assert c.public.post(endpoint,json={'portion':'booking_fee'}).status_code==200
    with SessionLocal() as db: assert db.scalar(select(func.count()).select_from(StripeCheckout).where(StripeCheckout.invoice_id==inv['id']))==1
    obj.update(status='complete',payment_status='paid',payment_intent='pi_'+uuid4().hex)
    assert event(c,obj,account='acct_wrong').status_code==400
    assert event(c,obj,account=account,secret='bad').status_code==400
    eid='evt_'+uuid4().hex
    for same in [eid,eid,'evt_'+uuid4().hex]:
        r=event(c,obj,account=account,eid=same);assert r.status_code==200,r.text
    with SessionLocal() as db:
        assert db.get(BookingInvoice,inv['id']).paid_pence==10000
        assert db.scalar(select(func.count()).select_from(BookingPayment).where(BookingPayment.invoice_id==inv['id']))==1
    portal=c.public.get(c.path);assert 'PRIVATE HOLIDAY' not in portal.text and 'PRIVATE NOTE' not in portal.text
    assert portal.json()['invoices'][0]['payment_instructions']['bank_account_name']=='Studio account'
    charge={'id':'ch_'+uuid4().hex,'payment_intent':obj['payment_intent'],'amount_refunded':2500}
    objects['/v1/charges/'+charge['id']]=charge
    for _ in range(2):assert event(c,charge,'charge.refunded',account=account).status_code==200
    with SessionLocal() as db: assert db.get(BookingInvoice,inv['id']).paid_pence==7500


def test_payment_failure_never_marks_paid_and_cross_invoice_denied(studio_case,stripe):
    c=studio_case;account=connect(c);inv=accept(c)['invoice'];objects,calls=stripe
    assert c.public.post(c.path+'/invoices/unknown/card-checkout',json={}).status_code==404
    c.public.post(c.path+'/invoices/'+inv['id']+'/card-checkout',json={})
    obj=next(v for k,v in objects.items() if k.startswith('/v1/checkout/'))
    assert event(c,obj,account=account).status_code==200
    with SessionLocal() as db:assert db.get(BookingInvoice,inv['id']).paid_pence==0
    obj.update(status='complete',payment_status='paid',payment_intent='pi_fake',amount_total=1)
    assert event(c,obj,account=account).status_code==400
    with SessionLocal() as db:assert db.get(BookingInvoice,inv['id']).paid_pence==0


def test_manual_bank_payment_expires_pending_card_checkout(studio_case,stripe):
    c=studio_case;connect(c);inv=accept(c)['invoice'];objects,calls=stripe
    c.public.post(c.path+'/invoices/'+inv['id']+'/card-checkout',json={})
    r=c.studio.post('/api/studio/invoices/'+inv['id']+'/payments',headers=csrf(c.studio),json={'amount_pence':10000,'paid_date':'2026-09-23','payment_type':'bank_transfer'})
    assert r.status_code==201,r.text
    assert any(x[1].endswith('/expire') for x in calls)


def test_subscription_payment_activates_child_and_duplicate_receipt_once(studio_case,stripe):
    c=studio_case;tid=second(c,1500);objects,calls=stripe
    c.studio.post('/api/studio/businesses/switch',headers=csrf(c.studio),json={'tenant_id':tid})
    r=c.studio.post('/api/studio/payments/subscription-checkout',headers=csrf(c.studio));assert r.status_code==200,r.text
    assert c.studio.get('/api/studio/dashboard').status_code==403
    obj=next(v for k,v in objects.items() if k.startswith('/v1/checkout/'));subid='sub_'+uuid4().hex;invid='in_'+uuid4().hex;customer='cus_'+uuid4().hex
    obj.update(status='complete',subscription=subid,customer=customer)
    sub={'id':subid,'status':'active','customer':customer,'metadata':obj['metadata'],'latest_invoice':invid,'current_period_end':int(time.time())+2592000}
    inv={'id':invid,'status':'paid','customer':customer,'currency':'gbp','subscription':subid,'amount_paid':1500}
    objects['/v1/subscriptions/'+subid]=sub;objects['/v1/invoices/'+invid]=inv
    # An incomplete subscription may arrive before card authentication finishes.
    sub['status']='incomplete';inv['status']='open';obj['status']='open'
    assert event(c,sub,'customer.subscription.created').status_code==200
    assert c.studio.get('/api/studio/dashboard').status_code==403
    r=c.studio.post('/api/studio/payments/subscription-checkout',headers=csrf(c.studio))
    assert r.status_code==200 and r.json()['url']==obj['url']
    sub['status']='active';inv['status']='paid';obj['status']='complete'
    # invoice.paid may precede checkout.session.completed.
    assert event(c,inv,'invoice.paid').status_code==200
    for _ in range(2):
        r=event(c,obj);assert r.status_code==200,r.text
    assert c.studio.get('/api/studio/dashboard').status_code==200
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(PlatformBillingPayment).where(PlatformBillingPayment.tenant_id==tid))==1
    assert c.manager.put('/api/manager/tenants/'+tid+'/business-access',headers=csrf(c.manager),json={'complimentary':True}).status_code==409
    # Successful renewals must not lift a deliberate Manager suspension.
    r=c.manager.post('/api/manager/tenants/'+tid+'/billing/suspend',headers=csrf(c.manager),json={'reason':'Manual support hold'})
    assert r.status_code==200,r.text
    assert event(c,inv,'invoice.paid').status_code==200
    assert c.studio.get('/api/studio/dashboard').status_code==403
    sub['status']='canceled'
    assert event(c,sub,'customer.subscription.deleted').status_code==200
    assert c.studio.get('/api/studio/dashboard').status_code==403
    assert c.manager.put('/api/manager/tenants/'+tid+'/business-access',headers=csrf(c.manager),json={'complimentary':True}).status_code==200
    # Late events from the old subscription must not undo a free grant.
    assert event(c,sub,'customer.subscription.deleted').status_code==200
    assert c.studio.get('/api/studio/dashboard').status_code==200


def test_disabled_stripe_and_owner_only_controls(studio_case,monkeypatch):
    c=studio_case;inv=accept(c)['invoice'];monkeypatch.setattr(payments.settings,'stripe_enabled',False)
    assert not c.public.get(c.path).json()['invoices'][0]['card_available']
    assert c.public.post(c.path+'/invoices/'+inv['id']+'/card-checkout',json={}).status_code==409
    with SessionLocal() as db:
        mem=db.scalar(select(Membership).where(Membership.user_id==c.ids.owner));mem.role=MembershipRole.STAFF;db.commit()
    assert c.studio.get('/api/studio/payments/settings').status_code==403
    assert c.studio.post('/api/studio/payments/connect',headers=csrf(c.studio)).status_code==403


def test_signature_age_and_scope(studio_case,stripe):
    c=studio_case
    assert c.public.post('/api/stripe/webhook/platform',content=b'{}').status_code==400
    body=b'{"id":"evt_old","data":{"object":{}}}'
    stamp=str(int(time.time())-400)
    sig=hmac.new(b'whsec_platform',stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
    assert c.public.post('/api/stripe/webhook/platform',content=body,headers={'stripe-signature':f't={stamp},v1={sig}'}).status_code==400


def test_additive_columns_repeatable():
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as db:
        db.execute(text('CREATE TABLE tenants (id VARCHAR PRIMARY KEY)'))
        db.execute(text('CREATE TABLE user_sessions (id VARCHAR PRIMARY KEY)'))
        db.execute(text("INSERT INTO tenants VALUES ('old')"))
    with Session(engine) as db:
        payments.ensure_payment_columns(db);payments.ensure_payment_columns(db)
        assert db.execute(text('SELECT primary_tenant_id FROM tenants')).scalar() is None


def test_oauth_state_requires_same_owner_and_is_consumed_on_timeout(studio_case,stripe,monkeypatch):
    from urllib.parse import urlparse, parse_qs
    import httpx
    c=studio_case
    r=c.studio.post('/api/studio/payments/connect',headers=csrf(c.studio));assert r.status_code==200
    state=parse_qs(urlparse(r.json()['url']).query)['state'][0]
    path='/api/stripe/connect/finish?state='+state+'&code=ac_test'
    assert c.public.get(path).status_code==401
    assert c.manager.get(path).status_code==400
    def timeout(*args,**kwargs):raise httpx.ReadTimeout('test timeout')
    monkeypatch.setattr(payments.httpx,'post',timeout)
    assert c.studio.get(path).status_code==502
    assert c.studio.get(path).status_code==400


def test_declining_or_expiring_checkout_allows_fresh_attempt(studio_case,stripe):
    c=studio_case;connect(c);inv=accept(c)['invoice'];objects,calls=stripe
    path=c.path+'/invoices/'+inv['id']+'/card-checkout'
    assert c.public.post(path,json={}).status_code==200
    obj=next(v for k,v in objects.items() if k.startswith('/v1/checkout/'));obj['status']='expired'
    assert c.public.post(path,json={}).status_code==200
    with SessionLocal() as db:
        statuses=list(db.scalars(select(StripeCheckout.status).where(StripeCheckout.invoice_id==inv['id'])))
        assert sorted(statuses)==['expired','pending']
