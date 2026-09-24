"""Two businesses per seat and optional Stripe. All amounts originate on the server."""
import hashlib
import hmac
import json
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode, quote

import httpx
from fastapi import Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel, Field
from typing import Literal
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import (Tenant, TenantStatus, User, Membership, MembershipRole, UserSession,
    TenantSubscription, Workflow, Booking, BookingJourney, BookingInvoice, BookingPayment,
    PlatformBillingPayment, StudioNotification, StripeConnection, StripeOAuthState, StripeCheckout, StripeEvent,
    StripeBillingReceipt, uid)
from .security import token_hash, utcnow
from .tenant_context import set_database_tenant

settings = get_settings()


def ensure_payment_columns(db):
    tables = set(inspect(db.bind).get_table_names())
    for table, column, definition in [
        ('tenants', 'primary_tenant_id', 'VARCHAR(36) REFERENCES tenants(id)'),
        ('user_sessions', 'active_tenant_id', 'VARCHAR(36) REFERENCES tenants(id)'),
    ]:
        if table not in tables:
            continue
        if column not in {c['name'] for c in inspect(db.bind).get_columns(table)}:
            db.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {definition}'))
    if 'tenants' in tables:
        db.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_tenants_primary ON tenants (primary_tenant_id)'))
    db.commit()
    set_database_tenant(db, platform_admin=True)


def ready():
    prefix = 'sk_live_' if settings.stripe_live_mode else 'sk_test_'
    return bool(settings.stripe_enabled and settings.stripe_secret_key.startswith(prefix)
                and settings.stripe_webhook_secret and settings.stripe_connect_webhook_secret)


def stripe_call(method, path, data=None, account='', key=None):
    if not ready():
        raise HTTPException(503, 'Stripe is not configured yet. Bank transfer remains available.')
    headers = {'Authorization': 'Bearer '+settings.stripe_secret_key,
               'Stripe-Version': settings.stripe_api_version}
    if account:
        headers['Stripe-Account'] = account
    if key:
        headers['Idempotency-Key'] = key
    try:
        response = httpx.request(method, 'https://api.stripe.com'+path,
                                 data=data, headers=headers, timeout=25)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, 'Stripe could not complete that request. Please retry shortly.') from None


def card_available(db, tenant_id):
    if not ready():
        return False
    row = db.get(StripeConnection, tenant_id)
    return bool(row and row.enabled and row.charges_enabled and row.livemode == settings.stripe_live_mode)


def member(db, session, tenant_id=None, owner=False):
    if session.user.is_platform_admin:
        raise HTTPException(403, 'Sign in with the business owner account')
    tenant_id = tenant_id or session.active_tenant_id
    q = select(Membership).where(Membership.user_id == session.user_id)
    if tenant_id:
        q = q.where(Membership.tenant_id == tenant_id)
    row = db.scalar(q.order_by(Membership.created_at).limit(1))
    if not row or (owner and row.role != MembershipRole.OWNER):
        raise HTTPException(403, 'Business owner access is required')
    set_database_tenant(db, row.tenant_id)
    tenant = db.get(Tenant, row.tenant_id)
    if not tenant or tenant.status == TenantStatus.CANCELLED:
        raise HTTPException(403, 'This business is no longer available')
    return tenant


def prevent_pending_subscription_change(db, tenant_id):
    pending = db.scalar(select(StripeCheckout).where(StripeCheckout.tenant_id == tenant_id,
        StripeCheckout.kind == 'subscription', StripeCheckout.status == 'pending'))
    if pending and pending.session_id and ready():
        obj = stripe_call('GET', '/v1/checkout/sessions/'+pending.session_id)
        if obj.get('status') == 'expired':
            pending.status = 'expired'; db.flush(); return
    if pending:
        # Never change price while an already-created Checkout may still charge the old amount.
        raise HTTPException(409, 'A subscription checkout is pending. Complete or expire it in Stripe and retry after reconciliation.')


def expire_invoice_checkout(db, invoice):
    for row in db.scalars(select(StripeCheckout).where(StripeCheckout.invoice_id == invoice.id,
            StripeCheckout.status == 'pending').with_for_update()).all():
        if not row.session_id:
            raise HTTPException(409, 'A card checkout is being prepared. Please retry shortly.')
        checkout = stripe_call('GET', '/v1/checkout/sessions/'+quote(row.session_id, safe=''), account=row.account_id)
        if checkout.get('status') == 'complete':
            raise HTTPException(409, 'A card payment is processing. Wait for Stripe confirmation before recording another payment.')
        if checkout.get('status') == 'open':
            stripe_call('POST', '/v1/checkout/sessions/'+row.session_id+'/expire', account=row.account_id,
                        key='expire-'+row.id)
        row.status = 'expired'


def checkout_url(db, row):
    # Intent was committed before the external request. Same row and key survive timeouts.
    response = stripe_call('POST', '/v1/checkout/sessions', row.request_data,
                           account=row.account_id, key='checkout-'+row.id)
    if response.get('livemode') != settings.stripe_live_mode:
        raise HTTPException(502, 'Stripe mode mismatch')
    row.session_id = response['id']; row.url = response.get('url') or ''
    if not row.url.startswith('https://checkout.stripe.com/'):
        raise HTTPException(502, 'Stripe did not return an active checkout')
    url = row.url
    db.commit()
    return {'url': url}


def get_pending(db, tenant_id, kind, invoice_id=None):
    q = select(StripeCheckout).where(StripeCheckout.tenant_id == tenant_id,
        StripeCheckout.kind == kind, StripeCheckout.status == 'pending')
    if invoice_id:
        q = q.where(StripeCheckout.invoice_id == invoice_id)
    row = db.scalar(q.with_for_update())
    if row and row.session_id:
        obj = stripe_call('GET', '/v1/checkout/sessions/'+row.session_id, account=row.account_id)
        if obj.get('status') == 'expired':
            row.status = 'expired'; db.flush(); return None
        if obj.get('status') == 'complete':
            raise HTTPException(409, 'Payment is being confirmed. Please refresh shortly.')
    elif row and aware(row.expires_at) <= utcnow():
        # An uncertain external result must be recovered with the original key, not replaced.
        raise HTTPException(409, 'Checkout needs reconciliation. Please contact Ivory Digital.')
    return row


def aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


class SecondBusinessIn(BaseModel):
    display_name: str = Field(min_length=2, max_length=160)
    slug: str = Field(min_length=2, max_length=80, pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    price_pence: int = Field(default=0, ge=0, le=10000000)
    billing_cycle: Literal['monthly', 'yearly'] = 'monthly'


class BusinessSwitchIn(BaseModel):
    tenant_id: str


class GrantIn(BaseModel):
    complimentary: bool
    price_pence: int = Field(default=0, ge=0, le=10000000)
    billing_cycle: Literal['monthly', 'yearly'] = 'monthly'


class CardChoiceIn(BaseModel):
    portion: Literal['booking_fee', 'balance'] = 'booking_fee'


class EnabledIn(BaseModel):
    enabled: bool


def register_routes(m):
    app = m.app

    @app.get('/api/studio/businesses')
    def businesses(session: UserSession = Depends(m.session_dependency), db: Session = Depends(get_db)):
        if session.user.is_platform_admin:
            raise HTTPException(403, 'Use Studio as a photographer')
        rows = db.scalars(select(Membership).where(Membership.user_id == session.user_id).order_by(Membership.created_at)).all()
        selected = session.active_tenant_id or (rows[0].tenant_id if rows else None)
        return {'selected': selected, 'businesses': [{'id': r.tenant_id, 'name': r.tenant.display_name,
                    'status': r.tenant.status.value, 'role': r.role.value} for r in rows]}

    @app.post('/api/studio/businesses/switch')
    def switch(payload: BusinessSwitchIn, session: UserSession = Depends(m.require_csrf), db: Session = Depends(get_db)):
        tenant = member(db, session, payload.tenant_id)
        session.active_tenant_id = tenant.id
        m.audit(db, 'business_switched', 'tenant', tenant.id, actor=session.user, tenant_id=tenant.id)
        db.commit()
        return {'ok': True}

    @app.get('/api/manager/tenants/{tenant_id}/businesses')
    def seat(tenant_id: str, admin=Depends(m.platform_admin), db: Session = Depends(get_db)):
        tenant = db.get(Tenant, tenant_id)
        if not tenant:
            raise HTTPException(404, 'Business not found')
        root = tenant.primary_tenant_id or tenant.id
        rows = db.scalars(select(Tenant).where((Tenant.id == root) | (Tenant.primary_tenant_id == root))).all()
        return {'primary_id': root, 'businesses': [{'id': t.id, 'name': t.display_name, 'status': t.status.value,
            'second': bool(t.primary_tenant_id), 'subscription': m.subscription_json(m.ensure_subscription(db,t),t)} for t in rows]}

    @app.post('/api/manager/tenants/{tenant_id}/businesses', status_code=201)
    def add_business(tenant_id: str, payload: SecondBusinessIn, admin=Depends(m.platform_admin_write), db: Session = Depends(get_db)):
        root = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if not root or root.primary_tenant_id:
            raise HTTPException(422, 'Choose the primary business for this seat')
        if db.scalar(select(Tenant.id).where(Tenant.primary_tenant_id == root.id)):
            raise HTTPException(409, 'This seat already has two businesses')
        owner = db.scalar(select(Membership).where(Membership.tenant_id == root.id, Membership.role == MembershipRole.OWNER))
        if not owner:
            raise HTTPException(409, 'The owner must accept their first business invitation before adding another')
        if db.scalar(select(Tenant.id).where(Tenant.slug == payload.slug)):
            raise HTTPException(409, 'That client address is already in use')
        free = payload.price_pence == 0
        tenant = Tenant(primary_tenant_id=root.id, slug=payload.slug, display_name=payload.display_name.strip(),
            owner_email=root.owner_email, timezone=root.timezone,
            status=TenantStatus.ACTIVE if free else TenantStatus.SUSPENDED,
            trial_ends_at=utcnow(), automations_paused=True, onboarding={},
            branding={'display_name': payload.display_name.strip(), 'accent_colour': '#a9782e'})
        db.add(tenant); db.flush()
        db.add(Membership(tenant_id=tenant.id, user_id=owner.user_id, role=MembershipRole.OWNER))
        db.add(TenantSubscription(tenant_id=tenant.id, plan_name='Second business', price_pence=payload.price_pence,
            billing_cycle=payload.billing_cycle, billing_status='complimentary' if free else 'payment_required',
            provider='complimentary' if free else 'manual', auto_suspend=not free))
        m.ensure_questionnaire_templates(db, tenant)
        db.add(Workflow(tenant_id=tenant.id, name='Main client journey', is_active=False))
        (settings.tenant_storage_root / tenant.storage_key).mkdir(parents=True, exist_ok=True)
        m.audit(db, 'second_business_created', 'tenant', tenant.id, actor=admin, tenant_id=tenant.id,
                detail={'primary_tenant_id': root.id, 'price_pence': payload.price_pence})
        db.commit()
        return {'id': tenant.id, 'name': tenant.display_name}

    @app.put('/api/manager/tenants/{tenant_id}/business-access')
    def access(tenant_id: str, payload: GrantIn, admin=Depends(m.platform_admin_write), db: Session = Depends(get_db)):
        tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if not tenant or not tenant.primary_tenant_id:
            raise HTTPException(422, 'This control is for the second business')
        sub = m.ensure_subscription(db, tenant)
        if sub.provider_subscription_id:
            # Requires confirmed cancellation to avoid collecting money while granting free access.
            obj = stripe_call('GET', '/v1/subscriptions/'+sub.provider_subscription_id)
            if obj.get('status') not in {'canceled', 'incomplete_expired'}:
                raise HTTPException(409, 'Cancel this subscription in Stripe first, then grant free access or change the price here.')
            old_attempt = db.scalar(select(StripeCheckout).where(StripeCheckout.subscription_id == sub.provider_subscription_id))
            if old_attempt:
                old_attempt.status = 'closed'
            sub.provider_subscription_id = ''
        prevent_pending_subscription_change(db, tenant.id)
        if not payload.complimentary and not payload.price_pence:
            raise HTTPException(422, 'Enter the subscription charge or choose free access')
        sub.price_pence = 0 if payload.complimentary else payload.price_pence
        sub.billing_cycle = payload.billing_cycle
        sub.provider = 'complimentary' if payload.complimentary else 'manual'
        sub.billing_status = 'complimentary' if payload.complimentary else 'payment_required'
        sub.next_payment_due = None; sub.auto_suspend = not payload.complimentary
        sub.suspension_reason = ''; sub.suspended_at = None
        tenant.onboarding = {**(tenant.onboarding or {}), "admin_access_hold": False}
        tenant.status = TenantStatus.ACTIVE if payload.complimentary else TenantStatus.SUSPENDED
        tenant.automations_paused = True
        m.audit(db, 'second_business_access_changed', 'tenant', tenant.id, actor=admin, tenant_id=tenant.id, detail=payload.model_dump())
        db.commit()
        return {'ok': True}

    @app.get('/api/studio/payments/settings')
    def payment_settings(session: UserSession = Depends(m.session_dependency), db: Session = Depends(get_db)):
        tenant = member(db, session, owner=True)
        conn = db.get(StripeConnection, tenant.id)
        sub = m.ensure_subscription(db, tenant)
        return {'configured': ready(), 'test_mode': not settings.stripe_live_mode,
            'connect_available': ready() and bool(settings.stripe_connect_client_id),
            'subscription_pending': bool(db.scalar(select(StripeCheckout.id).where(StripeCheckout.tenant_id == tenant.id, StripeCheckout.kind == 'subscription', StripeCheckout.status == 'pending'))),
            'connected': bool(conn), 'enabled': bool(conn and conn.enabled),
            'charges_enabled': bool(conn and conn.charges_enabled),
            'subscription': m.subscription_json(sub, tenant), 'has_stripe_subscription': bool(sub.provider_subscription_id)}

    @app.post('/api/studio/payments/enabled')
    def enable(payload: EnabledIn, session: UserSession = Depends(m.require_csrf), db: Session = Depends(get_db)):
        tenant = member(db, session, owner=True); conn = db.get(StripeConnection, tenant.id)
        if not conn:
            raise HTTPException(409, 'Connect Stripe first')
        if payload.enabled:
            obj = stripe_call('GET', '/v1/accounts/'+conn.account_id)
            conn.charges_enabled = bool(obj.get('charges_enabled'))
            if not conn.charges_enabled or conn.livemode != settings.stripe_live_mode:
                raise HTTPException(409, 'Finish Stripe setup before enabling card payments')
        conn.enabled = payload.enabled
        m.audit(db, 'card_payments_changed', 'tenant', tenant.id, actor=session.user, tenant_id=tenant.id, detail=payload.model_dump())
        db.commit(); return {'ok': True}

    @app.post('/api/studio/payments/connect')
    def connect(session: UserSession = Depends(m.require_csrf), db: Session = Depends(get_db)):
        tenant = member(db, session, owner=True)
        if not ready() or not settings.stripe_connect_client_id:
            raise HTTPException(503, 'Ivory Digital has not configured Stripe Connect yet')
        # Reauthorisation may reconnect the same account; callback forbids changing the recipient.
        state = secrets.token_urlsafe(40)
        db.add(StripeOAuthState(id=token_hash(state), tenant_id=tenant.id, user_id=session.user_id,
                              expires_at=utcnow()+timedelta(minutes=10)))
        db.commit()
        return {'url': 'https://connect.stripe.com/oauth/authorize?'+urlencode({
            'response_type': 'code', 'client_id': settings.stripe_connect_client_id,
            'scope': 'read_write', 'state': state,
            'redirect_uri': settings.studio_url.rstrip('/')+'/api/stripe/connect/callback'})}

    @app.get('/api/stripe/connect/callback')
    def callback_handoff(state: str = '', code: str = ''):
        # A completed same-origin document allows Strict cookies on the next request.
        # Do not exchange the code until the initiating owner is authenticated again.
        target = '/api/stripe/connect/finish?'+urlencode({'state':state, 'code':code})
        import html
        return HTMLResponse('<!doctype html><title>Complete Stripe connection</title><p>Returning to your secure Studio…</p>'
            +'<a href="'+html.escape(target, quote=True)+'">Continue</a><script src="/api/stripe/connect/return.js"></script>')

    @app.get('/api/stripe/connect/return.js')
    def callback_script():
        return Response("location.replace('/api/stripe/connect/finish'+location.search);", media_type='application/javascript')

    @app.get('/api/stripe/connect/finish')
    def callback(state: str = '', code: str = '', session: UserSession = Depends(m.session_dependency), db: Session = Depends(get_db)):
        set_database_tenant(db, platform_admin=True)
        row = db.scalar(select(StripeOAuthState).where(StripeOAuthState.id == token_hash(state)).with_for_update())
        if not row or row.used_at or aware(row.expires_at) < utcnow() or not code or row.user_id != session.user_id:
            raise HTTPException(400, 'This Stripe connection link has expired. Return to Studio and try again.')
        user = db.get(User, row.user_id)
        owner = db.scalar(select(Membership).where(Membership.user_id == row.user_id,
                         Membership.tenant_id == row.tenant_id, Membership.role == MembershipRole.OWNER))
        if not user or not user.is_active or not owner:
            raise HTTPException(403, 'This connection is no longer authorised')
        tenant = db.get(Tenant, row.tenant_id)
        if not tenant or tenant.status == TenantStatus.CANCELLED:
            raise HTTPException(403, 'Business is no longer available')
        if not ready():
            raise HTTPException(503, 'Stripe is not configured')
        # OAuth codes are non-idempotent: never retry a code after a timeout.
        row.used_at = utcnow(); db.commit(); set_database_tenant(db, platform_admin=True)
        try:
            response = httpx.post('https://connect.stripe.com/oauth/token', data={
                'grant_type': 'authorization_code', 'code': code, 'client_secret': settings.stripe_secret_key}, timeout=25)
            response.raise_for_status(); obj = response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, 'Stripe could not complete connection. Return to Studio and start again.') from None
        if obj.get('livemode') != settings.stripe_live_mode or not obj.get('stripe_user_id','').startswith('acct_'):
            raise HTTPException(400, 'Connect a Stripe account in the correct test or live mode')
        account_id = obj['stripe_user_id']
        existing = db.get(StripeConnection, row.tenant_id)
        assigned = db.scalar(select(StripeConnection).where(StripeConnection.account_id == account_id))
        if (assigned and assigned.tenant_id != row.tenant_id) or (existing and existing.account_id != account_id):
            raise HTTPException(409, 'Use this business’s original Stripe account. A different recipient cannot replace it here.')
        details = stripe_call('GET', '/v1/accounts/'+account_id)
        if existing:
            existing.charges_enabled = bool(details.get('charges_enabled')); existing.livemode = settings.stripe_live_mode
        else:
            db.add(StripeConnection(tenant_id=row.tenant_id, account_id=account_id, enabled=False,
                charges_enabled=bool(details.get('charges_enabled')), livemode=settings.stripe_live_mode))
        row.used_at = utcnow()
        m.audit(db, 'stripe_connected', 'tenant', row.tenant_id, actor=user, tenant_id=row.tenant_id)
        db.commit()
        return RedirectResponse(settings.studio_url.rstrip('/')+'/?stripe=connected', status_code=303)

    @app.post('/api/studio/payments/subscription-checkout')
    def subscribe(session: UserSession = Depends(m.require_csrf), db: Session = Depends(get_db)):
        tenant = member(db, session, owner=True)
        sub = db.scalar(select(TenantSubscription).where(TenantSubscription.tenant_id == tenant.id).with_for_update())
        if not sub or sub.price_pence <= 0 or sub.provider == 'complimentary':
            raise HTTPException(409, 'No paid subscription is required for this business')
        if sub.provider_subscription_id:
            pending = db.scalar(select(StripeCheckout).where(StripeCheckout.tenant_id == tenant.id, StripeCheckout.kind == 'subscription', StripeCheckout.status == 'pending').with_for_update())
            if pending and pending.session_id:
                current_checkout = stripe_call('GET', '/v1/checkout/sessions/'+pending.session_id)
                if current_checkout.get('status') == 'open':
                    return {'url': pending.url}
            previous = stripe_call('GET', '/v1/subscriptions/'+sub.provider_subscription_id)
            if previous.get('status') not in {'canceled','incomplete_expired'}:
                raise HTTPException(409, 'A Stripe subscription already exists. Use Manage subscription.')
            old = db.scalar(select(StripeCheckout).where(StripeCheckout.subscription_id == sub.provider_subscription_id))
            if old: old.status = 'closed'
            sub.provider_subscription_id = ''
        if sub.price_pence < 30:
            raise HTTPException(422, 'The subscription amount must be at least £0.30 for card checkout')
        if not ready():
            raise HTTPException(503, 'Stripe is not configured yet. Arrange bank transfer with Ivory Digital.')
        row = get_pending(db, tenant.id, 'subscription')
        if not row:
            if sub.billing_cycle not in {'monthly', 'yearly'}:
                raise HTTPException(409, 'Ask Ivory Digital to set a monthly or yearly plan')
            row = StripeCheckout(id=uid(), tenant_id=tenant.id, kind='subscription', amount_pence=sub.price_pence,
                                 expires_at=utcnow()+timedelta(minutes=35))
            data = {'mode': 'subscription', 'payment_method_types[0]': 'card',
                'success_url': settings.studio_url.rstrip('/')+'/?stripe=returned',
                'cancel_url': settings.studio_url.rstrip('/')+'/?stripe=cancelled',
                'client_reference_id': row.id, 'metadata[attempt_id]': row.id,
                'subscription_data[metadata][tenant_id]': tenant.id,
                'subscription_data[metadata][attempt_id]': row.id,
                'line_items[0][price_data][currency]': 'gbp',
                'line_items[0][price_data][unit_amount]': str(sub.price_pence),
                'line_items[0][price_data][recurring][interval]': 'month' if sub.billing_cycle == 'monthly' else 'year',
                'line_items[0][price_data][product_data][name]': sub.plan_name+' — '+tenant.display_name,
                'line_items[0][quantity]': '1', 'expires_at': str(int(row.expires_at.timestamp()))}
            if sub.provider_customer_id:
                data['customer'] = sub.provider_customer_id
            else:
                data['customer_email'] = tenant.owner_email
            row.request_data = data; db.add(row)
        db.commit(); set_database_tenant(db, tenant.id)
        return checkout_url(db, row)

    @app.post('/api/studio/payments/subscription-portal')
    def billing_portal(session: UserSession = Depends(m.require_csrf), db: Session = Depends(get_db)):
        tenant = member(db, session, owner=True); sub = db.get(TenantSubscription, tenant.id)
        if not sub or not sub.provider_customer_id:
            raise HTTPException(409, 'No Stripe billing account exists yet')
        obj = stripe_call('POST', '/v1/billing_portal/sessions', {
            'customer': sub.provider_customer_id, 'return_url': settings.studio_url.rstrip('/')+'/?stripe=returned'})
        return {'url': obj['url']}

    @app.post('/api/public/portal/{raw_token}/invoices/{invoice_id}/card-checkout')
    def couple_checkout(raw_token: str, invoice_id: str, payload: CardChoiceIn, db: Session = Depends(get_db)):
        tenant, booking, journey = m.public_portal(raw_token, db)
        if booking.status in {'cancelled', 'archived'} or not card_available(db, tenant.id):
            raise HTTPException(409, 'Card payments are unavailable. Please use the bank transfer details or contact your photographer.')
        inv = db.scalar(select(BookingInvoice).where(BookingInvoice.id == invoice_id,
            BookingInvoice.tenant_id == tenant.id, BookingInvoice.booking_id == booking.id).with_for_update())
        if not inv or inv.status == 'void':
            raise HTTPException(404, 'Invoice not available')
        outstanding = max(0, inv.total_pence-inv.paid_pence)
        amount = outstanding if payload.portion == 'balance' else max(0, min(journey.booking_fee_pence, inv.total_pence)-inv.paid_pence)
        if amount < 30:
            raise HTTPException(409, 'There is no card-payable amount remaining for this choice. Please check your invoice.')
        row = get_pending(db, tenant.id, 'booking', inv.id)
        if row and row.amount_pence != amount:
            expire_invoice_checkout(db, inv); row = None
        if not row:
            conn = db.get(StripeConnection, tenant.id)
            row = StripeCheckout(id=uid(), tenant_id=tenant.id, invoice_id=inv.id, kind='booking',
                account_id=conn.account_id, amount_pence=amount, expires_at=utcnow()+timedelta(minutes=35))
            # Do not send the private portal bearer token to Stripe.
            return_url = settings.client_url.rstrip('/')+'/payment-return'
            row.request_data = {'mode': 'payment', 'payment_method_types[0]': 'card',
                'success_url': return_url+'?result=received', 'cancel_url': return_url+'?result=cancelled',
                'client_reference_id': row.id, 'metadata[attempt_id]': row.id,
                'payment_intent_data[metadata][attempt_id]': row.id,
                'line_items[0][price_data][currency]': 'gbp', 'line_items[0][price_data][unit_amount]': str(amount),
                'line_items[0][price_data][product_data][name]': tenant.display_name+' — invoice '+inv.number,
                'line_items[0][quantity]': '1', 'expires_at': str(int(row.expires_at.timestamp()))}
            db.add(row)
        db.commit(); set_database_tenant(db, tenant.id)
        return checkout_url(db, row)

    @app.post('/api/stripe/webhook/{channel}')
    async def webhook(channel: str, request: Request, db: Session = Depends(get_db)):
        if channel not in {'platform', 'connect'} or not ready():
            raise HTTPException(503, 'Stripe webhook is not configured')
        body = await request.body()
        if len(body) > 1000000:
            raise HTTPException(413, 'Payload too large')
        secret = settings.stripe_webhook_secret if channel == 'platform' else settings.stripe_connect_webhook_secret
        event = verify_event(body, request.headers.get('stripe-signature', ''), secret)
        if bool(event.get('livemode')) != settings.stripe_live_mode:
            raise HTTPException(400, 'Mode mismatch')
        if bool(event.get('account')) != (channel == 'connect'):
            raise HTTPException(400, 'Webhook account scope mismatch')
        set_database_tenant(db, platform_admin=True)
        if db.get(StripeEvent, event['id']):
            return {'received': True}
        try:
            db.add(StripeEvent(id=event['id'])); db.flush()
        except IntegrityError:
            db.rollback(); return {'received': True}
        await run_in_threadpool(process_event, m, db, event)
        db.commit()
        return {'received': True}


def verify_event(body, signature, secret):
    try:
        parts = [p.split('=', 1) for p in signature.split(',')]
        stamp = next(v for k,v in parts if k == 't')
        if abs(time.time()-int(stamp)) > 300:
            raise ValueError()
        expected = hmac.new(secret.encode(), stamp.encode()+b'.'+body, hashlib.sha256).hexdigest()
        if not any(k == 'v1' and hmac.compare_digest(v, expected) for k,v in parts):
            raise ValueError()
        event = json.loads(body)
        if not isinstance(event, dict) or not event.get('id') or not isinstance(event.get('data',{}).get('object'), dict):
            raise ValueError()
        return event
    except (ValueError, StopIteration, TypeError):
        raise HTTPException(400, 'Invalid Stripe signature or payload') from None


def payment_received(m, db, row, obj):
    if row.status == 'paid':
        return
    if obj.get('payment_status') != 'paid':
        return
    if obj.get('amount_total') != row.amount_pence or obj.get('currency') != 'gbp' or not obj.get('payment_intent'):
        raise HTTPException(400, 'Payment does not match the saved checkout')
    inv = db.scalar(select(BookingInvoice).where(BookingInvoice.id == row.invoice_id,
                    BookingInvoice.tenant_id == row.tenant_id).with_for_update())
    if not inv:
        raise HTTPException(409, 'Invoice unavailable for reconciliation')
    row.payment_intent_id = obj['payment_intent']; row.status = 'paid'
    db.add(BookingPayment(tenant_id=row.tenant_id, invoice_id=inv.id, amount_pence=row.amount_pence,
        paid_date=date.today(), payment_type='stripe', reference=row.payment_intent_id, notes='Verified Stripe card payment'))
    prior_paid = inv.paid_pence; inv.paid_pence += row.amount_pence
    booking = db.get(Booking, inv.booking_id); journey = db.get(BookingJourney, inv.booking_id)
    tenant = db.get(Tenant, row.tenant_id)
    if inv.status != 'void':
        inv.status = 'paid' if inv.paid_pence >= inv.total_pence else 'part_paid'
        if booking.status not in {'cancelled', 'archived'}:
            fee = min(journey.booking_fee_pence, inv.total_pence)
            if inv.paid_pence >= fee and booking.status != 'confirmed':
                booking.status = 'confirmed'
                journey.calendar_state = {**(journey.calendar_state or {}), 'status':'pending', 'desired_action':'create_or_update', 'reason':'booking_secured'}
                m.trigger_workflow(db, tenant, booking, 'booking_fee_paid')
            if prior_paid < inv.total_pence <= inv.paid_pence:
                m.trigger_workflow(db, tenant, booking, 'balance_paid')
    if inv.paid_pence > inv.total_pence or inv.status == 'void' or booking.status in {'cancelled','archived'}:
        db.add(StudioNotification(tenant_id=row.tenant_id, booking_id=booking.id, kind='payment_review', title='Card payment needs review', body='A payment arrived for an inactive invoice/booking or exceeded the balance. Check the invoice and Stripe before refunding.'))
        m.audit(db, 'stripe_payment_needs_review', 'invoice', inv.id, tenant_id=row.tenant_id,
                detail={'amount_pence': row.amount_pence, 'reason': 'Overpayment or inactive booking/invoice'})
    db.flush()
    if booking.status not in {'cancelled', 'archived'} and inv.status != 'void':
        m.sync_booking_calendar_safely(db, tenant, booking, journey)
    m.audit(db, 'stripe_payment_received', 'invoice', inv.id, tenant_id=row.tenant_id, detail={'amount_pence':row.amount_pence})


def process_event(m, db, event):
    kind = event.get('type',''); obj = event['data']['object']; account = event.get('account','')
    if kind == 'account.updated' and account:
        conn = db.scalar(select(StripeConnection).where(StripeConnection.account_id == account))
        if conn:
            current = stripe_call('GET', '/v1/accounts/'+account)
            conn.charges_enabled = bool(current.get('charges_enabled'))
        return
    if kind == 'account.application.deauthorized' and account:
        conn = db.scalar(select(StripeConnection).where(StripeConnection.account_id == account))
        if conn:
            conn.enabled = False; conn.charges_enabled = False
        return
    if kind.startswith('checkout.session.'):
        attempt = (obj.get('metadata') or {}).get('attempt_id')
        row = db.scalar(select(StripeCheckout).where(StripeCheckout.id == attempt).with_for_update()) if attempt else None
        if not row or row.status == 'closed':
            return
        if row.account_id != account or (row.session_id and row.session_id != obj['id']):
            raise HTTPException(400, 'Checkout scope mismatch')
        current = stripe_call('GET', '/v1/checkout/sessions/'+obj['id'], account=account)
        if (current.get('metadata') or {}).get('attempt_id') != row.id:
            raise HTTPException(400, 'Checkout metadata mismatch')
        row.session_id = obj['id']
        if current.get('status') == 'expired' and row.status != 'paid':
            row.status = 'expired'
        elif row.kind == 'booking':
            payment_received(m, db, row, current)
        elif current.get('status') == 'complete' and current.get('subscription'):
            sub = db.scalar(select(TenantSubscription).where(TenantSubscription.tenant_id == row.tenant_id).with_for_update())
            row.subscription_id = current['subscription']; row.status = 'complete'
            sub.provider = 'stripe'; sub.provider_customer_id = current['customer']; sub.provider_subscription_id = current['subscription']
            sync_subscription(m, db, sub, row)
        return
    if kind.startswith('charge.dispute.') and account:
        row = db.scalar(select(StripeCheckout).where(StripeCheckout.account_id == account,
            StripeCheckout.payment_intent_id == obj.get('payment_intent')))
        if row:
            inv = db.get(BookingInvoice, row.invoice_id)
            db.add(StudioNotification(tenant_id=row.tenant_id, booking_id=inv.booking_id, kind='payment_review',
                title='Stripe payment dispute', body='A card payment dispute changed. Open Stripe to review the case and its deadline.'))
            m.audit(db, 'stripe_dispute_updated', 'invoice', row.invoice_id, tenant_id=row.tenant_id,
                detail={'dispute_id':obj['id'], 'status':obj.get('status')})
        return
    if kind == 'charge.refunded' and account:
        row = db.scalar(select(StripeCheckout).where(StripeCheckout.payment_intent_id == obj.get('payment_intent'),
                  StripeCheckout.account_id == account).with_for_update())
        if not row:
            # Refund may arrive before the payment event. Retry rather than lose it.
            pending = db.scalar(select(StripeCheckout).where(StripeCheckout.account_id == account,
                StripeCheckout.id == (obj.get('metadata') or {}).get('attempt_id')))
            if pending:
                raise HTTPException(409, 'Awaiting original payment event')
            return
        charge = stripe_call('GET', '/v1/charges/'+obj['id'], account=account)
        cumulative = int(charge.get('amount_refunded',0)); delta = cumulative-row.refunded_pence
        if delta > 0:
            if cumulative > row.amount_pence:
                raise HTTPException(400, 'Refund exceeds payment')
            inv = db.scalar(select(BookingInvoice).where(BookingInvoice.id == row.invoice_id).with_for_update())
            row.refunded_pence = cumulative; inv.paid_pence -= delta
            if inv.status != 'void':
                inv.status = 'paid' if inv.paid_pence >= inv.total_pence else 'part_paid' if inv.paid_pence > 0 else 'unpaid'
            db.add(BookingPayment(tenant_id=row.tenant_id, invoice_id=inv.id, amount_pence=-delta,
                paid_date=date.today(), payment_type='stripe_refund', reference=obj['id'], notes='Verified Stripe refund'))
            db.add(StudioNotification(tenant_id=row.tenant_id, booking_id=inv.booking_id, kind='payment_review', title='Stripe refund recorded', body='Check the remaining balance and any planned reminders. The booking has not been automatically cancelled.'))
            m.audit(db, 'stripe_refund_received', 'invoice', inv.id, tenant_id=row.tenant_id, detail={'amount_pence':delta})
        return
    if kind.startswith('customer.subscription.') or kind in {'invoice.paid','invoice.payment_failed'}:
        if account:
            return
        subscription_id = obj['id'] if kind.startswith('customer.subscription.') else (obj.get('subscription') or
            (obj.get('parent',{}).get('subscription_details') or {}).get('subscription'))
        if not subscription_id:
            return
        sub = db.scalar(select(TenantSubscription).where(TenantSubscription.provider_subscription_id == subscription_id).with_for_update())
        if not sub:
            current = stripe_call('GET', '/v1/subscriptions/'+subscription_id)
            attempt_id = (current.get('metadata') or {}).get('attempt_id')
            row = db.scalar(select(StripeCheckout).where(StripeCheckout.id == attempt_id).with_for_update()) if attempt_id else None
            if not row or row.kind != 'subscription' or row.account_id or row.status == 'closed':
                return
            sub = db.scalar(select(TenantSubscription).where(TenantSubscription.tenant_id == row.tenant_id).with_for_update())
            if sub.provider_subscription_id and sub.provider_subscription_id != subscription_id:
                raise HTTPException(409, 'Subscription already assigned')
            row.subscription_id = subscription_id
            sub.provider_subscription_id = subscription_id; sub.provider_customer_id = current['customer']; sub.provider = 'stripe'
        row = db.scalar(select(StripeCheckout).where(StripeCheckout.subscription_id == subscription_id))
        if kind == 'invoice.paid':
            invoice = stripe_call('GET', '/v1/invoices/'+obj['id'])
            record_subscription_invoice(db, sub, invoice)
        sync_subscription(m, db, sub, row)


def sync_subscription(m, db, sub, attempt):
    current = stripe_call('GET', '/v1/subscriptions/'+sub.provider_subscription_id)
    tenant = db.get(Tenant, sub.tenant_id)
    status = current.get('status'); sub.billing_status = status or 'unknown'
    latest = current.get('latest_invoice')
    inv = stripe_call('GET', '/v1/invoices/'+latest) if isinstance(latest,str) else latest
    paid = inv and inv.get('status') == 'paid'
    if paid:
        record_subscription_invoice(db, sub, inv)
    end = current.get('current_period_end')
    if not end:
        end = max([i.get('current_period_end',0) for i in current.get('items',{}).get('data',[])] or [0])
    if end:
        sub.next_payment_due = datetime.fromtimestamp(end, timezone.utc).date()
    # Mark-paid browser redirects never activate access. Only verified Stripe state can.
    if status in {'active', 'trialing'} and paid and tenant.status != TenantStatus.CANCELLED and not (tenant.onboarding or {}).get('admin_access_hold'):
        tenant.status = TenantStatus.ACTIVE; sub.suspension_reason = ''; sub.suspended_at = None
    elif status in {'canceled','unpaid','incomplete_expired','paused'}:
        tenant.status = TenantStatus.SUSPENDED if tenant.status != TenantStatus.CANCELLED else tenant.status
        m.pause_open_workflow_actions(db, tenant.id)
        tenant.automations_paused = True; sub.suspension_reason = 'Stripe subscription '+status; sub.suspended_at = utcnow()
    elif status == 'past_due' and inv:
        # Grace starts at the failed invoice, not the next subscription period.
        sub.next_payment_due = datetime.fromtimestamp(inv.get('created',time.time()),timezone.utc).date()


def record_subscription_invoice(db, sub, inv):
    if inv.get('status') != 'paid' or db.get(StripeBillingReceipt, inv['id']):
        return
    if inv.get('currency') != 'gbp' or inv.get('customer') != sub.provider_customer_id:
        raise HTTPException(400, 'Subscription invoice scope mismatch')
    db.add(StripeBillingReceipt(id=inv['id'], tenant_id=sub.tenant_id))
    db.add(PlatformBillingPayment(tenant_id=sub.tenant_id, amount_pence=inv.get('amount_paid',0),
        paid_date=datetime.fromtimestamp((inv.get('status_transitions') or {}).get('paid_at') or time.time(), timezone.utc).date(),
        payment_method='stripe', reference=inv['id'], notes='Verified Stripe subscription invoice'))
    sub.last_payment_at = utcnow()
    db.flush()


def release_cancelled_subscription(db, sub):
    if not sub.provider_subscription_id:
        return
    obj = stripe_call('GET', '/v1/subscriptions/'+sub.provider_subscription_id)
    if obj.get('status') not in {'canceled', 'incomplete_expired'}:
        raise HTTPException(409, 'Cancel the existing Stripe subscription before changing its price here.')
    attempt = db.scalar(select(StripeCheckout).where(StripeCheckout.subscription_id == sub.provider_subscription_id))
    if attempt:
        attempt.status = 'closed'
    sub.provider_subscription_id = ''
    sub.provider = 'manual'
