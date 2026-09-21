"""Tenant-safe email rendering and delivery shared by Studio and the worker."""

import html
import mimetypes
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage as MimeMessage
from email.utils import make_msgid
from pathlib import Path

from sqlalchemy.orm import Session

from .models import (Booking, Client, EmailMessage, MailboxSetting, Tenant,
                     TenantEmailBranding)
from .config import get_settings
from .security import decrypt_secret


settings = get_settings()


def merge_message(value: str, tenant: Tenant, booking: Booking | None,
                  client: Client | None, extra: dict | None = None) -> str:
    extra = extra or {}
    replacements = {
        "business_name": tenant.display_name,
        "couple_names": booking.title if booking else extra.get("recipient_name", ""),
        "couple_first_name": client.first_name if client else extra.get("recipient_name", ""),
        "wedding_date": booking.event_date.strftime("%A %d %B %Y") if booking and booking.event_date else "",
        "venue": booking.venue if booking else "",
        "client_portal_link": extra.get("client_portal_link", ""),
    }
    result = value or ""
    for key, replacement in replacements.items():
        result = result.replace("{{" + key + "}}", str(replacement or ""))
    return result


def _paragraphs(value: str) -> str:
    return "".join(
        f"<p style=\"margin:0 0 16px;line-height:1.65\">{html.escape(part).replace(chr(10), '<br>')}</p>"
        for part in (value or "").split("\n\n") if part.strip()
    )


def render_html(tenant: Tenant, branding: TenantEmailBranding | None,
                body: str, embedded: dict[str, str], action_url: str = "",
                action_label: str = "Open your private booking") -> str:
    accent = str((tenant.branding or {}).get("accent_colour") or "#a9782e")
    branding = branding or TenantEmailBranding(tenant_id=tenant.id, signature_name=tenant.display_name)
    signature_name = branding.signature_name or tenant.display_name
    details = [html.escape(branding.signature_role or ""),
               html.escape(branding.telephone or "")]
    if branding.website:
        details.append(f'<a href="{html.escape(branding.website)}" style="color:{accent}">{html.escape(branding.website)}</a>')
    imagery = ""
    if embedded.get("logo") and branding.show_logo:
        imagery += f'<img src="cid:{embedded["logo"]}" alt="{html.escape(tenant.display_name)}" style="max-width:170px;max-height:80px;margin:0 16px 10px 0;vertical-align:middle">'
    if embedded.get("badge") and branding.show_badge:
        imagery += f'<img src="cid:{embedded["badge"]}" alt="Awards" style="max-width:260px;max-height:105px;margin:0 0 10px;vertical-align:middle">'
    action = ""
    if action_url:
        safe_url = html.escape(action_url, quote=True)
        action = (
            f'<p style="margin:24px 0 28px;text-align:center">'
            f'<a href="{safe_url}" style="display:inline-block;padding:15px 25px;border-radius:10px;'
            f'color:#ffffff;background:{accent};font-size:16px;font-weight:700;text-decoration:none">'
            f'{html.escape(action_label)}</a></p>'
        )
    return f"""<!doctype html><html><body style="margin:0;background:#f4f1eb;color:#243330;font-family:Arial,sans-serif">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f1eb;padding:24px 10px"><tr><td align="center">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:650px;background:#ffffff;border:1px solid #e7e0d5;border-radius:14px;overflow:hidden">
    <tr><td style="height:6px;background:{accent}"></td></tr><tr><td style="padding:34px 36px 20px">{_paragraphs(body)}{action}</td></tr>
    <tr><td style="padding:4px 36px 34px;border-top:1px solid #eee7dd"><p style="line-height:1.6;margin:20px 0 10px">{html.escape(branding.signoff or 'Kind regards')}<br><strong>{html.escape(signature_name)}</strong>{('<br>'+ '<br>'.join(x for x in details if x)) if any(details) else ''}</p>{imagery}</td></tr>
    </table><p style="font-size:11px;color:#75817e">Sent securely through Ivory Digital Booking Studio</p></td></tr></table></body></html>"""


def _asset_parts(branding: TenantEmailBranding | None) -> tuple[dict[str, str], list[tuple[str, bytes, str]]]:
    embedded: dict[str, str] = {}
    parts: list[tuple[str, bytes, str]] = []
    if not branding:
        return embedded, parts
    for kind, value, shown in (("logo", branding.logo_path, branding.show_logo),
                               ("badge", branding.badge_path, branding.show_badge)):
        path = Path(value) if value else None
        if not shown or not path or not path.is_file():
            continue
        cid = make_msgid(domain="ivorydigital.uk")[1:-1]
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        embedded[kind] = cid
        parts.append((cid, path.read_bytes(), mime))
    return embedded, parts


def send_tenant_email(db: Session, tenant: Tenant, mailbox: MailboxSetting,
                      recipient: str, subject: str, body: str,
                      booking: Booking | None = None, client: Client | None = None,
                      template_id: str | None = None, extra: dict | None = None,
                      in_reply_to: str = "",
                      attachments: list[tuple[str, bytes, str]] | None = None) -> EmailMessage:
    branding = db.get(TenantEmailBranding, tenant.id)
    merged_subject = merge_message(subject, tenant, booking, client, extra)
    merged_body = merge_message(body, tenant, booking, client, extra)
    embedded, parts = _asset_parts(branding)
    action_url = str((extra or {}).get("client_portal_link") or "")
    action_label = str((extra or {}).get("action_label") or "Open your private booking")
    rendered = render_html(tenant, branding, merged_body, embedded, action_url, action_label)
    mime = MimeMessage()
    mime["From"] = f"{mailbox.from_name} <{mailbox.email_address}>"
    mime["Reply-To"] = mailbox.email_address
    mime["To"] = recipient
    mime["Subject"] = merged_subject
    mime["Message-ID"] = make_msgid(domain="ivorydigital.uk")
    if in_reply_to:
        mime["In-Reply-To"] = in_reply_to
        mime["References"] = in_reply_to
    mime.set_content(merged_body)
    mime.add_alternative(rendered, subtype="html")
    html_part = mime.get_payload()[-1]
    for cid, raw, content_type in parts:
        maintype, subtype = content_type.split("/", 1)
        html_part.add_related(raw, maintype=maintype, subtype=subtype, cid=f"<{cid}>")
    for filename, raw, content_type in attachments or []:
        maintype, subtype = (content_type or "application/octet-stream").split("/", 1)
        mime.add_attachment(raw, maintype=maintype, subtype=subtype, filename=filename)
    record = EmailMessage(
        tenant_id=tenant.id,
        booking_id=booking.id if booking else None,
        template_id=template_id,
        direction="outbound", folder="sent",
        message_id_header=str(mime["Message-ID"] or ""),
        in_reply_to=in_reply_to,
        sender=mailbox.email_address, recipient=recipient,
        subject=merged_subject, body_text=merged_body, body_html=rendered,
        status="sending", is_read=True, sent_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.flush()
    saved_attachments = []
    if attachments:
        attachment_root = settings.tenant_storage_root / tenant.storage_key / "mail" / record.id
        attachment_root.mkdir(parents=True, exist_ok=True)
        for index, (filename, raw, content_type) in enumerate(attachments):
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).name).strip(".-") or "attachment"
            stored = f"{index + 1}-{safe_name}"[:220]
            (attachment_root / stored).write_bytes(raw)
            saved_attachments.append({"name": filename, "stored": stored,
                                      "content_type": content_type,
                                      "size": len(raw)})
        record.attachments = saved_attachments
    context = ssl.create_default_context()
    try:
        if mailbox.smtp_security == "ssl":
            connection = smtplib.SMTP_SSL(mailbox.smtp_host, mailbox.smtp_port, timeout=25, context=context)
        else:
            connection = smtplib.SMTP(mailbox.smtp_host, mailbox.smtp_port, timeout=25)
        try:
            connection.ehlo()
            if mailbox.smtp_security == "starttls":
                connection.starttls(context=context)
                connection.ehlo()
            connection.login(mailbox.smtp_username, decrypt_secret(mailbox.smtp_password_encrypted))
            connection.send_message(mime)
        finally:
            try:
                connection.quit()
            except Exception:
                connection.close()
        record.status = "sent"
    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)[:1000]
        raise
    return record
