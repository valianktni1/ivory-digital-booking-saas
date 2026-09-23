"""Real tenant sessions: immutable payment snapshots, preview privacy and migration."""
from copy import deepcopy
import io
import pytest
from sqlalchemy import select, create_engine, text, inspect
from sqlalchemy.orm import Session
from test_phase57_experience import studio_case, csrf, accept
from app import main
from app.database import SessionLocal
from app.models import Tenant, BookingJourney, BookingInvoice, ServicePackage, Booking
from app.banking import ensure_bank_columns, freeze_tenant_payments

BANK={'id':'account2','label':'New bank PRIVATE LABEL','bank_account_name':'New Studio Account','bank_sort_code':'12-34-56','bank_account_number':'12345678'}

def save_banks(c,bank=BANK):
    r=c.studio.put('/api/studio/bank-accounts',headers=csrf(c.studio),json={'accounts':[bank]})
    assert r.status_code==200,r.text
    return r

def package(c):
    r=c.studio.post('/api/studio/packages',headers=csrf(c.studio),json={'name':'Full story','price_pence':100000,'booking_fee_pence':20000})
    assert r.status_code==201,r.text
    return r.json()['id']

def draft(c,pid,bank='account2'):
    r=c.studio.put(f'/api/studio/bookings/{c.ids.booking}/quote',headers=csrf(c.studio),json={'package_ids':[pid],'bank_account_id':bank})
    assert r.status_code==200,r.text
    return r.json()

def test_quote_invoice_and_portal_keep_bank_snapshot(studio_case):
    c=studio_case;save_banks(c);pid=package(c);q=draft(c,pid)
    assert q['quote']['payment_details']['bank_account_number']=='12345678'
    save_banks(c,{**BANK,'label':'Changed bank','bank_account_number':'87654321'})
    # Ordinary draft edits retain saved bank; changing business settings cannot redirect it.
    q=draft(c,pid);assert q['quote']['payment_details']['bank_account_number']=='12345678'
    with SessionLocal() as db:
        j=db.get(BookingJourney,c.ids.booking);j.quote_state={**j.quote_state,'status':'sent'};db.commit()
    r=c.public.post(c.path+'/quote/accept',json={'package_id':pid,'client_name':'Avery'})
    assert r.status_code==200,r.text
    invoice_id=r.json()['invoice']['id']
    # Even legacy settings are allowed to change without rewriting this invoice.
    r=c.studio.patch('/api/studio/branding',headers=csrf(c.studio),json={'display_name':'Changed studio','bank_account_name':'Different','bank_sort_code':'99-99-99','bank_account_number':'99999999'})
    assert r.status_code==200
    portal=c.public.get(c.path).json()
    assert portal['payment_instructions']['bank_account_number']=='12345678'
    assert portal['invoices'][0]['payment_instructions']['bank_account_number']=='12345678'
    assert 'New bank PRIVATE LABEL' not in str(portal)
    assert 'bank_accounts' not in str(portal)
    pdf=c.public.get(c.path+f'/invoices/{invoice_id}/pdf')
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
    from pypdf import PdfReader
    wording='\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert '12345678' in wording and '99999999' not in wording and '87654321' not in wording

def test_legacy_invoice_is_frozen_before_settings_change(studio_case):
    c=studio_case;accepted=accept(c)
    with SessionLocal() as db:
        inv=db.get(BookingInvoice,accepted['invoice']['id']);inv.payment_details=None
        j=db.get(BookingJourney,c.ids.booking)
        for key in ['quote_state','accepted_quote']:
            state=dict(getattr(j,key));state.pop('payment_details',None);setattr(j,key,state)
        db.commit()
    save_banks(c)
    c.studio.patch('/api/studio/branding',headers=csrf(c.studio),json={'display_name':'Changed studio','bank_account_name':'Wrong new bank'})
    assert c.public.get(c.path).json()['payment_instructions']['bank_account_name']=='Studio account'
    with SessionLocal() as db:
        inv=db.get(BookingInvoice,accepted['invoice']['id']);assert inv.payment_details['bank_account_name']=='Studio account'
        freeze_tenant_payments(db,db.get(Tenant,c.ids.tenant));db.commit()
        assert inv.payment_details['bank_account_name']=='Studio account'

def test_preview_has_no_view_event_no_private_data_and_requires_session(studio_case):
    c=studio_case;save_banks(c);pid=package(c);draft(c,pid)
    url=f'/api/studio/bookings/{c.ids.booking}/couple-preview-data'
    with SessionLocal() as db:before=deepcopy(db.get(BookingJourney,c.ids.booking).quote_state)
    r=c.studio.get(url);assert r.status_code==200,r.text
    data=r.json();assert data['quote']['status']=='sent';assert data['preview']
    assert not data['available_questionnaires']
    for secret in ['PRIVATE NOTE','PRIVATE HOLIDAY','PRIVATE LABEL','private_date_conflicts','quote_template_name']:
        assert secret not in r.text
    with SessionLocal() as db:assert db.get(BookingJourney,c.ids.booking).quote_state==before
    assert c.public.get(url).status_code==401
    assert c.studio.post(url,json={}).status_code==405
    page=c.studio.get(url.replace('-data',''));assert page.status_code==200
    assert '/api/studio/preview-assets/app.js' in page.text
    assert c.studio.get('/api/studio/preview-assets/app.js').status_code==200
    assert c.public.get('/api/studio/preview-assets/app.js').status_code==401
    with SessionLocal() as db:other=db.scalar(select(Booking).where(Booking.tenant_id==c.ids.other)).id
    assert c.studio.get(f'/api/studio/bookings/{other}/couple-preview-data').status_code==404

def test_bank_choices_are_tenant_scoped_and_template_applies_choice(studio_case):
    c=studio_case;pid=package(c)
    # Account 2 exists in a different tenant only; cannot select it here.
    with SessionLocal() as db:
        other=db.get(Tenant,c.ids.other);other.branding={'bank_accounts':[BANK]};db.commit()
    payload={'name':'Internal template','package_ids':[pid],'bank_account_id':'account2'}
    assert c.studio.post('/api/studio/quote-templates',headers=csrf(c.studio),json=payload).status_code==422
    assert not c.studio.get('/api/studio/bank-accounts').json()['accounts'][0].get('id')=='account2'
    save_banks(c)
    r=c.studio.post('/api/studio/quote-templates',headers=csrf(c.studio),json=payload);assert r.status_code==201,r.text
    tid=r.json()['id'];assert r.json()['bank_account_id']=='account2'
    r=c.studio.post(f'/api/studio/bookings/{c.ids.booking}/quote/from-template/{tid}',headers=csrf(c.studio))
    assert r.status_code==200,r.text
    assert r.json()['quote']['payment_details']['bank_account_number']=='12345678'
    assert 'Internal template' not in c.studio.get(f'/api/studio/bookings/{c.ids.booking}/couple-preview-data').text
    # Disabling a bank does not prevent edits to a draft with already saved details.
    c.studio.put('/api/studio/bank-accounts',headers=csrf(c.studio),json={'accounts':[]})
    assert draft(c,pid)['quote']['payment_details']['bank_account_number']=='12345678'

def test_bank_validation_and_manager_cannot_edit_tenant_banks(studio_case):
    c=studio_case
    for rows in [[BANK,BANK],[{**BANK,'bank_account_number':'abc'}],[{**BANK,'label':' '}]]:
        r=c.studio.put('/api/studio/bank-accounts',headers=csrf(c.studio),json={'accounts':rows});assert r.status_code==422
    assert c.public.put('/api/studio/bank-accounts',json={'accounts':[]}).status_code==401
    assert c.manager.put('/api/studio/bank-accounts',headers=csrf(c.manager),json={'accounts':[]}).status_code==403

def test_additive_bank_migration_is_repeatable():
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as con:
        con.execute(text('CREATE TABLE quote_templates (id VARCHAR PRIMARY KEY)'))
        con.execute(text('CREATE TABLE booking_invoices (id VARCHAR PRIMARY KEY)'))
        con.execute(text("INSERT INTO booking_invoices VALUES ('existing')"))
    with Session(engine) as db:
        ensure_bank_columns(db);ensure_bank_columns(db)
        assert db.execute(text('SELECT payment_details FROM booking_invoices WHERE id=\'existing\'')).one()[0] is None
        assert 'bank_account_id' in {c['name'] for c in inspect(engine).get_columns('quote_templates')}
