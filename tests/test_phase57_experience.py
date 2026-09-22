"""Behaviour checks for privacy, recovery, drafts, scheduling and tenant operations."""
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main, worker
from app.database import SessionLocal
from app.models import (Tenant, TenantStatus, User, Membership, MembershipRole, Client,
                        Booking, BookingJourney, BookingNote, BookingInvoice, BookingPayment,
                        TenantDateBlock, QuestionnaireTemplate, StudioNotification,
                        MailboxSetting, WorkflowAction, TenantSubscription, PasswordReset)
from app.security import create_session, encrypt_secret, hash_password, token_hash, utcnow


def csrf(client):
    return {"X-CSRF-Token": client.cookies.get("ivory_booking_csrf")}


@pytest.fixture
def studio_case():
    with TestClient(main.app) as public:
        key = uuid4().hex[:10]
        with SessionLocal() as db:
            tenant = Tenant(slug='studio-'+key, display_name='Studio '+key, owner_email=key+'@example.com',
                trial_ends_at=utcnow()+timedelta(days=30), status=TenantStatus.ACTIVE,
                branding={"booking_fee_due_days": 7, "bank_account_name": "Studio account"})
            other = Tenant(slug='other-'+key, display_name='Other studio', owner_email='other-'+key+'@example.com',
                           trial_ends_at=utcnow()+timedelta(days=30))
            owner = User(email=tenant.owner_email, full_name='Studio Owner', password_hash=hash_password('OriginalPassword!2026'), is_active=True)
            admin = User(email='admin-'+key+'@example.com', full_name='Manager', password_hash=hash_password('AdminPassword!2026'),
                         is_active=True, is_platform_admin=True, totp_enabled=True)
            db.add_all([tenant, other, owner, admin]);db.flush()
            db.add(Membership(tenant_id=tenant.id, user_id=owner.id, role=MembershipRole.OWNER))
            person = Client(tenant_id=tenant.id, first_name='Avery', partner_name='Morgan', email='couple-'+key+'@example.com')
            db.add(person);db.flush()
            booking = Booking(tenant_id=tenant.id, client_id=person.id, title='Avery & Morgan',
                              event_date=date(2028,6,10), status='enquiry', is_provisional=True)
            other_booking = Booking(tenant_id=other.id, client_id=person.id, title='OTHER TENANT SECRET',
                                    event_date=booking.event_date, status='confirmed', is_provisional=False)
            db.add_all([booking, other_booking]);db.flush()
            raw = 'portal-'+uuid4().hex
            journey = BookingJourney(tenant_id=tenant.id, booking_id=booking.id,
                portal_token_hash=token_hash(raw), portal_token_encrypted=encrypt_secret(raw),
                quote_state={"status":"sent","message":"Choose your package", "packages":[
                    {"id":"package-one","name":"Full day","price_pence":69950,"booking_fee_pence":10000,"balance_due_days":30}],
                    "add_ons":[{"id":"extra","name":"Album","price_pence":9950,"selection_mode":"mandatory","eligible_package_ids":[]}],
                    "custom_items":[],"questionnaire_form_types":["booking","final_timings"],"internal_marker":"QUOTE PRIVATE"})
            db.add_all([journey, BookingNote(tenant_id=tenant.id, booking_id=booking.id, body='PRIVATE NOTE SECRET', created_by_user_id=owner.id),
                TenantDateBlock(tenant_id=tenant.id, start_date=booking.event_date, end_date=booking.event_date, label='PRIVATE HOLIDAY SECRET'),
                QuestionnaireTemplate(tenant_id=tenant.id, form_type='booking', name='Booking details', introduction='Your details',
                    questions=[{"id":"details","label":"Tell us more","type":"long_text","required":True}], is_active=True),
                QuestionnaireTemplate(tenant_id=tenant.id, form_type='final_timings', name='Final timings', questions=[], is_active=True)])
            main.ensure_subscription(db,tenant)
            _, owner_token, owner_csrf = create_session(db,owner,None,None)
            _, admin_token, admin_csrf = create_session(db,admin,None,None,assurance='mfa')
            db.commit()
            ids=SimpleNamespace(tenant=tenant.id, other=other.id, owner=owner.id, booking=booking.id, raw=raw,
                                slug=tenant.slug, email=owner.email)
        studio=TestClient(main.app);studio.cookies.set('ivory_booking_session',owner_token);studio.cookies.set('ivory_booking_csrf',owner_csrf)
        manager=TestClient(main.app);manager.cookies.set('ivory_booking_session',admin_token);manager.cookies.set('ivory_booking_csrf',admin_csrf)
        yield SimpleNamespace(public=public,studio=studio,manager=manager,ids=ids,path='/api/public/portal/'+raw)
        studio.close();manager.close()


def accept(case):
    response=case.public.post(case.path+'/quote/accept',json={"package_id":"package-one","add_on_ids":[],"client_name":"Avery"})
    assert response.status_code==200,response.text
    return response.json()


def test_clash_is_private_and_acceptance_still_succeeds(studio_case):
    c=studio_case
    studio=c.studio.get(f'/api/studio/bookings/{c.ids.booking}/journey').json()
    assert studio['private_date_conflicts']==[{"kind":"date_block","id":studio['private_date_conflicts'][0]['id'],"label":"PRIVATE HOLIDAY SECRET"}]
    response=c.public.get(c.path)
    assert response.status_code==200
    for secret in ['PRIVATE NOTE SECRET','PRIVATE HOLIDAY SECRET','OTHER TENANT SECRET','private_date_conflicts','workflow_actions','special_payment_note','internal_marker','viewed_at']:
        assert secret not in response.text
    for day in ['2028-06-10','2028-06-11']:
        data=c.public.get(f'/api/public/business/{c.ids.slug}/availability/{day}').json()
        assert 'available' not in data and 'conflict' not in str(data)
    result=accept(c)
    assert result['invoice']['total_pence']==79900
    assert result['invoice']['booking_fee_due_date']==(date.today()+timedelta(days=7)).isoformat()
    assert 'private' not in str(result).lower()
    with SessionLocal() as db:
        notices=db.scalars(select(StudioNotification).where(StudioNotification.tenant_id==c.ids.tenant,
            StudioNotification.kind=='private_date_conflict')).all()
        assert notices


def test_public_invoice_hides_internal_payment_notes(studio_case):
    c=studio_case;result=accept(c)
    with SessionLocal() as db:
        invoice=db.get(BookingInvoice,result['invoice']['id']);invoice.notes='INVOICE PRIVATE';invoice.void_reason='PRIVATE VOID'
        db.add(BookingPayment(tenant_id=c.ids.tenant, invoice_id=invoice.id,
                             amount_pence=100, paid_date=date.today(), payment_type='bank_transfer',notes='PAYMENT PRIVATE',reference='PRIVATE REFERENCE'))
        db.commit()
    body=c.public.get(c.path).text
    for text in ['INVOICE PRIVATE','PRIVATE VOID','PAYMENT PRIVATE','PRIVATE REFERENCE']:
        assert text not in body


def test_drafts_are_gated_persisted_and_removed_on_submission(studio_case):
    c=studio_case
    assert c.public.put(c.path+'/questionnaires/booking/draft',json={'answers':{'details':'Early'}}).status_code==404
    accept(c)
    assert c.public.put(c.path+'/questionnaires/final_timings/draft',json={'answers':{}}).status_code==404
    response=c.public.put(c.path+'/questionnaires/booking/draft',json={'answers':{'details':'Half finished','unknown':'Discard'}})
    assert response.status_code==200,response.text
    form=c.public.get(c.path).json()['available_questionnaires'][0]
    assert form['draft']['answers']=={'details':'Half finished'} and form['submitted'] is False
    submitted=c.public.post(c.path+'/questionnaires/booking',json={'answers':{'details':'Complete'}})
    assert submitted.status_code==200,submitted.text
    form=c.public.get(c.path).json()['available_questionnaires'][0]
    assert form['draft'] is None and form['submitted'] is True
    assert c.public.put('/api/public/portal/invalid/questionnaires/booking/draft',json={'answers':{}}).status_code==404


def test_owner_recovery_is_one_use_and_revokes_sessions(studio_case):
    c=studio_case
    assert c.studio.post(f'/api/manager/tenants/{c.ids.tenant}/password-reset',headers=csrf(c.studio)).status_code==403
    issue=c.manager.post(f'/api/manager/tenants/{c.ids.tenant}/password-reset',headers=csrf(c.manager))
    assert issue.status_code==200,issue.text
    token=issue.json()['reset_url'].split('reset=')[1]
    payload={'token':token,'password':'ReplacementPassword!2026'}
    assert c.public.post('/api/auth/password-reset/complete',json=payload).status_code==200
    assert c.public.post('/api/auth/password-reset/complete',json=payload).status_code==400
    assert c.studio.get('/api/studio/dashboard').status_code==401
    assert c.public.post('/api/auth/login',json={'email':c.ids.email,'password':payload['password']}).status_code==200


def test_recovery_email_does_not_create_studio_email_history(studio_case,monkeypatch):
    c=studio_case;deliveries=[]
    with SessionLocal() as db:
        db.add(MailboxSetting(tenant_id=c.ids.tenant,email_address='studio@example.com',smtp_verified_at=utcnow(),smtp_password_encrypted=encrypt_secret('test')));db.commit()
    monkeypatch.setattr(main,'send_recovery_email',lambda mailbox,email,url:deliveries.append((email,url)))
    valid=c.public.post('/api/auth/password-reset/request',json={'email':c.ids.email})
    invalid=c.public.post('/api/auth/password-reset/request',json={'email':'missing@example.com'})
    assert valid.json()==invalid.json()
    assert len(deliveries)==1 and deliveries[0][0]==c.ids.email
    assert c.studio.get('/api/studio/emails').json()==[]


def test_suspended_portal_keeps_documents_but_blocks_writes(studio_case):
    c=studio_case;result=accept(c)
    with SessionLocal() as db:
        db.get(Tenant,c.ids.tenant).status=TenantStatus.SUSPENDED;db.commit()
    response=c.public.get(c.path)
    assert response.status_code==200 and response.json()['read_only'] is True
    assert c.public.get(c.path+'/invoices/'+result['invoice']['id']+'/pdf').status_code==200
    assert c.public.post(c.path+'/questionnaires/booking',json={'answers':{'details':'No'}}).status_code==404
    assert c.public.put(c.path+'/questionnaires/booking/draft',json={'answers':{}}).status_code==404


def test_manager_operations_and_studio_subscription_are_isolated(studio_case):
    c=studio_case
    assert c.studio.get('/api/manager/operations').status_code==403
    ops=c.manager.get('/api/manager/operations')
    assert ops.status_code==200,ops.text
    row=next(r for r in ops.json()['businesses'] if r['id']==c.ids.tenant)
    assert row['last_login'] is None and row['document_bytes']==0
    assert 'couple' not in row and 'PRIVATE' not in str(row)
    sub=c.studio.get('/api/studio/subscription')
    assert sub.status_code==200 and sub.json()['subscription']['tenant_id']==c.ids.tenant


def test_worker_skips_blocked_studios_and_non_email_queue_items(studio_case,monkeypatch):
    c=studio_case
    with SessionLocal() as db:
        tenant=db.get(Tenant,c.ids.tenant);tenant.automations_paused=False
        for index in range(30):
            db.add(WorkflowAction(tenant_id=tenant.id,booking_id=c.ids.booking,trigger_key='test',mode='task',status='queued',
                due_at=utcnow()-timedelta(days=2),payload={'action_type':'task'}, step_id=uuid4().hex))
        action=WorkflowAction(tenant_id=tenant.id,booking_id=c.ids.booking,trigger_key='test',mode='auto',status='queued',
            due_at=utcnow()-timedelta(days=1),payload={'action_type':'email'},step_id=uuid4().hex)
        db.add(action);db.commit();target=action.id
    sent=[]
    monkeypatch.setattr(worker,'inside_delivery_window',lambda tenant:True)
    def send(db,action,tenant):
        sent.append(action.id);action.status='sent';action.completed_at=utcnow()
    monkeypatch.setattr(worker,'send_action',send)
    worker.process_workflow_actions()
    assert target in sent
    worker.process_workflow_actions()
    assert sent.count(target)==1


def test_help_refresh_seeds_once_and_preserves_manager_edits(studio_case):
    from app.models import HelpArticle
    from app.help_phase57 import PHASE57_HELP_ARTICLES
    c=studio_case
    with SessionLocal() as db:
        rows=db.scalars(select(HelpArticle).where(HelpArticle.slug.like('phase57-%'))).all()
        assert len(rows)==7
        row=rows[0];row.body='Manager custom wording';identifier=row.id;db.commit()
        main.ensure_help_catalog(db)
        assert db.get(HelpArticle,identifier).body=='Manager custom wording'
        assert len(db.scalars(select(HelpArticle).where(HelpArticle.slug.like('phase57-%'))).all())==7
    response=c.studio.post('/api/studio/help/ask',headers=csrf(c.studio),json={'question':'booking fee due deadline','context':'brand'})
    assert response.status_code==200
    assert response.json()['article']['slug']=='phase57-booking-fee-deadline'
    assert all(item['sort_order']>=0 for item in PHASE57_HELP_ARTICLES)
