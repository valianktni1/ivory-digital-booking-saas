import smtplib
import ssl
import time
from datetime import date, datetime, timezone
from email.message import EmailMessage

from redis import Redis
from sqlalchemy import delete, select

from .config import get_settings
from .database import SessionLocal
from .models import (AuditLog, Booking, Client, MailboxSetting, Tenant,
                     TenantStatus, TenantSubscription, UserSession, WorkflowAction)
from .security import decrypt_secret
from .tenant_context import set_database_tenant


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def merge_message(value: str, tenant: Tenant, booking: Booking | None,
                  client: Client | None, payload: dict) -> str:
    names = booking.title if booking else payload.get("recipient_name", "")
    replacements = {
        "business_name": tenant.display_name,
        "couple_names": names,
        "couple_first_name": client.first_name if client else payload.get("recipient_name", ""),
        "wedding_date": booking.event_date.strftime("%A %d %B %Y") if booking and booking.event_date else "",
        "venue": booking.venue if booking else "",
    }
    result = value
    for key, replacement in replacements.items():
        result = result.replace("{{" + key + "}}", str(replacement or ""))
    return result


def send_action(db, action: WorkflowAction, tenant: Tenant) -> None:
    if tenant.automations_paused or action.status != "queued" or aware(action.due_at) > datetime.now(timezone.utc):
        return
    payload = dict(action.payload or {})
    if payload.get("action_type") != "email":
        return
    mailbox = db.get(MailboxSetting, tenant.id)
    if not mailbox or not mailbox.smtp_verified_at or not mailbox.smtp_password_encrypted:
        raise RuntimeError("The studio SMTP connection has not been verified")
    booking = db.get(Booking, action.booking_id) if action.booking_id else None
    client = db.get(Client, booking.client_id) if booking else None
    recipient = payload.get("recipient_email") or (client.email if client else "")
    if not recipient:
        raise RuntimeError("No couple email address is available")
    message = EmailMessage()
    message["From"] = f"{mailbox.from_name} <{mailbox.email_address}>"
    message["To"] = recipient
    message["Subject"] = merge_message(payload.get("subject", ""), tenant, booking, client, payload)
    message.set_content(merge_message(payload.get("message_body", ""), tenant, booking, client, payload))
    context = ssl.create_default_context()
    if mailbox.smtp_security == "ssl":
        connection = smtplib.SMTP_SSL(mailbox.smtp_host, mailbox.smtp_port, timeout=20, context=context)
    else:
        connection = smtplib.SMTP(mailbox.smtp_host, mailbox.smtp_port, timeout=20)
    try:
        connection.ehlo()
        if mailbox.smtp_security == "starttls":
            connection.starttls(context=context); connection.ehlo()
        connection.login(mailbox.smtp_username, decrypt_secret(mailbox.smtp_password_encrypted))
        connection.send_message(message)
    finally:
        try:
            connection.quit()
        except Exception:
            connection.close()
    action.status = "sent"; action.completed_at = datetime.now(timezone.utc)
    payload["sent_at"] = action.completed_at.isoformat(); payload.pop("last_error", None)
    action.payload = payload
    db.add(AuditLog(tenant_id=tenant.id, action="workflow_email_sent",
                    subject_type="workflow_action", subject_id=action.id,
                    detail={"recipient": recipient, "subject": message["Subject"]}))


def process_workflow_actions() -> None:
    with SessionLocal() as scan:
        set_database_tenant(scan, platform_admin=True)
        candidates = list(scan.execute(select(WorkflowAction.id, WorkflowAction.tenant_id).where(
            WorkflowAction.status == "queued",
            WorkflowAction.due_at <= datetime.now(timezone.utc)
        ).order_by(WorkflowAction.due_at).limit(25)).all())
    for action_id, tenant_id in candidates:
        with SessionLocal() as db:
            set_database_tenant(db, tenant_id)
            action = db.scalar(select(WorkflowAction).where(
                WorkflowAction.id == action_id,
                WorkflowAction.tenant_id == tenant_id))
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
            redis.set("ivory-booking:worker-heartbeat", now.isoformat(), ex=180)
            with SessionLocal() as db:
                set_database_tenant(db, platform_admin=True)
                db.execute(delete(UserSession).where(UserSession.expires_at < now))
                db.commit()
            process_billing_statuses(now)
            process_workflow_actions()
        except Exception:
            # Docker restarts unhealthy dependencies; the worker retries without
            # changing or discarding tenant work.
            pass
        time.sleep(60)


if __name__ == "__main__":
    run()
