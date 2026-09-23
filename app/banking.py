"""Tenant-scoped bank choices; historical payment instructions are immutable snapshots."""
from fastapi import HTTPException
from sqlalchemy import inspect, select, text
from .models import Tenant, BookingInvoice, BookingJourney

PUBLIC_KEYS = ('bank_account_name', 'bank_sort_code', 'bank_account_number', 'invoice_payment_note')

def public_payment_details(details):
    return {key: (details or {}).get(key, '') for key in PUBLIC_KEYS}

def legacy_payment_details(tenant):
    return {**public_payment_details(tenant.branding), 'id': None, 'label': 'Original bank transfer details'}

def bank_choices(tenant):
    brand = tenant.branding or {}
    if 'bank_accounts' in brand:
        return brand['bank_accounts']
    old = legacy_payment_details(tenant)
    return [{**old, 'id': 'account1', 'label': 'Current bank'}] if any(old[k] for k in PUBLIC_KEYS[:3]) else []

def selected_payment_details(tenant, account_id):
    if account_id is None:
        return legacy_payment_details(tenant)
    row = next((r for r in bank_choices(tenant) if r['id'] == account_id), None)
    if not row or not all(row.get(k) for k in PUBLIC_KEYS[:3]):
        raise HTTPException(422, 'Set up complete details for the selected bank account in Business settings first.')
    return {**row, 'invoice_payment_note': (tenant.branding or {}).get('invoice_payment_note', '')}

def freeze_tenant_payments(db, tenant):
    """Capture pre-upgrade/current legacy values once, including intentionally empty values."""
    old = legacy_payment_details(tenant)
    for j in db.scalars(select(BookingJourney).where(BookingJourney.tenant_id == tenant.id)).all():
        for field in ('quote_state', 'accepted_quote'):
            state = getattr(j, field) or {}
            if state and 'payment_details' not in state:
                setattr(j, field, {**state, 'payment_details': old})
    for inv in db.scalars(select(BookingInvoice).where(BookingInvoice.tenant_id == tenant.id)).all():
        if inv.payment_details is None:
            j = db.scalar(select(BookingJourney).where(BookingJourney.tenant_id == tenant.id, BookingJourney.booking_id == inv.booking_id))
            inv.payment_details = (j.accepted_quote or {}).get('payment_details', old) if j else old

def payment_for_booking(db, tenant, journey):
    for state in (journey.accepted_quote, journey.quote_state):
        if state and 'payment_details' in state:
            return state['payment_details']
    inv = db.scalar(select(BookingInvoice).where(BookingInvoice.tenant_id == tenant.id,
        BookingInvoice.booking_id == journey.booking_id, BookingInvoice.status != 'void').order_by(BookingInvoice.created_at.desc()))
    return inv.payment_details if inv and inv.payment_details is not None else legacy_payment_details(tenant)

def ensure_bank_columns(db):
    for table, column, dtype in [('quote_templates', 'bank_account_id', 'VARCHAR(20)'), ('booking_invoices', 'payment_details', 'JSON')]:
        if inspect(db.bind).has_table(table) and column not in {c['name'] for c in inspect(db.bind).get_columns(table)}:
            db.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {dtype}'))
