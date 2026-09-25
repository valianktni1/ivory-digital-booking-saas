import logging
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from redis import Redis
from sqlalchemy import delete, select

from .config import get_settings
from .database import SessionLocal
from .messaging import send_tenant_email
from .models import (AuditLog, Booking, Client, MailboxSetting, StudioNotification,
                     Tenant, TenantEmailBranding, TenantStatus,
                     TenantSubscription, UserSession, WorkflowAction)
from .tenant_context import set_database_tenant


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def inside_delivery_window(tenant: Tenant) -> bool:
    try:
        local = datetime.now(timezone.utc).astimezone(ZoneInfo(tenant.timezone or "Europe/London"))
    except Exception:
        local = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/London"))
    return 9 <= local.hour < 19


def send_action(db, action: WorkflowAction, tenant: Tenant) -> None:
    if tenant.status not in {TenantStatus.ACTIVE, TenantStatus.TRIAL} or tenant.automations_paused or action.status != "queued" or aware(action.due_at) > datetime.now(timezone.utc):
        return
    payload = dict(action.payload or {})
    if payload.get("action_type") != "email":
        return
    if not inside_delivery_window(tenant):
        return
    mailbox = db.get(MailboxSetting, tenant.id)
    if not mailbox or not mailbox.smtp_verified_at or not mailbox.smtp_password_encrypted:
        raise RuntimeError("The studio SMTP connection has not been verified")
    booking = db.get(Booking, action.booking_id) if action.booking_id else None
    client = db.get(Client, booking.client_id) if booking else None
    recipient = payload.get("recipient_email") or (client.email if client else "")
    if not recipient:
        raise RuntimeError("No couple email address is available")
    message = send_tenant_email(
        db, tenant, mailbox, recipient,
        payload.get("subject", ""), payload.get("message_body", ""),
        booking=booking, client=client, extra=payload,
    )
    action.status = "sent"; action.completed_at = datetime.now(timezone.utc)
    payload["sent_at"] = action.completed_at.isoformat(); payload.pop("last_error", None)
    action.payload = payload
    db.add(AuditLog(tenant_id=tenant.id, action="workflow_email_sent",
                    subject_type="workflow_action", subject_id=action.id,
                    detail={"recipient": recipient, "subject": message.subject,
                            "email_message_id": message.id}))


def process_owner_notifications() -> None:
    with SessionLocal() as scan:
        set_database_tenant(scan, platform_admin=True)
        candidates = list(scan.execute(select(StudioNotification.id, StudioNotification.tenant_id).where(
            StudioNotification.email_status.in_(["queued", "waiting_for_mailbox"]),
            StudioNotification.tenant_id.in_(select(MailboxSetting.tenant_id).where(
                MailboxSetting.smtp_verified_at.is_not(None)))
        ).order_by(StudioNotification.created_at).limit(25)).all())
    for notification_id, tenant_id in candidates:
        with SessionLocal() as db:
            set_database_tenant(db, tenant_id)
            row = db.scalar(select(StudioNotification).where(StudioNotification.id == notification_id,
                StudioNotification.email_status.in_(["queued", "waiting_for_mailbox"])).with_for_update(skip_locked=True))
            tenant = db.get(Tenant, tenant_id)
            branding = db.get(TenantEmailBranding, tenant_id)
            mailbox = db.get(MailboxSetting, tenant_id)
            if not row or not tenant or not branding or not branding.owner_notifications_enabled:
                if row:
                    row.email_status = "not_requested"
                    db.commit()
                continue
            if not mailbox or not mailbox.smtp_verified_at:
                row.email_status = "waiting_for_mailbox"
                db.commit()
                continue
            try:
                send_tenant_email(db, tenant, mailbox, tenant.owner_email,
                                  row.title, row.body or row.title,
                                  booking=db.get(Booking, row.booking_id) if row.booking_id else None)
                row.email_status = "sent"
            except Exception:
                logging.getLogger(__name__).exception("Owner notification delivery failed")
                row.email_status = "failed"
            db.commit()


def process_workflow_actions() -> None:
    with SessionLocal() as scan:
        set_database_tenant(scan, platform_admin=True)
        # Fair per-studio batches: sleeping or paused studios cannot monopolise the queue.
        candidates = []
        tenants = scan.scalars(select(Tenant).where(Tenant.automations_paused.is_(False),
            Tenant.status.in_([TenantStatus.ACTIVE, TenantStatus.TRIAL]))).all()
        for tenant in tenants:
            if not inside_delivery_window(tenant):
                continue
            candidates.extend(scan.execute(select(WorkflowAction.id, WorkflowAction.tenant_id).where(
                WorkflowAction.tenant_id == tenant.id, WorkflowAction.status == "queued",
                WorkflowAction.payload["action_type"].as_string() == "email",
                WorkflowAction.due_at <= datetime.now(timezone.utc)
            ).order_by(WorkflowAction.due_at).limit(25)).all())
    for action_id, tenant_id in candidates:
        with SessionLocal() as db:
            set_database_tenant(db, tenant_id)
            action = db.scalar(select(WorkflowAction).where(
                WorkflowAction.id == action_id,
                WorkflowAction.tenant_id == tenant_id,
                WorkflowAction.status == "queued").with_for_update(skip_locked=True))
            tenant = db.get(Tenant, tenant_id)
            if not action:
                continue
            if not tenant:
                continue
            try:
                send_action(db, action, tenant)
            except Exception as exc:
                details = dict(action.payload or {})
                details["last_error"] = str(exc)[:500]
                details["failed_at"] = datetime.now(timezone.utc).isoformat()
                action.payload = details; action.status = "error"
                db.add(AuditLog(tenant_id=tenant.id, action="workflow_email_failed",
                                subject_type="workflow_action", subject_id=action.id,
                                detail={"error": str(exc)[:500]}))
            db.commit()


def pause_open_actions(db, tenant_id: str) -> None:
    actions = db.scalars(select(WorkflowAction).where(
        WorkflowAction.tenant_id == tenant_id,
        WorkflowAction.completed_at.is_(None),
        WorkflowAction.status.notin_(["sent", "completed", "cancelled"]))).all()
    for action in actions:
        payload = dict(action.payload or {})
        if action.status != "paused":
            payload["resume_status"] = action.status
        action.status = "paused"
        action.payload = payload


def process_billing_statuses(now: datetime | None = None) -> None:
    current = now or datetime.now(timezone.utc)
    today: date = current.date()
    with SessionLocal() as db:
        set_database_tenant(db, platform_admin=True)
        rows = db.execute(select(Tenant, TenantSubscription).join(
            TenantSubscription, TenantSubscription.tenant_id == Tenant.id)).all()
        for tenant, subscription in rows:
            if tenant.status == TenantStatus.CANCELLED:
                continue
            previous = subscription.billing_status
            reason = ""
            if tenant.status == TenantStatus.TRIAL and aware(tenant.trial_ends_at) <= current:
                reason = "Trial ended without an active subscription"
            elif tenant.status == TenantStatus.ACTIVE and subscription.next_payment_due:
                overdue_days = (today - subscription.next_payment_due).days
                if overdue_days > subscription.grace_days and subscription.auto_suspend:
                    reason = (
                        f"Payment was {overdue_days} days overdue; "
                        f"the {subscription.grace_days}-day grace period ended"
                    )
                elif overdue_days > 0:
                    subscription.billing_status = "past_due"
                elif previous == "past_due":
                    subscription.billing_status = "active"
            if reason:
                tenant.status = TenantStatus.SUSPENDED
                tenant.automations_paused = True
                subscription.billing_status = "suspended"
                subscription.suspended_at = current
                subscription.suspension_reason = reason
                pause_open_actions(db, tenant.id)
                db.add(AuditLog(
                    tenant_id=tenant.id,
                    action="tenant_auto_suspended",
                    subject_type="tenant",
                    subject_id=tenant.id,
                    detail={"reason": reason, "data_deleted": False,
                            "automations_paused": True},
                ))
            elif previous != subscription.billing_status:
                db.add(AuditLog(
                    tenant_id=tenant.id,
                    action="billing_status_changed",
                    subject_type="tenant_subscription",
                    subject_id=tenant.id,
                    detail={"from": previous, "to": subscription.billing_status},
                ))
        db.commit()


def run() -> None:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    while True:
        now = datetime.now(timezone.utc)
        try:
            with SessionLocal() as db:
                set_database_tenant(db, platform_admin=True)
                db.execute(delete(UserSession).where(UserSession.expires_at < now))
                db.commit()
            process_billing_statuses(now)
            process_workflow_actions()
            process_owner_notifications()
            from .accounting import process_accounting
            process_accounting(lambda: redis.set('ivory-booking:worker-heartbeat', datetime.now(timezone.utc).isoformat(), ex=180))
            redis.set("ivory-booking:worker-heartbeat", datetime.now(timezone.utc).isoformat(), ex=180)
        except Exception:
            logging.getLogger(__name__).exception("Worker cycle failed; pending work is retained")
        time.sleep(60)


if __name__ == "__main__":
    run()
