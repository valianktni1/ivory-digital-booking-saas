import base64
import html
import imaplib
import ipaddress
import io
import socket
import smtplib
import ssl
import re
from urllib.parse import quote, urlencode
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
import qrcode
import qrcode.image.svg
import httpx
from fastapi.responses import RedirectResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .database import Base, SessionLocal, engine, get_db
from .models import (AuditLog, Booking, BookingContract, BookingInvoice,
                     BookingJourney, BookingPayment, Client, Enquiry, EnquiryAnswer,
                     EnquiryFormConfig, EnquiryFormQuestion, Invitation,
                     MailboxSetting, Membership,
                     MembershipRole, PackageAddOn, QuestionnaireSubmission,
                     QuestionnaireTemplate, ServicePackage, Tenant,
                     TenantCalendarConnection, TenantCalendarOAuthState,
                     TenantContractTemplate,
                     TenantDateBlock, TenantInvoiceCounter, BookingQuoteRevision,
                     TenantStatus, User, UserSession, Workflow, WorkflowRevision,
                     WorkflowAction, WorkflowStep, WorkflowStepControl)
from .schemas import (AutomationPauseIn, BookingCreateIn, BrandingPatchIn,
                      BookingCancelIn, BookingCompleteIn, CalendarSettingsIn, ClientCreateIn,
                      ContractIssueIn, ContractSignIn, ContractTemplateIn,
                      DateBlockIn, EnquiryConvertIn, EnquiryFormIn, EnquiryQuestionIn,
                      InvitationAcceptIn, LoginIn, AddOnIn, MailboxSettingsIn,
                      PackageIn, PaymentRecordIn, PublicEnquiryIn,
                      QuestionnaireSubmitIn, QuestionnaireTemplateIn,
                      QuoteAcceptIn, QuoteAmendmentIn, QuoteDraftIn, SpecialPaymentIn,
                      TenantCreateIn, TenantStatusIn, TotpConfirmIn,
                      WorkflowBookingControlIn, WorkflowIn, WorkflowModeIn,
                      WorkflowStepIn, InvoiceVoidIn)
from .security import (clear_login_failures, create_session, csrf_matches,
                       decrypt_secret, encrypt_secret, find_session,
                       generate_totp_secret, hash_password, login_locked,
                       normalise_email, opaque_token, password_is_strong,
                       record_login_failure, recovery_codes, token_hash,
                       totp_uri, utcnow, verify_password, verify_totp)
from .tenant_context import (install_postgres_rls, membership_for,
                             set_database_tenant)


settings = get_settings()
SESSION_COOKIE = "ivory_booking_session"


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else None)


def audit(db: Session, action: str, subject_type: str, subject_id: str | None,
          actor: User | None = None, tenant_id: str | None = None,
          detail: dict | None = None, request: Request | None = None) -> None:
    db.add(AuditLog(
        tenant_id=tenant_id,
        actor_user_id=actor.id if actor else None,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        detail=detail or {},
        ip_address=client_ip(request) if request else None,
    ))


def bootstrap_platform_admin(db: Session) -> None:
    email = normalise_email(str(settings.platform_admin_email))
    admin = db.scalar(select(User).where(User.email == email))
    if not admin:
        admin = User(
            email=email,
            full_name=settings.platform_admin_name,
            password_hash=hash_password(settings.platform_admin_password),
            is_platform_admin=True,
        )
        db.add(admin)
        db.flush()
        audit(db, "platform_admin_bootstrapped", "user", admin.id, actor=admin)
    elif not admin.is_platform_admin:
        raise RuntimeError("PLATFORM_ADMIN_EMAIL belongs to a non-platform account")
    db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.platform_storage_root.mkdir(parents=True, exist_ok=True)
    settings.tenant_storage_root.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        bootstrap_platform_admin(db)
        install_postgres_rls(db)
        db.commit()
    yield


app = FastAPI(
    title="Ivory Digital Booking System",
    version="0.4.0-phase-four-journey-rc1",
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
    return response


def session_dependency(request: Request, db: Session = Depends(get_db)) -> UserSession:
    row = find_session(db, request.cookies.get(SESSION_COOKIE))
    if not row:
        raise HTTPException(401, "Please sign in")
    return row


def current_user(session: UserSession = Depends(session_dependency)) -> User:
    return session.user


def require_csrf(request: Request, session: UserSession = Depends(session_dependency)) -> UserSession:
    if not csrf_matches(session, request.headers.get("x-csrf-token")):
        raise HTTPException(403, "Your secure form token has expired. Refresh and try again")
    return session


def platform_admin(session: UserSession = Depends(session_dependency),
                   db: Session = Depends(get_db)) -> User:
    if not session.user.is_platform_admin:
        raise HTTPException(403, "Ivory Digital Manager access is required")
    if not session.user.totp_enabled or session.assurance != "mfa":
        raise HTTPException(403, "Two-factor authentication is required for Manager")
    set_database_tenant(db, platform_admin=True)
    return session.user


def platform_admin_write(request: Request,
                         session: UserSession = Depends(require_csrf),
                         db: Session = Depends(get_db)) -> User:
    if not session.user.is_platform_admin or not session.user.totp_enabled or session.assurance != "mfa":
        raise HTTPException(403, "Two-factor authentication is required for Manager")
    set_database_tenant(db, platform_admin=True)
    return session.user


def set_session_cookie(response: Response, token: str, csrf: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, secure=settings.cookie_secure,
        samesite="strict", max_age=settings.session_hours * 3600, path="/",
    )
    response.set_cookie(
        "ivory_booking_csrf", csrf, httponly=False, secure=settings.cookie_secure,
        samesite="strict", max_age=settings.session_hours * 3600, path="/",
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "build": "2026.09.18-phase-four-journey-rc1", "service": "ivory-booking-saas"}


@app.post("/api/auth/login")
def login(payload: LoginIn, response: Response, request: Request,
          db: Session = Depends(get_db)):
    email = normalise_email(str(payload.email))
    if login_locked(db, email):
        raise HTTPException(429, "Too many attempts. Please wait 15 minutes before trying again")
    user = db.scalar(select(User).where(User.email == email))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        record_login_failure(db, email)
        raise HTTPException(401, "Email or password is incorrect")
    if user.is_platform_admin and user.totp_enabled:
        if not payload.code:
            return Response(
                content='{"requires_two_factor":true}', status_code=status.HTTP_202_ACCEPTED,
                media_type="application/json",
            )
        secret = decrypt_secret(user.totp_secret_encrypted or "")
        if not verify_totp(secret, payload.code):
            supplied_recovery = token_hash(payload.code.strip().upper())
            remaining_codes = list(user.recovery_code_hashes or [])
            if supplied_recovery not in remaining_codes:
                record_login_failure(db, email)
                raise HTTPException(401, "That authentication code was not accepted")
            remaining_codes.remove(supplied_recovery)
            user.recovery_code_hashes = remaining_codes
        assurance = "mfa"
    else:
        assurance = "password"
    clear_login_failures(db, email)
    row, raw_token, csrf = create_session(
        db, user, client_ip(request), request.headers.get("user-agent"), assurance,
    )
    user.last_login_at = utcnow()
    audit(db, "login", "user", user.id, actor=user, request=request,
          detail={"assurance": assurance})
    db.commit()
    set_session_cookie(response, raw_token, csrf)
    return {
        "ok": True,
        "csrf_token": csrf,
        "requires_two_factor_setup": bool(user.is_platform_admin and not user.totp_enabled),
        "destination": "manager" if user.is_platform_admin else "studio",
    }


@app.get("/api/auth/me")
def me(session: UserSession = Depends(session_dependency), db: Session = Depends(get_db)):
    memberships = list(db.scalars(select(Membership).where(Membership.user_id == session.user_id)).all())
    return {
        "id": session.user.id,
        "email": session.user.email,
        "full_name": session.user.full_name,
        "is_platform_admin": session.user.is_platform_admin,
        "two_factor_enabled": session.user.totp_enabled,
        "assurance": session.assurance,
        "memberships": [{"tenant_id": row.tenant_id, "role": row.role.value} for row in memberships],
    }


@app.post("/api/auth/logout")
def logout(response: Response, request: Request,
           session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    session.revoked_at = utcnow()
    audit(db, "logout", "user", session.user_id, actor=session.user, request=request)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie("ivory_booking_csrf", path="/")
    return {"ok": True}


@app.post("/api/auth/2fa/setup")
def setup_two_factor(request: Request, session: UserSession = Depends(require_csrf),
                     db: Session = Depends(get_db)):
    user = session.user
    if not user.is_platform_admin:
        raise HTTPException(403, "Manager two-factor setup is only available to the platform administrator")
    if user.totp_enabled:
        raise HTTPException(409, "Two-factor authentication is already enabled")
    secret = generate_totp_secret()
    user.totp_secret_encrypted = encrypt_secret(secret)
    uri = totp_uri(secret, user.email)
    qr_buffer = io.BytesIO()
    qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage).save(qr_buffer)
    audit(db, "two_factor_setup_started", "user", user.id, actor=user, request=request)
    db.commit()
    return {
        "secret": secret,
        "provisioning_uri": uri,
        "qr_data_url": "data:image/svg+xml;base64," + base64.b64encode(qr_buffer.getvalue()).decode(),
    }


@app.post("/api/auth/2fa/enable")
def enable_two_factor(payload: TotpConfirmIn, request: Request,
                      session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    user = session.user
    if not user.is_platform_admin or not user.totp_secret_encrypted:
        raise HTTPException(409, "Start two-factor setup first")
    if not verify_totp(decrypt_secret(user.totp_secret_encrypted), payload.code):
        raise HTTPException(422, "That code did not match. Wait for a fresh code and try again")
    plain_codes, hashes = recovery_codes()
    user.totp_enabled = True
    user.recovery_code_hashes = hashes
    session.assurance = "mfa"
    audit(db, "two_factor_enabled", "user", user.id, actor=user, request=request)
    db.commit()
    return {"ok": True, "recovery_codes": plain_codes}


def create_invitation(db: Session, tenant: Tenant, email: str,
                      role: MembershipRole = MembershipRole.OWNER) -> tuple[Invitation, str]:
    raw = opaque_token(36)
    invitation = Invitation(
        tenant_id=tenant.id,
        email=normalise_email(email),
        role=role,
        token_hash=token_hash(raw),
        expires_at=utcnow() + timedelta(hours=settings.invitation_hours),
    )
    db.add(invitation)
    db.flush()
    return invitation, raw


def invitation_json(row: Invitation, raw_token: str | None = None) -> dict:
    result = {
        "id": row.id,
        "email": row.email,
        "role": row.role.value,
        "expires_at": row.expires_at.isoformat(),
        "accepted_at": row.accepted_at.isoformat() if row.accepted_at else None,
        "revoked": bool(row.revoked_at),
    }
    if raw_token:
        result["setup_url"] = f"{settings.studio_url.rstrip('/')}/?invite={raw_token}"
    return result


def tenant_json(row: Tenant, db: Session) -> dict:
    member_count = db.scalar(select(func.count(Membership.id)).where(Membership.tenant_id == row.id)) or 0
    latest_invite = db.scalar(select(Invitation).where(Invitation.tenant_id == row.id)
                              .order_by(Invitation.created_at.desc()).limit(1))
    return {
        "id": row.id,
        "slug": row.slug,
        "display_name": row.display_name,
        "owner_email": row.owner_email,
        "status": row.status.value,
        "trial_ends_at": row.trial_ends_at.isoformat(),
        "timezone": row.timezone,
        "automations_paused": row.automations_paused,
        "member_count": member_count,
        "invitation": invitation_json(latest_invite) if latest_invite else None,
        "studio_url": settings.studio_url,
        "client_url": f"{settings.client_url.rstrip('/')}/{row.slug}",
        "created_at": row.created_at.isoformat(),
    }


@app.get("/api/manager/summary")
def manager_summary(_: User = Depends(platform_admin), db: Session = Depends(get_db)):
    tenants = list(db.scalars(select(Tenant)).all())
    statuses = Counter(row.status.value for row in tenants)
    pending_invites = db.scalar(select(func.count(Invitation.id)).where(
        Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None), Invitation.expires_at > utcnow()
    )) or 0
    return {
        "businesses": len(tenants),
        "trial": statuses["trial"],
        "active": statuses["active"],
        "suspended": statuses["suspended"],
        "pending_invitations": pending_invites,
        "automatic_messages_paused": sum(1 for row in tenants if row.automations_paused),
    }


@app.get("/api/manager/tenants")
def list_tenants(_: User = Depends(platform_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(Tenant).order_by(Tenant.created_at.desc())).all()
    return [tenant_json(row, db) for row in rows]


@app.post("/api/manager/tenants", status_code=201)
def create_tenant(payload: TenantCreateIn, request: Request,
                  admin: User = Depends(platform_admin_write), db: Session = Depends(get_db)):
    if db.scalar(select(Tenant.id).where(Tenant.slug == payload.slug)):
        raise HTTPException(409, "That client address is already in use")
    if db.scalar(select(Tenant.id).where(Tenant.owner_email == normalise_email(str(payload.owner_email)))):
        raise HTTPException(409, "That email already owns a business account")
    started = utcnow()
    tenant = Tenant(
        slug=payload.slug,
        display_name=payload.display_name.strip(),
        owner_email=normalise_email(str(payload.owner_email)),
        status=TenantStatus.TRIAL,
        trial_started_at=started,
        trial_ends_at=started + timedelta(days=settings.trial_days),
        timezone=payload.timezone,
        onboarding={"business": False, "branding": False, "packages": False,
                    "templates": False, "calendar": False, "ready": False},
        branding={"display_name": payload.display_name.strip(), "accent_colour": "#a9782e",
                  "welcome_message": "Welcome to your private booking area."},
        automations_paused=True,
    )
    db.add(tenant)
    db.flush()
    db.add(Workflow(
        tenant_id=tenant.id,
        name="Main client journey",
        description="Your enquiry-to-wedding workflow. Add each step in the order you want it to happen.",
        is_active=False,
    ))
    invitation, raw = create_invitation(db, tenant, tenant.owner_email)
    tenant_path = settings.tenant_storage_root / tenant.storage_key
    tenant_path.mkdir(parents=True, exist_ok=False)
    audit(db, "tenant_created", "tenant", tenant.id, actor=admin, tenant_id=tenant.id,
          request=request, detail={"slug": tenant.slug, "owner_email": tenant.owner_email,
                                   "owner_name": payload.owner_name, "trial_days": settings.trial_days})
    db.commit()
    result = tenant_json(tenant, db)
    result["invitation"] = invitation_json(invitation, raw)
    return result


@app.post("/api/manager/tenants/{tenant_id}/invite")
def resend_invitation(tenant_id: str, request: Request,
                      admin: User = Depends(platform_admin_write), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Business not found")
    for row in db.scalars(select(Invitation).where(
            Invitation.tenant_id == tenant.id, Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None))).all():
        row.revoked_at = utcnow()
    invitation, raw = create_invitation(db, tenant, tenant.owner_email)
    audit(db, "invitation_reissued", "tenant", tenant.id, actor=admin,
          tenant_id=tenant.id, request=request)
    db.commit()
    return invitation_json(invitation, raw)


@app.patch("/api/manager/tenants/{tenant_id}/status")
def change_tenant_status(tenant_id: str, payload: TenantStatusIn, request: Request,
                         admin: User = Depends(platform_admin_write), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Business not found")
    old = tenant.status.value
    tenant.status = TenantStatus(payload.status)
    if tenant.status in {TenantStatus.SUSPENDED, TenantStatus.CANCELLED}:
        tenant.automations_paused = True
    audit(db, "tenant_status_changed", "tenant", tenant.id, actor=admin,
          tenant_id=tenant.id, request=request,
          detail={"from": old, "to": tenant.status.value, "reason": payload.reason})
    db.commit()
    return tenant_json(tenant, db)


@app.patch("/api/manager/tenants/{tenant_id}/automations")
def pause_tenant_automations(tenant_id: str, payload: AutomationPauseIn, request: Request,
                             admin: User = Depends(platform_admin_write), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Business not found")
    tenant.automations_paused = payload.paused
    actions = db.scalars(select(WorkflowAction).where(
        WorkflowAction.tenant_id == tenant.id,
        WorkflowAction.completed_at.is_(None),
        WorkflowAction.status.notin_(["sent", "completed", "cancelled"]))).all()
    for action in actions:
        details = dict(action.payload or {})
        if payload.paused:
            if action.status != "paused":
                details["resume_status"] = action.status
            action.status = "paused"
        elif action.status == "paused":
            action.status = details.get("resume_status") or (
                "review" if action.mode == "review" else "pending" if action.mode == "task" else "queued")
        action.payload = details
    audit(db, "tenant_automations_changed", "tenant", tenant.id, actor=admin,
          tenant_id=tenant.id, request=request,
          detail={"paused": payload.paused, "reason": payload.reason})
    db.commit()
    return tenant_json(tenant, db)


@app.get("/api/manager/tenants/{tenant_id}/support")
def support_view(tenant_id: str, request: Request,
                 admin: User = Depends(platform_admin), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Business not found")
    client_count = db.scalar(select(func.count(Client.id)).where(Client.tenant_id == tenant.id)) or 0
    booking_count = db.scalar(select(func.count(Booking.id)).where(Booking.tenant_id == tenant.id)) or 0
    audit(db, "support_view_opened", "tenant", tenant.id, actor=admin,
          tenant_id=tenant.id, request=request,
          detail={"scope": "health_and_counts_only"})
    db.commit()
    return {"tenant": tenant_json(tenant, db), "client_count": client_count,
            "booking_count": booking_count, "storage_key": tenant.storage_key,
            "data_access": "No couple details opened"}


@app.get("/api/manager/audit")
def manager_audit(_: User = Depends(platform_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100)).all()
    return [{"id": row.id, "action": row.action, "subject_type": row.subject_type,
             "subject_id": row.subject_id, "tenant_id": row.tenant_id,
             "detail": row.detail, "created_at": row.created_at.isoformat()} for row in rows]


def valid_invitation(db: Session, raw_token: str) -> Invitation:
    row = db.scalar(select(Invitation).options(selectinload(Invitation.tenant)).where(
        Invitation.token_hash == token_hash(raw_token)))
    if (not row or row.accepted_at or row.revoked_at
            or aware(row.expires_at) <= utcnow()):
        raise HTTPException(404, "This setup invitation is invalid or has expired")
    return row


@app.get("/api/invitations/{raw_token}")
def invitation_details(raw_token: str, db: Session = Depends(get_db)):
    row = valid_invitation(db, raw_token)
    return {"business_name": row.tenant.display_name, "email": row.email,
            "expires_at": row.expires_at.isoformat(), "trial_ends_at": row.tenant.trial_ends_at.isoformat()}


@app.post("/api/invitations/{raw_token}/accept")
def accept_invitation(raw_token: str, payload: InvitationAcceptIn, response: Response,
                      request: Request, db: Session = Depends(get_db)):
    if not password_is_strong(payload.password):
        raise HTTPException(422, "Use at least 14 characters with upper and lowercase letters, a number and a symbol")
    invitation = valid_invitation(db, raw_token)
    user = db.scalar(select(User).where(User.email == invitation.email))
    if user and user.password_hash:
        raise HTTPException(409, "This email already has an account. Sign in instead")
    if not user:
        user = User(email=invitation.email, full_name=payload.full_name.strip())
        db.add(user)
        db.flush()
    user.full_name = payload.full_name.strip()
    user.password_hash = hash_password(payload.password)
    user.is_active = True
    membership = db.scalar(select(Membership).where(
        Membership.tenant_id == invitation.tenant_id, Membership.user_id == user.id))
    if not membership:
        db.add(Membership(tenant_id=invitation.tenant_id, user_id=user.id, role=invitation.role))
    invitation.accepted_at = utcnow()
    row, token, csrf = create_session(db, user, client_ip(request), request.headers.get("user-agent"))
    audit(db, "invitation_accepted", "tenant", invitation.tenant_id, actor=user,
          tenant_id=invitation.tenant_id, request=request)
    db.commit()
    set_session_cookie(response, token, csrf)
    return {"ok": True, "csrf_token": csrf, "studio_url": settings.studio_url}


def studio_context(session: UserSession = Depends(session_dependency),
                   db: Session = Depends(get_db)) -> tuple[UserSession, Membership, Tenant]:
    if session.user.is_platform_admin:
        raise HTTPException(403, "Use Ivory Digital Manager for the platform account")
    membership = membership_for(db, session.user)
    tenant = db.get(Tenant, membership.tenant_id)
    return session, membership, tenant


def studio_write_context(session: UserSession, db: Session) -> tuple[Membership, Tenant]:
    if session.user.is_platform_admin:
        raise HTTPException(403, "Use Ivory Digital Manager for the platform account")
    membership = membership_for(db, session.user)
    if membership.role not in {MembershipRole.OWNER, MembershipRole.ADMIN}:
        raise HTTPException(403, "Owner or administrator access is required")
    return membership, db.get(Tenant, membership.tenant_id)


def package_json(row: ServicePackage) -> dict:
    return {"id": row.id, "name": row.name, "short_description": row.short_description,
            "price_pence": row.price_pence, "booking_fee_pence": row.booking_fee_pence,
            "balance_due_days": row.balance_due_days, "inclusions": row.inclusions or [],
            "is_featured": row.is_featured, "is_active": row.is_active,
            "sort_order": row.sort_order}


def add_on_json(row: PackageAddOn) -> dict:
    return {"id": row.id, "name": row.name, "description": row.description,
            "price_pence": row.price_pence, "selection_mode": row.selection_mode,
            "mandatory_reason": row.mandatory_reason, "is_active": row.is_active,
            "sort_order": row.sort_order}


def step_json(row: WorkflowStep, mode: str | None = None) -> dict:
    return {"id": row.id, "workflow_id": row.workflow_id, "name": row.name,
            "trigger_event": row.trigger_event, "timing_direction": row.timing_direction,
            "offset_value": row.offset_value, "offset_unit": row.offset_unit,
            "action_type": row.action_type, "subject": row.subject,
            "message_body": row.message_body, "task_title": row.task_title,
            "is_paused": row.is_paused, "mode": mode or ("off" if row.is_paused else "review"),
            "sort_order": row.sort_order}


def workflow_json(row: Workflow, db: Session) -> dict:
    steps = db.scalars(select(WorkflowStep).where(
        WorkflowStep.tenant_id == row.tenant_id, WorkflowStep.workflow_id == row.id
    ).order_by(WorkflowStep.sort_order, WorkflowStep.created_at)).all()
    controls = {item.step_id: item.mode for item in db.scalars(select(WorkflowStepControl).where(
        WorkflowStepControl.tenant_id == row.tenant_id,
        WorkflowStepControl.step_id.in_([step.id for step in steps])
    )).all()} if steps else {}
    return {"id": row.id, "name": row.name, "description": row.description,
            "is_active": row.is_active, "sort_order": row.sort_order,
            "revision": row.revision,
            "steps": [step_json(item, controls.get(item.id)) for item in steps]}


def save_workflow_revision(db: Session, workflow: Workflow, actor: User) -> None:
    db.add(WorkflowRevision(tenant_id=workflow.tenant_id, workflow_id=workflow.id,
                            revision=workflow.revision,
                            snapshot=workflow_json(workflow, db), actor_user_id=actor.id))
    workflow.revision += 1


STARTER_WORKFLOW_STEPS = (
    ("Warm enquiry acknowledgement", "enquiry_received", "immediately", 0, "days", "email", "Thanks for getting in touch", "Hi {{couple_first_name}},\n\nThank you for getting in touch about your wedding. I have your enquiry safely and will come back to you personally soon.\n\n{{business_name}}"),
    ("Check the quote arrived", "quote_sent", "after", 1, "days", "email", "Just checking your quote arrived", "Hi {{couple_first_name}},\n\nI just wanted to make sure your wedding quote arrived safely. Please feel free to ask me anything at all.\n\n{{business_name}}"),
    ("Final quote reminder", "quote_sent", "after", 9, "days", "email", "A final check-in about your date", "Hi {{couple_first_name}},\n\nI wanted to check in once more about your wedding on {{wedding_date}}. There is no pressure at all — I simply did not want you to miss the message.\n\n{{business_name}}"),
    ("Review the accepted quote", "quote_accepted", "immediately", 0, "days", "manual_task", "", ""),
    ("Booking details reminder", "quote_accepted", "after", 3, "days", "email", "Your booking details", "Hi {{couple_first_name}},\n\nWhen you have a moment, please complete the booking details in your private client area.\n\n{{business_name}}"),
    ("Agreement reminder", "quote_accepted", "after", 5, "days", "email", "Your wedding agreement", "Hi {{couple_first_name}},\n\nYour wedding agreement is waiting in your private client area whenever you are ready.\n\n{{business_name}}"),
    ("Booking fee check", "booking_fee_due", "immediately", 0, "days", "email", "A quick booking fee reminder", "Hi {{couple_first_name}},\n\nThis is a gentle reminder that the booking fee for your wedding is now due. If you have already paid it, thank you — please ignore this message.\n\n{{business_name}}"),
    ("Balance reminder", "balance_due", "before", 7, "days", "email", "Your wedding balance", "Hi {{couple_first_name}},\n\nJust a friendly reminder that your remaining wedding balance is due soon.\n\n{{business_name}}"),
    ("Final balance reminder", "balance_due", "before", 1, "days", "email", "Wedding balance due tomorrow", "Hi {{couple_first_name}},\n\nA quick reminder that your remaining wedding balance is due tomorrow.\n\n{{business_name}}"),
    ("Four-month check-in", "wedding_date", "before", 120, "days", "manual_task", "", ""),
    ("Send final timings form", "wedding_date", "before", 30, "days", "email", "Your final wedding timings", "Hi {{couple_first_name}},\n\nYour wedding is getting close. Please complete the final timings form in your private client area when you are ready.\n\n{{business_name}}"),
)


def ensure_starter_workflow(db: Session, tenant: Tenant) -> None:
    workflow = db.scalar(select(Workflow).where(Workflow.tenant_id == tenant.id)
                         .order_by(Workflow.created_at).limit(1))
    if not workflow:
        workflow = Workflow(tenant_id=tenant.id, name="Main client journey",
                            description="Your complete enquiry-to-wedding journey.",
                            is_active=True)
        db.add(workflow); db.flush()
    existing = db.scalar(select(func.count(WorkflowStep.id)).where(
        WorkflowStep.tenant_id == tenant.id, WorkflowStep.workflow_id == workflow.id)) or 0
    if existing:
        return
    workflow.is_active = True
    for order, (name, trigger, direction, offset, unit, action, subject, message) in enumerate(STARTER_WORKFLOW_STEPS):
        row = WorkflowStep(tenant_id=tenant.id, workflow_id=workflow.id,
                           name=name, trigger_event=trigger,
                           timing_direction=direction, offset_value=offset,
                           offset_unit=unit, action_type=action, subject=subject,
                           message_body=message,
                           task_title=name if action == "manual_task" else "",
                           is_paused=True, sort_order=order)
        db.add(row); db.flush()
        db.add(WorkflowStepControl(step_id=row.id, tenant_id=tenant.id, mode="off"))


def validate_package(payload: PackageIn) -> None:
    if payload.booking_fee_pence > payload.price_pence:
        raise HTTPException(422, "The booking fee cannot be more than the package price")


def validate_add_on(payload: AddOnIn) -> None:
    if payload.selection_mode == "mandatory" and not payload.mandatory_reason:
        raise HTTPException(422, "Explain why this add-on is mandatory for the couple")


def mark_onboarding(tenant: Tenant, key: str) -> None:
    onboarding = dict(tenant.onboarding or {})
    onboarding[key] = True
    tenant.onboarding = onboarding


def enquiry_form_json(row: EnquiryFormConfig, tenant: Tenant) -> dict:
    return {"heading": row.heading, "introduction": row.introduction,
            "submit_label": row.submit_label, "success_message": row.success_message,
            "ask_partner_name": row.ask_partner_name, "ask_phone": row.ask_phone,
            "ask_venue": row.ask_venue, "ask_package_interest": row.ask_package_interest,
            "ask_message": row.ask_message, "is_published": row.is_published,
            "public_url": f"{settings.client_url.rstrip('/')}/{tenant.slug}/enquire"}


DEFAULT_ENQUIRY_QUESTIONS = (
    ("first_name", "Your name", "short_text", True),
    ("partner_name", "Partner's name", "short_text", False),
    ("email", "Email address", "email", True),
    ("phone", "Telephone number", "phone", False),
    ("event_date", "Wedding or event date", "date", True),
    ("venue", "Wedding or event venue", "venue", True),
    ("package_interest", "Package you are interested in", "single_choice", False),
    ("message", "Tell us a little about your plans", "long_text", False),
)


def ensure_default_enquiry_questions(db: Session, tenant: Tenant,
                                     config: EnquiryFormConfig | None = None) -> list[EnquiryFormQuestion]:
    existing = list(db.scalars(select(EnquiryFormQuestion).where(
        EnquiryFormQuestion.tenant_id == tenant.id
    ).order_by(EnquiryFormQuestion.sort_order, EnquiryFormQuestion.created_at)).all())
    existing_keys = {row.system_key for row in existing}
    visibility = {
        "partner_name": config.ask_partner_name if config else True,
        "phone": config.ask_phone if config else True,
        "venue": config.ask_venue if config else True,
        "package_interest": config.ask_package_interest if config else True,
        "message": config.ask_message if config else True,
    }
    changed = False
    for order, (key, label, kind, required) in enumerate(DEFAULT_ENQUIRY_QUESTIONS):
        if key not in existing_keys:
            row = EnquiryFormQuestion(tenant_id=tenant.id, system_key=key, label=label,
                                      question_type=kind, is_required=required,
                                      is_protected=True, is_active=visibility.get(key, True),
                                      sort_order=order)
            db.add(row); existing.append(row); changed = True
    if changed:
        db.flush()
    return sorted(existing, key=lambda item: (item.sort_order, item.created_at))


def enquiry_question_json(row: EnquiryFormQuestion) -> dict:
    return {"id": row.id, "system_key": row.system_key, "label": row.label,
            "help_text": row.help_text, "question_type": row.question_type,
            "is_required": row.is_required, "is_protected": row.is_protected,
            "is_active": row.is_active, "options": row.options or [],
            "sort_order": row.sort_order}


def mailbox_json(row: MailboxSetting | None) -> dict:
    if not row:
        return {"configured": False, "from_name": "", "email_address": "",
                "smtp_host": "", "smtp_port": 465, "smtp_security": "ssl",
                "smtp_username": "", "smtp_has_password": False,
                "imap_host": "", "imap_port": 993, "imap_security": "ssl",
                "imap_username": "", "imap_has_password": False,
                "smtp_verified_at": None, "imap_verified_at": None}
    return {"configured": bool(row.smtp_password_encrypted and row.imap_password_encrypted),
            "from_name": row.from_name, "email_address": row.email_address,
            "smtp_host": row.smtp_host, "smtp_port": row.smtp_port,
            "smtp_security": row.smtp_security, "smtp_username": row.smtp_username,
            "smtp_has_password": bool(row.smtp_password_encrypted),
            "imap_host": row.imap_host, "imap_port": row.imap_port,
            "imap_security": row.imap_security, "imap_username": row.imap_username,
            "imap_has_password": bool(row.imap_password_encrypted),
            "smtp_verified_at": row.smtp_verified_at.isoformat() if row.smtp_verified_at else None,
            "imap_verified_at": row.imap_verified_at.isoformat() if row.imap_verified_at else None}


@app.get("/api/studio/dashboard")
def studio_dashboard(context=Depends(studio_context), db: Session = Depends(get_db)):
    session, membership, tenant = context
    client_count = db.scalar(select(func.count(Client.id)).where(Client.tenant_id == tenant.id)) or 0
    booking_count = db.scalar(select(func.count(Booking.id)).where(Booking.tenant_id == tenant.id)) or 0
    enquiry_count = db.scalar(select(func.count(Enquiry.id)).where(Enquiry.tenant_id == tenant.id)) or 0
    return {
        "user": {"full_name": session.user.full_name, "email": session.user.email,
                 "role": membership.role.value},
        "tenant": tenant_json(tenant, db),
        "onboarding": tenant.onboarding or {},
        "client_count": client_count,
        "booking_count": booking_count,
        "enquiry_count": enquiry_count,
        "phase": "Complete booking journey release candidate",
    }


@app.patch("/api/studio/branding")
def update_branding(payload: BrandingPatchIn, request: Request,
                    session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    if session.user.is_platform_admin:
        raise HTTPException(403, "Use Ivory Digital Manager for the platform account")
    membership = membership_for(db, session.user)
    if membership.role not in {MembershipRole.OWNER, MembershipRole.ADMIN}:
        raise HTTPException(403, "Owner or administrator access is required")
    tenant = db.get(Tenant, membership.tenant_id)
    tenant.display_name = payload.display_name.strip()
    tenant.branding = payload.model_dump()
    onboarding = dict(tenant.onboarding or {})
    onboarding["business"] = True
    onboarding["branding"] = True
    tenant.onboarding = onboarding
    audit(db, "branding_updated", "tenant", tenant.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit()
    return tenant_json(tenant, db)


@app.get("/api/studio/packages")
def list_packages(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    rows = db.scalars(select(ServicePackage).where(ServicePackage.tenant_id == tenant.id)
                      .order_by(ServicePackage.sort_order, ServicePackage.created_at)).all()
    return [package_json(row) for row in rows]


@app.post("/api/studio/packages", status_code=201)
def create_package(payload: PackageIn, request: Request,
                   session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    validate_package(payload)
    membership, tenant = studio_write_context(session, db)
    row = ServicePackage(tenant_id=membership.tenant_id, **payload.model_dump())
    db.add(row); db.flush()
    if row.is_active: mark_onboarding(tenant, "packages")
    audit(db, "package_created", "service_package", row.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit()
    return package_json(row)


@app.patch("/api/studio/packages/{package_id}")
def update_package(package_id: str, payload: PackageIn, request: Request,
                   session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    validate_package(payload)
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(ServicePackage).where(ServicePackage.id == package_id,
                    ServicePackage.tenant_id == membership.tenant_id))
    if not row: raise HTTPException(404, "Package not found")
    for key, value in payload.model_dump().items(): setattr(row, key, value)
    if row.is_active: mark_onboarding(tenant, "packages")
    audit(db, "package_updated", "service_package", row.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit()
    return package_json(row)


@app.get("/api/studio/add-ons")
def list_add_ons(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    rows = db.scalars(select(PackageAddOn).where(PackageAddOn.tenant_id == tenant.id)
                      .order_by(PackageAddOn.sort_order, PackageAddOn.created_at)).all()
    return [add_on_json(row) for row in rows]


@app.post("/api/studio/add-ons", status_code=201)
def create_add_on(payload: AddOnIn, request: Request,
                  session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    validate_add_on(payload)
    membership, tenant = studio_write_context(session, db)
    row = PackageAddOn(tenant_id=membership.tenant_id, **payload.model_dump())
    db.add(row); db.flush()
    audit(db, "add_on_created", "package_add_on", row.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit()
    return add_on_json(row)


@app.patch("/api/studio/add-ons/{add_on_id}")
def update_add_on(add_on_id: str, payload: AddOnIn, request: Request,
                  session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    validate_add_on(payload)
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(PackageAddOn).where(PackageAddOn.id == add_on_id,
                    PackageAddOn.tenant_id == membership.tenant_id))
    if not row: raise HTTPException(404, "Add-on not found")
    for key, value in payload.model_dump().items(): setattr(row, key, value)
    audit(db, "add_on_updated", "package_add_on", row.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit()
    return add_on_json(row)


@app.get("/api/studio/workflows")
def list_workflows(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    ensure_starter_workflow(db, tenant)
    rows = db.scalars(select(Workflow).where(Workflow.tenant_id == tenant.id)
                      .order_by(Workflow.sort_order, Workflow.created_at)).all()
    result = [workflow_json(row, db) for row in rows]
    db.commit()
    return result


@app.post("/api/studio/workflows", status_code=201)
def create_workflow(payload: WorkflowIn, request: Request,
                    session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = Workflow(tenant_id=membership.tenant_id, **payload.model_dump())
    db.add(row); db.flush()
    audit(db, "workflow_created", "workflow", row.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    result = workflow_json(row, db)
    db.commit()
    return result


@app.patch("/api/studio/workflows/{workflow_id}")
def update_workflow(workflow_id: str, payload: WorkflowIn, request: Request,
                    session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(Workflow).where(Workflow.id == workflow_id,
                    Workflow.tenant_id == membership.tenant_id))
    if not row: raise HTTPException(404, "Workflow not found")
    save_workflow_revision(db, row, session.user)
    for key, value in payload.model_dump().items(): setattr(row, key, value)
    audit(db, "workflow_updated", "workflow", row.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    result = workflow_json(row, db)
    db.commit()
    return result


@app.post("/api/studio/workflows/{workflow_id}/steps", status_code=201)
def create_workflow_step(workflow_id: str, payload: WorkflowStepIn, request: Request,
                         session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    workflow = db.scalar(select(Workflow).where(Workflow.id == workflow_id,
                         Workflow.tenant_id == membership.tenant_id))
    if not workflow: raise HTTPException(404, "Workflow not found")
    save_workflow_revision(db, workflow, session.user)
    data = payload.model_dump()
    data["is_paused"] = True  # Configuration cannot accidentally start sending.
    row = WorkflowStep(tenant_id=tenant.id, workflow_id=workflow.id, **data)
    workflow.is_active = True
    db.add(row); db.flush()
    db.add(WorkflowStepControl(step_id=row.id, tenant_id=tenant.id, mode="off"))
    mark_onboarding(tenant, "templates")
    audit(db, "workflow_step_created", "workflow_step", row.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"forced_paused": True})
    db.commit()
    return step_json(row, "off")


@app.patch("/api/studio/workflows/{workflow_id}/steps/{step_id}")
def update_workflow_step(workflow_id: str, step_id: str, payload: WorkflowStepIn,
                         request: Request, session: UserSession = Depends(require_csrf),
                         db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    workflow = db.scalar(select(Workflow).where(Workflow.id == workflow_id,
                         Workflow.tenant_id == membership.tenant_id))
    if not workflow: raise HTTPException(404, "Workflow not found")
    row = db.scalar(select(WorkflowStep).where(WorkflowStep.id == step_id,
                    WorkflowStep.workflow_id == workflow.id,
                    WorkflowStep.tenant_id == membership.tenant_id))
    if not row: raise HTTPException(404, "Workflow step not found")
    save_workflow_revision(db, workflow, session.user)
    for key, value in payload.model_dump().items(): setattr(row, key, value)
    audit(db, "workflow_step_updated", "workflow_step", row.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit()
    return step_json(row)


@app.get("/api/studio/enquiry-form")
def get_enquiry_form(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    row = db.get(EnquiryFormConfig, tenant.id)
    if not row:
        row = EnquiryFormConfig(tenant_id=tenant.id)
        db.add(row); db.flush()
    questions = ensure_default_enquiry_questions(db, tenant, row)
    db.commit(); db.refresh(row)
    result = enquiry_form_json(row, tenant)
    result["questions"] = [enquiry_question_json(item) for item in questions]
    return result


@app.get("/api/studio/enquiries")
def list_enquiries(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    rows = db.scalars(select(Enquiry).where(Enquiry.tenant_id == tenant.id)
                      .order_by(Enquiry.created_at.desc()).limit(250)).all()
    result = []
    for row in rows:
        answers = db.scalars(select(EnquiryAnswer).where(
            EnquiryAnswer.tenant_id == tenant.id, EnquiryAnswer.enquiry_id == row.id
        ).order_by(EnquiryAnswer.sort_order)).all()
        result.append({"id": row.id, "first_name": row.first_name, "partner_name": row.partner_name,
             "email": row.email, "phone": row.phone,
             "event_date": row.event_date.isoformat() if row.event_date else None,
             "venue": row.venue, "package_interest": row.package_interest,
             "message": row.message, "status": row.status,
             "answers": [{"label": item.question_label, "answer": item.answer} for item in answers],
             "created_at": row.created_at.isoformat()})
    return result


@app.put("/api/studio/enquiry-form")
def save_enquiry_form(payload: EnquiryFormIn, request: Request,
                      session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.get(EnquiryFormConfig, membership.tenant_id)
    if not row:
        row = EnquiryFormConfig(tenant_id=membership.tenant_id)
        db.add(row)
    for key, value in payload.model_dump().items(): setattr(row, key, value)
    onboarding = dict(tenant.onboarding or {}); onboarding["enquiry_form"] = True; tenant.onboarding = onboarding
    audit(db, "enquiry_form_updated", "enquiry_form", tenant.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"published": row.is_published})
    questions = ensure_default_enquiry_questions(db, tenant, row)
    db.commit()
    result = enquiry_form_json(row, tenant)
    result["questions"] = [enquiry_question_json(item) for item in questions]
    return result


@app.post("/api/studio/enquiry-form/questions", status_code=201)
def create_enquiry_question(payload: EnquiryQuestionIn, request: Request,
                            session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    if payload.question_type in {"single_choice", "multiple_choice"} and len(payload.options) < 2:
        raise HTTPException(422, "Add at least two choices for this question")
    row = EnquiryFormQuestion(tenant_id=membership.tenant_id, **payload.model_dump())
    db.add(row); db.flush()
    audit(db, "enquiry_question_created", "enquiry_question", row.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit()
    return enquiry_question_json(row)


@app.patch("/api/studio/enquiry-form/questions/{question_id}")
def update_enquiry_question(question_id: str, payload: EnquiryQuestionIn, request: Request,
                            session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(EnquiryFormQuestion).where(
        EnquiryFormQuestion.id == question_id,
        EnquiryFormQuestion.tenant_id == membership.tenant_id))
    if not row:
        raise HTTPException(404, "Question not found")
    if payload.question_type in {"single_choice", "multiple_choice"} and len(payload.options) < 2:
        raise HTTPException(422, "Add at least two choices for this question")
    data = payload.model_dump()
    if row.is_protected:
        data["question_type"] = row.question_type
        data["is_active"] = True if row.system_key in {"first_name", "email"} else data["is_active"]
    for key, value in data.items(): setattr(row, key, value)
    audit(db, "enquiry_question_updated", "enquiry_question", row.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit()
    return enquiry_question_json(row)


@app.delete("/api/studio/enquiry-form/questions/{question_id}")
def delete_enquiry_question(question_id: str, request: Request,
                            session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(EnquiryFormQuestion).where(
        EnquiryFormQuestion.id == question_id,
        EnquiryFormQuestion.tenant_id == membership.tenant_id))
    if not row:
        raise HTTPException(404, "Question not found")
    if row.is_protected:
        raise HTTPException(409, "This core question is protected and cannot be deleted")
    audit(db, "enquiry_question_deleted", "enquiry_question", row.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"label": row.label})
    db.delete(row); db.commit()
    return {"ok": True}


@app.get("/api/studio/mailbox")
def get_mailbox(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    return mailbox_json(db.get(MailboxSetting, tenant.id))


@app.put("/api/studio/mailbox")
def save_mailbox(payload: MailboxSettingsIn, request: Request,
                 session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.get(MailboxSetting, membership.tenant_id)
    if not row:
        row = MailboxSetting(tenant_id=membership.tenant_id)
        db.add(row)
    for key in ("from_name", "email_address", "smtp_host", "smtp_port", "smtp_security",
                "smtp_username", "imap_host", "imap_port", "imap_security", "imap_username"):
        setattr(row, key, getattr(payload, key))
    if payload.smtp_password:
        row.smtp_password_encrypted = encrypt_secret(payload.smtp_password)
        row.smtp_verified_at = None
    elif not row.smtp_password_encrypted:
        raise HTTPException(422, "Enter the outgoing mail password")
    if payload.imap_password:
        row.imap_password_encrypted = encrypt_secret(payload.imap_password)
        row.imap_verified_at = None
    elif not row.imap_password_encrypted:
        raise HTTPException(422, "Enter the incoming mail password")
    onboarding = dict(tenant.onboarding or {}); onboarding["mailbox"] = True; tenant.onboarding = onboarding
    audit(db, "mailbox_settings_updated", "mailbox", tenant.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"email_address": row.email_address, "passwords_logged": False})
    db.commit()
    return mailbox_json(row)


def test_smtp(row: MailboxSetting) -> None:
    ensure_public_mail_host(row.smtp_host, row.smtp_port, {25, 465, 587, 2525})
    password = decrypt_secret(row.smtp_password_encrypted)
    context = ssl.create_default_context()
    if row.smtp_security == "ssl":
        connection = smtplib.SMTP_SSL(row.smtp_host, row.smtp_port, timeout=10, context=context)
    else:
        connection = smtplib.SMTP(row.smtp_host, row.smtp_port, timeout=10)
    try:
        connection.ehlo()
        if row.smtp_security == "starttls": connection.starttls(context=context); connection.ehlo()
        connection.login(row.smtp_username, password)
    finally:
        try: connection.quit()
        except Exception: connection.close()


def test_imap(row: MailboxSetting) -> None:
    ensure_public_mail_host(row.imap_host, row.imap_port, {143, 993})
    password = decrypt_secret(row.imap_password_encrypted)
    if row.imap_security == "ssl":
        connection = imaplib.IMAP4_SSL(row.imap_host, row.imap_port, ssl_context=ssl.create_default_context(), timeout=10)
    else:
        connection = imaplib.IMAP4(row.imap_host, row.imap_port, timeout=10)
        if row.imap_security == "starttls": connection.starttls(ssl_context=ssl.create_default_context())
    try: connection.login(row.imap_username, password)
    finally:
        try: connection.logout()
        except Exception: pass


def ensure_public_mail_host(host: str, port: int, allowed_ports: set[int]) -> None:
    if port not in allowed_ports:
        raise ValueError("Unsupported mail port")
    lowered = host.strip().lower().rstrip(".")
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith((".local", ".internal", ".localhost")):
        raise ValueError("Private mail hosts are not permitted")
    addresses = {item[4][0] for item in socket.getaddrinfo(lowered, port, type=socket.SOCK_STREAM)}
    if not addresses:
        raise ValueError("Mail host did not resolve")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Private or reserved mail addresses are not permitted")


@app.post("/api/studio/mailbox/test/{protocol}")
def test_mailbox(protocol: str, request: Request,
                 session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.get(MailboxSetting, membership.tenant_id)
    if not row or protocol not in {"smtp", "imap"}:
        raise HTTPException(404, "Mail settings not found")
    try:
        (test_smtp if protocol == "smtp" else test_imap)(row)
    except Exception as exc:
        audit(db, f"{protocol}_connection_failed", "mailbox", tenant.id, actor=session.user,
              tenant_id=tenant.id, request=request, detail={"error_type": type(exc).__name__})
        db.commit()
        raise HTTPException(422, f"The {protocol.upper()} connection was not accepted. Check the server, port, security and login") from exc
    setattr(row, f"{protocol}_verified_at", utcnow())
    audit(db, f"{protocol}_connection_verified", "mailbox", tenant.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit()
    return {"ok": True, "protocol": protocol, "verified_at": getattr(row, f"{protocol}_verified_at").isoformat()}


@app.get("/api/studio/clients")
def list_clients(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    rows = db.scalars(select(Client).where(Client.tenant_id == tenant.id)
                      .order_by(Client.created_at.desc())).all()
    return [{"id": row.id, "first_name": row.first_name, "last_name": row.last_name,
             "partner_name": row.partner_name, "email": row.email, "phone": row.phone}
            for row in rows]


@app.post("/api/studio/clients", status_code=201)
def create_client(payload: ClientCreateIn, request: Request,
                  session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    if session.user.is_platform_admin:
        raise HTTPException(403, "Use Ivory Digital Manager for the platform account")
    membership = membership_for(db, session.user)
    row = Client(tenant_id=membership.tenant_id, **payload.model_dump())
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "A client with that email already exists in your business") from exc
    audit(db, "client_created", "client", row.id, actor=session.user,
          tenant_id=membership.tenant_id, request=request)
    db.commit()
    return {"id": row.id, "tenant_id": row.tenant_id, "email": row.email}


@app.get("/api/studio/clients/{client_id}")
def get_client(client_id: str, context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    row = db.scalar(select(Client).where(Client.id == client_id, Client.tenant_id == tenant.id))
    if not row:
        raise HTTPException(404, "Client not found")
    return {"id": row.id, "first_name": row.first_name, "last_name": row.last_name,
            "partner_name": row.partner_name, "email": row.email, "phone": row.phone}


@app.post("/api/studio/bookings", status_code=201)
def create_booking(payload: BookingCreateIn, request: Request,
                   session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    if session.user.is_platform_admin:
        raise HTTPException(403, "Use Ivory Digital Manager for the platform account")
    membership = membership_for(db, session.user)
    client = db.scalar(select(Client).where(
        Client.id == payload.client_id, Client.tenant_id == membership.tenant_id))
    if not client:
        raise HTTPException(404, "Client not found")
    row = Booking(tenant_id=membership.tenant_id, **payload.model_dump())
    db.add(row)
    db.flush()
    audit(db, "booking_created", "booking", row.id, actor=session.user,
          tenant_id=membership.tenant_id, request=request)
    db.commit()
    return {"id": row.id, "tenant_id": row.tenant_id, "title": row.title}


def studio_booking(db: Session, tenant_id: str, booking_id: str) -> Booking:
    row = db.scalar(select(Booking).where(Booking.id == booking_id,
                    Booking.tenant_id == tenant_id))
    if not row:
        raise HTTPException(404, "Wedding not found")
    return row


def booking_journey(db: Session, booking: Booking) -> BookingJourney:
    row = db.scalar(select(BookingJourney).where(
        BookingJourney.booking_id == booking.id,
        BookingJourney.tenant_id == booking.tenant_id))
    if not row:
        raw = opaque_token(32)
        row = BookingJourney(booking_id=booking.id, tenant_id=booking.tenant_id,
                             portal_token_hash=token_hash(raw),
                             portal_token_encrypted=encrypt_secret(raw))
        db.add(row); db.flush()
    return row


def portal_url(journey: BookingJourney) -> str:
    raw = decrypt_secret(journey.portal_token_encrypted)
    return f"{settings.client_url.rstrip('/')}/portal/{raw}"


def invoice_json(row: BookingInvoice, db: Session) -> dict:
    payments = db.scalars(select(BookingPayment).where(
        BookingPayment.tenant_id == row.tenant_id,
        BookingPayment.invoice_id == row.id
    ).order_by(BookingPayment.paid_date, BookingPayment.created_at)).all()
    return {"id": row.id, "number": row.number, "issue_date": row.issue_date.isoformat(),
            "booking_fee_due_date": row.booking_fee_due_date.isoformat() if row.booking_fee_due_date else None,
            "due_date": row.due_date.isoformat() if row.due_date else None,
            "total_pence": row.total_pence, "paid_pence": row.paid_pence,
            "outstanding_pence": max(0, row.total_pence - row.paid_pence),
            "status": row.status, "line_items": row.line_items or [],
            "payment_schedule": row.payment_schedule or [], "notes": row.notes,
            "void_reason": row.void_reason,
            "payments": [{"id": item.id, "amount_pence": item.amount_pence,
                           "paid_date": item.paid_date.isoformat(),
                           "payment_type": item.payment_type,
                           "reference": item.reference, "notes": item.notes}
                          for item in payments]}


def contract_json(row: BookingContract | None) -> dict | None:
    if not row:
        return None
    return {"id": row.id, "title": row.title, "version": row.version,
            "body": row.body_snapshot, "client_name": row.client_name,
            "client_signed_at": row.client_signed_at.isoformat() if row.client_signed_at else None,
            "supplier_name": row.supplier_name,
            "supplier_signed_at": row.supplier_signed_at.isoformat() if row.supplier_signed_at else None}


def questionnaire_json(row: QuestionnaireTemplate, submission: QuestionnaireSubmission | None = None) -> dict:
    return {"id": row.id, "form_type": row.form_type, "name": row.name,
            "introduction": row.introduction, "questions": row.questions or [],
            "is_active": row.is_active,
            "submission": ({"answers": submission.answers or {},
                            "submitted_at": submission.submitted_at.isoformat()}
                           if submission else None)}


def journey_json(db: Session, booking: Booking, journey: BookingJourney,
                 include_portal_url: bool = True) -> dict:
    client = db.scalar(select(Client).where(Client.id == booking.client_id,
                       Client.tenant_id == booking.tenant_id))
    invoices = db.scalars(select(BookingInvoice).where(
        BookingInvoice.tenant_id == booking.tenant_id,
        BookingInvoice.booking_id == booking.id
    ).order_by(BookingInvoice.created_at.desc())).all()
    contract = db.scalar(select(BookingContract).where(
        BookingContract.tenant_id == booking.tenant_id,
        BookingContract.booking_id == booking.id))
    submissions = db.scalars(select(QuestionnaireSubmission).where(
        QuestionnaireSubmission.tenant_id == booking.tenant_id,
        QuestionnaireSubmission.booking_id == booking.id)).all()
    actions = db.scalars(select(WorkflowAction).where(
        WorkflowAction.tenant_id == booking.tenant_id,
        WorkflowAction.booking_id == booking.id
    ).order_by(WorkflowAction.due_at)).all()
    quote_revisions = db.scalars(select(BookingQuoteRevision).where(
        BookingQuoteRevision.tenant_id == booking.tenant_id,
        BookingQuoteRevision.booking_id == booking.id
    ).order_by(BookingQuoteRevision.created_at)).all()
    result = {"id": booking.id, "title": booking.title,
              "event_date": booking.event_date.isoformat() if booking.event_date else None,
              "venue": booking.venue, "status": booking.status,
              "client": {"id": client.id, "first_name": client.first_name,
                         "last_name": client.last_name, "partner_name": client.partner_name,
                         "email": client.email, "phone": client.phone} if client else None,
              "quote": journey.quote_state or {}, "accepted_quote": journey.accepted_quote or {},
              "quote_revisions": [{"id": item.id, "reason": item.reason,
                                    "created_at": item.created_at.isoformat()}
                                   for item in quote_revisions],
              "booking_fee_pence": journey.booking_fee_pence,
              "balance_due_date": journey.balance_due_date.isoformat() if journey.balance_due_date else None,
              "special_payment_arrangement": journey.special_payment_arrangement,
              "special_payment_note": journey.special_payment_note,
              "workflow_controls": journey.workflow_controls or {},
              "calendar": journey.calendar_state or {},
              "completed_at": journey.completed_at.isoformat() if journey.completed_at else None,
              "cancelled_at": journey.cancelled_at.isoformat() if journey.cancelled_at else None,
              "invoices": [invoice_json(item, db) for item in invoices],
              "contract": contract_json(contract),
              "questionnaires": [{"form_type": item.form_type,
                                    "submitted_at": item.submitted_at.isoformat()}
                                   for item in submissions],
              "workflow_actions": [{"id": item.id, "step_id": item.step_id,
                                      "mode": item.mode, "due_at": item.due_at.isoformat(),
                                      "status": item.status, "payload": item.payload or {}}
                                     for item in actions]}
    if include_portal_url:
        result["portal_url"] = portal_url(journey)
    return result


def trigger_workflow(db: Session, tenant: Tenant, booking: Booking,
                     trigger: str, occurred_at: datetime | None = None) -> None:
    base = occurred_at or utcnow()
    workflows = db.scalars(select(Workflow).where(
        Workflow.tenant_id == tenant.id, Workflow.is_active.is_(True))).all()
    workflow_ids = [item.id for item in workflows]
    if not workflow_ids:
        return
    steps = db.scalars(select(WorkflowStep).where(
        WorkflowStep.tenant_id == tenant.id,
        WorkflowStep.workflow_id.in_(workflow_ids),
        WorkflowStep.trigger_event == trigger)).all()
    journey = booking_journey(db, booking)
    overrides = dict(journey.workflow_controls or {})
    multipliers = {"minutes": 60, "hours": 3600, "days": 86400, "weeks": 604800}
    for step in steps:
        if overrides.get(step.id, {}).get("paused") is True:
            continue
        control = db.scalar(select(WorkflowStepControl).where(
            WorkflowStepControl.step_id == step.id,
            WorkflowStepControl.tenant_id == tenant.id))
        mode = control.mode if control else ("off" if step.is_paused else ("task" if step.action_type == "manual_task" else "review"))
        if mode == "off":
            continue
        seconds = step.offset_value * multipliers.get(step.offset_unit, 86400)
        due = base if step.timing_direction == "immediately" else base + timedelta(
            seconds=(-seconds if step.timing_direction == "before" else seconds))
        resume_status = "review" if mode == "review" else "pending" if mode == "task" else "queued"
        status_value = "paused" if tenant.automations_paused else resume_status
        exists = db.scalar(select(WorkflowAction.id).where(
            WorkflowAction.tenant_id == tenant.id,
            WorkflowAction.booking_id == booking.id,
            WorkflowAction.step_id == step.id,
            WorkflowAction.trigger_key == trigger))
        if exists:
            continue
        db.add(WorkflowAction(tenant_id=tenant.id, booking_id=booking.id,
                              step_id=step.id, trigger_key=trigger, mode=mode,
                              due_at=due, status=status_value,
                              payload={"name": step.name, "action_type": step.action_type,
                                       "subject": step.subject, "message_body": step.message_body,
                                       "task_title": step.task_title,
                                       "resume_status": resume_status}))
        db.flush()


def trigger_enquiry_workflow(db: Session, tenant: Tenant, enquiry: Enquiry) -> None:
    workflows = db.scalars(select(Workflow).where(
        Workflow.tenant_id == tenant.id, Workflow.is_active.is_(True))).all()
    workflow_ids = [item.id for item in workflows]
    if not workflow_ids:
        return
    steps = db.scalars(select(WorkflowStep).where(
        WorkflowStep.tenant_id == tenant.id,
        WorkflowStep.workflow_id.in_(workflow_ids),
        WorkflowStep.trigger_event == "enquiry_received")).all()
    multipliers = {"minutes": 60, "hours": 3600, "days": 86400, "weeks": 604800}
    for step in steps:
        control = db.scalar(select(WorkflowStepControl).where(
            WorkflowStepControl.step_id == step.id,
            WorkflowStepControl.tenant_id == tenant.id))
        mode = control.mode if control else ("off" if step.is_paused else ("task" if step.action_type == "manual_task" else "review"))
        if mode == "off":
            continue
        seconds = step.offset_value * multipliers.get(step.offset_unit, 86400)
        due = enquiry.created_at if step.timing_direction == "immediately" else enquiry.created_at + timedelta(
            seconds=(-seconds if step.timing_direction == "before" else seconds))
        resume_status = "review" if mode == "review" else "pending" if mode == "task" else "queued"
        status_value = "paused" if tenant.automations_paused else resume_status
        db.add(WorkflowAction(tenant_id=tenant.id, enquiry_id=enquiry.id,
                              step_id=step.id, trigger_key="enquiry_received",
                              mode=mode, due_at=due, status=status_value,
                              payload={"name": step.name, "action_type": step.action_type,
                                       "subject": step.subject, "message_body": step.message_body,
                                       "task_title": step.task_title,
                                       "recipient_email": enquiry.email,
                                       "recipient_name": enquiry.first_name,
                                       "resume_status": resume_status}))
        db.flush()


def next_invoice(db: Session, tenant_id: str) -> tuple[int, str]:
    row = db.scalar(select(TenantInvoiceCounter).where(
        TenantInvoiceCounter.tenant_id == tenant_id).with_for_update())
    if not row:
        row = TenantInvoiceCounter(tenant_id=tenant_id, next_sequence=2)
        db.add(row)
        sequence = 1
    else:
        sequence = row.next_sequence
        row.next_sequence += 1
    return sequence, f"INV-{sequence:05d}"


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_SCOPES = "openid email https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.calendarlist.readonly"


def google_configured() -> bool:
    return bool(settings.google_calendar_client_id and settings.google_calendar_client_secret)


def google_redirect_uri() -> str:
    return settings.google_calendar_redirect_uri or f"{settings.studio_url.rstrip('/')}/api/integrations/google-calendar/callback"


def google_access_token(connection: TenantCalendarConnection) -> str:
    try:
        response = httpx.post(GOOGLE_TOKEN_URL, data={
            "client_id": settings.google_calendar_client_id,
            "client_secret": settings.google_calendar_client_secret,
            "refresh_token": decrypt_secret(connection.refresh_token_encrypted),
            "grant_type": "refresh_token",
        }, timeout=settings.google_calendar_timeout_seconds)
        response.raise_for_status()
        token = response.json().get("access_token")
    except Exception as exc:
        raise RuntimeError("Google Calendar authorisation needs attention") from exc
    if not token:
        raise RuntimeError("Google did not return an access token")
    return str(token)


def google_request(method: str, path: str, access_token: str, payload: dict | None = None) -> httpx.Response:
    return httpx.request(method, f"{GOOGLE_CALENDAR_API}{path}",
                         headers={"Authorization": f"Bearer {access_token}"},
                         params={"sendUpdates": "none"}, json=payload,
                         timeout=settings.google_calendar_timeout_seconds)


def booking_calendar_payload(booking: Booking) -> dict:
    if not booking.event_date:
        raise RuntimeError("The wedding date has not been set")
    event_id = f"b{token_hash(booking.id)[:40]}"
    return {"id": event_id, "summary": f"Wedding — {booking.title}",
            "description": f"Couple: {booking.title}\nVenue: {booking.venue or 'To be confirmed'}\n\nManaged by Ivory Digital Booking Studio.",
            "location": booking.venue or "", "start": {"date": booking.event_date.isoformat()},
            "end": {"date": (booking.event_date + timedelta(days=1)).isoformat()},
            "transparency": "opaque", "extendedProperties": {"private": {
                "ivory_booking_id": booking.id, "tenant_id": booking.tenant_id}}}


def sync_booking_calendar_safely(db: Session, tenant: Tenant, booking: Booking,
                                 journey: BookingJourney) -> dict:
    current = dict(journey.calendar_state or {})
    should_exist = bool(booking.event_date and booking.status in {"confirmed", "completed"} and
                        (journey.special_payment_arrangement or db.scalar(select(BookingPayment.id).join(
                            BookingInvoice, BookingInvoice.id == BookingPayment.invoice_id).where(
                            BookingInvoice.tenant_id == tenant.id,
                            BookingInvoice.booking_id == booking.id).limit(1))))
    event_id = current.get("event_id") or f"b{token_hash(booking.id)[:40]}"
    connection = db.get(TenantCalendarConnection, tenant.id)
    desired = "create_or_update" if should_exist else "delete"
    if not google_configured() or not connection or not connection.refresh_token_encrypted:
        state = {**current, "status": "pending", "desired_action": desired,
                 "event_id": event_id, "last_error": "Google Calendar is not connected yet."}
        journey.calendar_state = state
        return state
    try:
        access = google_access_token(connection)
        path = f"/calendars/{quote(connection.calendar_id or 'primary', safe='')}/events/{quote(event_id, safe='')}"
        if should_exist:
            response = google_request("PUT", path, access, booking_calendar_payload(booking))
            if response.status_code >= 400:
                raise RuntimeError("Google Calendar did not accept the wedding event")
            body = response.json()
            state = {"status": "synced", "desired_action": None, "event_id": event_id,
                     "calendar_id": connection.calendar_id, "html_link": body.get("htmlLink"),
                     "last_synced_at": utcnow().isoformat(), "last_error": None}
        else:
            response = google_request("DELETE", path, access)
            if response.status_code not in {204, 404, 410}:
                raise RuntimeError("Google Calendar did not remove the wedding event")
            state = {"status": "removed", "desired_action": None,
                     "removed_event_id": event_id, "last_synced_at": utcnow().isoformat(),
                     "last_error": None}
    except Exception as exc:
        state = {**current, "status": "error", "desired_action": desired,
                 "event_id": event_id, "last_attempt_at": utcnow().isoformat(),
                 "last_error": str(exc)[:500]}
    journey.calendar_state = state
    return state


def sync_date_block_safely(db: Session, tenant: Tenant, block: TenantDateBlock) -> dict:
    current = dict(block.calendar_state or {})
    event_id = current.get("event_id") or f"d{token_hash(block.id)[:40]}"
    connection = db.get(TenantCalendarConnection, tenant.id)
    should_exist = block.archived_at is None
    desired = "create_or_update" if should_exist else "delete"
    if not google_configured() or not connection or not connection.refresh_token_encrypted:
        state = {**current, "status": "pending", "desired_action": desired,
                 "event_id": event_id, "last_error": "Google Calendar is not connected yet."}
        block.calendar_state = state
        return state
    try:
        access = google_access_token(connection)
        path = f"/calendars/{quote(connection.calendar_id or 'primary', safe='')}/events/{quote(event_id, safe='')}"
        if should_exist:
            payload = {"id": event_id, "summary": f"Unavailable — {block.label}",
                       "description": "\n".join(filter(None, [block.notes, "Managed by Ivory Digital Booking Studio."])),
                       "start": {"date": block.start_date.isoformat()},
                       "end": {"date": (block.end_date + timedelta(days=1)).isoformat()},
                       "transparency": "opaque", "extendedProperties": {"private": {"ivory_date_block_id": block.id}}}
            response = google_request("PUT", path, access, payload)
            if response.status_code >= 400:
                raise RuntimeError("Google Calendar did not accept the blocked dates")
            state = {"status": "synced", "desired_action": None, "event_id": event_id,
                     "calendar_id": connection.calendar_id, "html_link": response.json().get("htmlLink"),
                     "last_synced_at": utcnow().isoformat(), "last_error": None}
        else:
            response = google_request("DELETE", path, access)
            if response.status_code not in {204, 404, 410}:
                raise RuntimeError("Google Calendar did not remove the blocked dates")
            state = {"status": "removed", "desired_action": None,
                     "removed_event_id": event_id, "last_synced_at": utcnow().isoformat(), "last_error": None}
    except Exception as exc:
        state = {**current, "status": "error", "desired_action": desired,
                 "event_id": event_id, "last_attempt_at": utcnow().isoformat(),
                 "last_error": str(exc)[:500]}
    block.calendar_state = state
    return state


@app.post("/api/studio/enquiries/{enquiry_id}/convert", status_code=201)
def convert_enquiry(enquiry_id: str, payload: EnquiryConvertIn, request: Request,
                    session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    enquiry = db.scalar(select(Enquiry).where(Enquiry.id == enquiry_id,
                        Enquiry.tenant_id == tenant.id))
    if not enquiry:
        raise HTTPException(404, "Enquiry not found")
    existing_journey = db.scalar(select(BookingJourney).where(
        BookingJourney.tenant_id == tenant.id, BookingJourney.enquiry_id == enquiry.id))
    if existing_journey:
        return journey_json(db, studio_booking(db, tenant.id, existing_journey.booking_id), existing_journey)
    email = normalise_email(enquiry.email)
    client = db.scalar(select(Client).where(Client.tenant_id == tenant.id, Client.email == email))
    if not client:
        client = Client(tenant_id=tenant.id, first_name=enquiry.first_name,
                        partner_name=enquiry.partner_name or None, email=email,
                        phone=enquiry.phone or None)
        db.add(client); db.flush()
    title = payload.title.strip() or " & ".join(filter(None, [enquiry.first_name, enquiry.partner_name]))
    booking = Booking(tenant_id=tenant.id, client_id=client.id, title=title,
                      event_date=enquiry.event_date, venue=enquiry.venue or None,
                      status="quote_preparation")
    db.add(booking); db.flush()
    journey = booking_journey(db, booking); journey.enquiry_id = enquiry.id
    enquiry.status = "converted"
    audit(db, "enquiry_converted", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"enquiry_id": enquiry.id})
    result = journey_json(db, booking, journey)
    db.commit()
    return result


@app.get("/api/studio/bookings")
def list_bookings(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    rows = db.scalars(select(Booking).where(Booking.tenant_id == tenant.id)
                      .order_by(Booking.event_date, Booking.created_at.desc())).all()
    result = []
    for booking in rows:
        journey = booking_journey(db, booking)
        result.append(journey_json(db, booking, journey))
    db.commit()
    return result


@app.get("/api/studio/bookings/{booking_id}/journey")
def get_booking_journey(booking_id: str, context=Depends(studio_context),
                        db: Session = Depends(get_db)):
    _, _, tenant = context
    booking = studio_booking(db, tenant.id, booking_id)
    journey = booking_journey(db, booking)
    result = journey_json(db, booking, journey)
    db.commit()
    return result


def quote_snapshot(db: Session, tenant_id: str, payload: QuoteDraftIn) -> dict:
    package_rows = db.scalars(select(ServicePackage).where(
        ServicePackage.tenant_id == tenant_id,
        ServicePackage.id.in_(payload.package_ids),
        ServicePackage.is_active.is_(True))).all() if payload.package_ids else []
    if len(package_rows) != len(set(payload.package_ids)):
        raise HTTPException(422, "One of the selected packages is no longer available")
    add_on_rows = db.scalars(select(PackageAddOn).where(
        PackageAddOn.tenant_id == tenant_id,
        PackageAddOn.is_active.is_(True))).all()
    allowed_addons = {item.id: item for item in add_on_rows}
    selected_ids = set(payload.add_on_ids)
    selected_ids.update(item.id for item in add_on_rows if item.selection_mode == "mandatory")
    if any(item_id not in allowed_addons for item_id in selected_ids):
        raise HTTPException(422, "One of the selected add-ons is no longer available")
    return {"status": "draft", "packages": [package_json(item) for item in package_rows],
            "add_ons": [add_on_json(allowed_addons[item_id]) for item_id in selected_ids],
            "custom_items": payload.custom_items, "message": payload.message,
            "expires_on": payload.expires_on.isoformat() if payload.expires_on else None,
            "updated_at": utcnow().isoformat()}


@app.put("/api/studio/bookings/{booking_id}/quote")
def save_quote(booking_id: str, payload: QuoteDraftIn, request: Request,
               session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id); journey = booking_journey(db, booking)
    if journey.accepted_quote:
        raise HTTPException(409, "This quote has been accepted and its snapshot cannot be changed")
    journey.quote_state = quote_snapshot(db, tenant.id, payload)
    booking.status = "quote_preparation"
    audit(db, "quote_draft_saved", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    result = journey_json(db, booking, journey)
    db.commit()
    return result


@app.post("/api/studio/bookings/{booking_id}/quote/send")
def send_quote(booking_id: str, request: Request,
               session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id); journey = booking_journey(db, booking)
    quote = dict(journey.quote_state or {})
    if not quote.get("packages"):
        raise HTTPException(422, "Add at least one package before preparing the quote link")
    if journey.accepted_quote:
        raise HTTPException(409, "This quote has already been accepted")
    quote.update({"status": "sent", "sent_at": utcnow().isoformat()})
    journey.quote_state = quote; booking.status = "awaiting_quote_acceptance"
    trigger_workflow(db, tenant, booking, "quote_sent")
    audit(db, "quote_marked_sent", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"delivery": "link_prepared", "automatic_sending_paused": tenant.automations_paused})
    db.commit()
    return {"ok": True, "portal_url": portal_url(journey),
            "delivery": "copy_link", "automatic_email_sent": False}


def public_portal(raw_token: str, db: Session) -> tuple[Tenant, Booking, BookingJourney]:
    # The high-entropy bearer token must be resolved before the tenant is known.
    # Switch immediately to that tenant after the exact hash match.
    set_database_tenant(db, platform_admin=True)
    journey = db.scalar(select(BookingJourney).where(
        BookingJourney.portal_token_hash == token_hash(raw_token)))
    if not journey:
        raise HTTPException(404, "This private client area is not available")
    tenant = db.get(Tenant, journey.tenant_id)
    if not tenant or tenant.status in {TenantStatus.SUSPENDED, TenantStatus.CANCELLED}:
        raise HTTPException(404, "This private client area is not available")
    set_database_tenant(db, tenant.id)
    booking = studio_booking(db, tenant.id, journey.booking_id)
    return tenant, booking, journey


@app.get("/api/public/portal/{raw_token}")
def get_public_portal(raw_token: str, db: Session = Depends(get_db)):
    tenant, booking, journey = public_portal(raw_token, db)
    data = journey_json(db, booking, journey, include_portal_url=False)
    data["business"] = {"display_name": (tenant.branding or {}).get("display_name") or tenant.display_name,
                        "accent_colour": (tenant.branding or {}).get("accent_colour") or "#a9782e",
                        "welcome_message": (tenant.branding or {}).get("welcome_message") or "Welcome to your private booking area."}
    templates = db.scalars(select(QuestionnaireTemplate).where(
        QuestionnaireTemplate.tenant_id == tenant.id,
        QuestionnaireTemplate.is_active.is_(True)).order_by(QuestionnaireTemplate.form_type)).all()
    submitted_types = {item["form_type"] for item in data["questionnaires"]}
    data["available_questionnaires"] = [
        {**questionnaire_json(row), "submitted": row.form_type in submitted_types}
        for row in templates
    ]
    return data


@app.post("/api/public/portal/{raw_token}/quote/accept")
def accept_public_quote(raw_token: str, payload: QuoteAcceptIn, request: Request,
                        db: Session = Depends(get_db)):
    tenant, booking, journey = public_portal(raw_token, db)
    if journey.accepted_quote:
        raise HTTPException(409, "This quote has already been accepted")
    quote = dict(journey.quote_state or {})
    if quote.get("status") != "sent":
        raise HTTPException(409, "This quote is not ready to accept")
    package = next((item for item in quote.get("packages", []) if item["id"] == payload.package_id), None)
    if not package:
        raise HTTPException(422, "Choose one of the available packages")
    offered_addons = {item["id"]: item for item in quote.get("add_ons", [])}
    selected_ids = set(payload.add_on_ids)
    selected_ids.update(item["id"] for item in offered_addons.values() if item["selection_mode"] == "mandatory")
    if any(item_id not in offered_addons for item_id in selected_ids):
        raise HTTPException(422, "Choose only the available extras")
    selected_addons = [offered_addons[item_id] for item_id in selected_ids]
    line_items = [{"kind": "package", "label": package["name"], "price_pence": package["price_pence"]}]
    line_items += [{"kind": "add_on", "label": item["name"], "price_pence": item["price_pence"]} for item in selected_addons]
    line_items += [{"kind": "custom", **item} for item in quote.get("custom_items", [])]
    total = sum(int(item["price_pence"]) for item in line_items)
    accepted = {"accepted_at": utcnow().isoformat(), "accepted_by": payload.client_name,
                "package": package, "add_ons": selected_addons,
                "custom_items": quote.get("custom_items", []), "line_items": line_items,
                "total_pence": total}
    journey.accepted_quote = accepted
    journey.booking_fee_pence = min(int(package.get("booking_fee_pence", 0)), total)
    due_days = int(package.get("balance_due_days", 45))
    journey.balance_due_date = booking.event_date - timedelta(days=due_days) if booking.event_date else None
    sequence, number = next_invoice(db, tenant.id)
    invoice = BookingInvoice(tenant_id=tenant.id, booking_id=booking.id,
                             sequence=sequence, number=number,
                             booking_fee_due_date=date.today() + timedelta(days=1),
                             due_date=journey.balance_due_date, total_pence=total,
                             line_items=line_items,
                             payment_schedule=[{"label": "Booking fee", "amount_pence": journey.booking_fee_pence,
                                                "due_date": (date.today() + timedelta(days=1)).isoformat()},
                                               {"label": "Remaining balance", "amount_pence": max(0, total - journey.booking_fee_pence),
                                                "due_date": journey.balance_due_date.isoformat() if journey.balance_due_date else None}])
    db.add(invoice); db.flush(); booking.status = "quote_accepted"
    trigger_workflow(db, tenant, booking, "quote_accepted")
    booking_fee_day = date.today() + timedelta(days=1)
    trigger_workflow(db, tenant, booking, "booking_fee_due",
                     datetime(booking_fee_day.year, booking_fee_day.month, booking_fee_day.day,
                              9, 0, tzinfo=timezone.utc))
    if journey.balance_due_date:
        balance_day = journey.balance_due_date
        trigger_workflow(db, tenant, booking, "balance_due",
                         datetime(balance_day.year, balance_day.month, balance_day.day,
                                  9, 0, tzinfo=timezone.utc))
    if booking.event_date:
        wedding_day = booking.event_date
        trigger_workflow(db, tenant, booking, "wedding_date",
                         datetime(wedding_day.year, wedding_day.month, wedding_day.day,
                                  9, 0, tzinfo=timezone.utc))
    audit(db, "quote_accepted", "booking", booking.id, tenant_id=tenant.id,
          request=request, detail={"invoice_number": number, "total_pence": total})
    result = invoice_json(invoice, db)
    db.commit()
    return {"ok": True, "invoice": result,
            "message": "Your package has been accepted safely."}


@app.post("/api/studio/bookings/{booking_id}/quote/amend", status_code=201)
def amend_accepted_quote(booking_id: str, payload: QuoteAmendmentIn, request: Request,
                         session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id); journey = booking_journey(db, booking)
    if not journey.accepted_quote:
        raise HTTPException(409, "The couple has not accepted a quote yet")
    invoice = db.scalar(select(BookingInvoice).where(
        BookingInvoice.tenant_id == tenant.id,
        BookingInvoice.booking_id == booking.id,
        BookingInvoice.status != "void").order_by(BookingInvoice.created_at.desc()))
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    if invoice.status == "paid" or invoice.paid_pence >= invoice.total_pence:
        raise HTTPException(409, "This booking is paid in full, so its quote and invoice are locked")
    before = dict(journey.accepted_quote or {})
    after = dict(before)
    items = list(before.get("line_items") or [])
    items.append({"kind": "amendment", "label": payload.label.strip(),
                  "price_pence": payload.price_pence})
    after["line_items"] = items
    after["total_pence"] = sum(int(item.get("price_pence", 0)) for item in items)
    after["last_amended_at"] = utcnow().isoformat()
    journey.accepted_quote = after
    invoice.line_items = items; invoice.total_pence = after["total_pence"]
    invoice.status = "part_paid" if invoice.paid_pence else "unpaid"
    schedule = list(invoice.payment_schedule or [])
    if schedule:
        schedule[-1] = {**schedule[-1],
                        "amount_pence": max(0, invoice.total_pence - journey.booking_fee_pence)}
        invoice.payment_schedule = schedule
    revision = BookingQuoteRevision(tenant_id=tenant.id, booking_id=booking.id,
                                    previous_snapshot=before, updated_snapshot=after,
                                    reason=payload.reason.strip(), actor_user_id=session.user.id)
    db.add(revision); db.flush()
    audit(db, "accepted_quote_amended", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"label": payload.label, "price_pence": payload.price_pence,
                  "reason": payload.reason, "invoice_number": invoice.number})
    result = journey_json(db, booking, journey)
    db.commit()
    return result


@app.post("/api/studio/invoices/{invoice_id}/payments", status_code=201)
def record_payment(invoice_id: str, payload: PaymentRecordIn, request: Request,
                   session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    invoice = db.scalar(select(BookingInvoice).where(
        BookingInvoice.id == invoice_id, BookingInvoice.tenant_id == tenant.id))
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    if invoice.status == "void":
        raise HTTPException(409, "A void invoice cannot receive a payment")
    outstanding = invoice.total_pence - invoice.paid_pence
    if payload.amount_pence > outstanding:
        raise HTTPException(422, "The payment is more than the outstanding invoice balance")
    payment = BookingPayment(tenant_id=tenant.id, invoice_id=invoice.id, **payload.model_dump())
    db.add(payment); invoice.paid_pence += payload.amount_pence
    invoice.status = "paid" if invoice.paid_pence >= invoice.total_pence else "part_paid"
    booking = studio_booking(db, tenant.id, invoice.booking_id)
    journey = booking_journey(db, booking)
    previous_status = booking.status
    if invoice.paid_pence >= min(journey.booking_fee_pence, invoice.total_pence):
        booking.status = "confirmed"
        state = dict(journey.calendar_state or {})
        state.update({"status": "pending", "desired_action": "create_or_update",
                      "reason": "booking_secured", "last_error": "Google Calendar is not connected yet."})
        journey.calendar_state = state
        if previous_status != "confirmed":
            trigger_workflow(db, tenant, booking, "booking_fee_paid")
    if invoice.status == "paid":
        trigger_workflow(db, tenant, booking, "balance_paid")
    sync_booking_calendar_safely(db, tenant, booking, journey)
    audit(db, "payment_recorded", "invoice", invoice.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"amount_pence": payload.amount_pence, "payment_type": payload.payment_type})
    result = invoice_json(invoice, db)
    db.commit()
    return result


@app.post("/api/studio/invoices/{invoice_id}/void")
def void_invoice(invoice_id: str, payload: InvoiceVoidIn, request: Request,
                 session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    invoice = db.scalar(select(BookingInvoice).where(
        BookingInvoice.id == invoice_id, BookingInvoice.tenant_id == tenant.id))
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    if invoice.paid_pence:
        raise HTTPException(409, "A paid or part-paid invoice must be corrected with an auditable refund or credit, not deleted")
    invoice.status = "void"; invoice.void_reason = payload.reason
    audit(db, "invoice_voided", "invoice", invoice.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"reason": payload.reason})
    result = invoice_json(invoice, db)
    db.commit()
    return result


@app.put("/api/studio/bookings/{booking_id}/payment-arrangement")
def set_payment_arrangement(booking_id: str, payload: SpecialPaymentIn, request: Request,
                            session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id); journey = booking_journey(db, booking)
    if payload.enabled and not payload.note.strip():
        raise HTTPException(422, "Add a note explaining the agreed payment arrangement")
    journey.special_payment_arrangement = payload.enabled
    journey.special_payment_note = payload.note.strip()
    if payload.enabled:
        booking.status = "confirmed"
        journey.calendar_state = {**(journey.calendar_state or {}), "status": "pending",
                                  "desired_action": "create_or_update",
                                  "reason": "special_payment_arrangement",
                                  "last_error": "Google Calendar is not connected yet."}
    sync_booking_calendar_safely(db, tenant, booking, journey)
    audit(db, "special_payment_arrangement_updated", "booking", booking.id,
          actor=session.user, tenant_id=tenant.id, request=request,
          detail={"enabled": payload.enabled, "note": payload.note})
    result = journey_json(db, booking, journey)
    db.commit()
    return result


@app.get("/api/studio/contract-templates")
def list_contract_templates(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    rows = db.scalars(select(TenantContractTemplate).where(
        TenantContractTemplate.tenant_id == tenant.id).order_by(
        TenantContractTemplate.updated_at.desc())).all()
    return [{"id": row.id, "name": row.name, "version": row.version,
             "body": row.body, "is_active": row.is_active} for row in rows]


@app.post("/api/studio/contract-templates", status_code=201)
def create_contract_template(payload: ContractTemplateIn, request: Request,
                             session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = TenantContractTemplate(tenant_id=tenant.id, **payload.model_dump())
    db.add(row); db.flush()
    audit(db, "contract_template_created", "contract_template", row.id,
          actor=session.user, tenant_id=tenant.id, request=request)
    db.commit()
    return {"id": row.id, "name": row.name, "version": row.version,
            "body": row.body, "is_active": row.is_active}


@app.put("/api/studio/contract-templates/{template_id}")
def update_contract_template(template_id: str, payload: ContractTemplateIn, request: Request,
                             session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(TenantContractTemplate).where(
        TenantContractTemplate.id == template_id,
        TenantContractTemplate.tenant_id == tenant.id))
    if not row:
        raise HTTPException(404, "Contract template not found")
    row.name = payload.name; row.body = payload.body; row.is_active = payload.is_active; row.version += 1
    audit(db, "contract_template_updated", "contract_template", row.id,
          actor=session.user, tenant_id=tenant.id, request=request,
          detail={"version": row.version})
    db.commit()
    return {"id": row.id, "name": row.name, "version": row.version,
            "body": row.body, "is_active": row.is_active}


@app.post("/api/studio/bookings/{booking_id}/contract")
def issue_contract(booking_id: str, payload: ContractIssueIn, request: Request,
                   session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id); booking_journey(db, booking)
    template = db.scalar(select(TenantContractTemplate).where(
        TenantContractTemplate.id == payload.template_id,
        TenantContractTemplate.tenant_id == tenant.id,
        TenantContractTemplate.is_active.is_(True)))
    if not template:
        raise HTTPException(404, "Contract template not found")
    existing = db.scalar(select(BookingContract).where(
        BookingContract.tenant_id == tenant.id, BookingContract.booking_id == booking.id))
    if existing and (existing.client_signed_at or existing.supplier_signed_at):
        raise HTTPException(409, "A signed contract is an immutable record and cannot be replaced")
    if existing:
        existing.template_id = template.id; existing.title = template.name
        existing.version = template.version; existing.body_snapshot = template.body
        row = existing
    else:
        row = BookingContract(tenant_id=tenant.id, booking_id=booking.id,
                              template_id=template.id, title=template.name,
                              version=template.version, body_snapshot=template.body)
        db.add(row)
    audit(db, "contract_issued", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"template_id": template.id, "version": template.version})
    db.commit()
    return contract_json(row)


@app.post("/api/public/portal/{raw_token}/contract/sign")
def sign_public_contract(raw_token: str, payload: ContractSignIn, request: Request,
                         db: Session = Depends(get_db)):
    tenant, booking, journey = public_portal(raw_token, db)
    if not payload.agreed:
        raise HTTPException(422, "Please confirm that you agree before signing")
    row = db.scalar(select(BookingContract).where(
        BookingContract.tenant_id == tenant.id, BookingContract.booking_id == booking.id))
    if not row:
        raise HTTPException(404, "Your contract is not ready yet")
    if row.client_signed_at:
        raise HTTPException(409, "This contract has already been signed")
    row.client_name = payload.full_name.strip(); row.client_signed_at = utcnow()
    row.client_ip = client_ip(request) or ""
    trigger_workflow(db, tenant, booking, "contract_signed")
    trigger_workflow(db, tenant, booking, "agreement_signed")
    audit(db, "contract_client_signed", "contract", row.id, tenant_id=tenant.id,
          request=request, detail={"signatory": row.client_name})
    db.commit()
    return contract_json(row)


@app.post("/api/studio/bookings/{booking_id}/contract/countersign")
def countersign_contract(booking_id: str, payload: ContractSignIn, request: Request,
                         session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id)
    row = db.scalar(select(BookingContract).where(
        BookingContract.tenant_id == tenant.id, BookingContract.booking_id == booking.id))
    if not row or not row.client_signed_at:
        raise HTTPException(409, "The couple must sign before you countersign")
    if not payload.agreed:
        raise HTTPException(422, "Confirm the agreement before countersigning")
    if row.supplier_signed_at:
        raise HTTPException(409, "This contract has already been countersigned")
    row.supplier_name = payload.full_name.strip(); row.supplier_signed_at = utcnow()
    audit(db, "contract_supplier_signed", "contract", row.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"signatory": row.supplier_name})
    db.commit()
    return contract_json(row)


@app.get("/api/studio/questionnaire-templates")
def list_questionnaire_templates(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    rows = db.scalars(select(QuestionnaireTemplate).where(
        QuestionnaireTemplate.tenant_id == tenant.id).order_by(QuestionnaireTemplate.form_type)).all()
    return [questionnaire_json(row) for row in rows]


@app.put("/api/studio/questionnaire-templates/{form_type}")
def save_questionnaire_template(form_type: str, payload: QuestionnaireTemplateIn,
                                request: Request, session: UserSession = Depends(require_csrf),
                                db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    if form_type != payload.form_type:
        raise HTTPException(422, "Questionnaire type does not match")
    row = db.scalar(select(QuestionnaireTemplate).where(
        QuestionnaireTemplate.tenant_id == tenant.id,
        QuestionnaireTemplate.form_type == form_type))
    if not row:
        row = QuestionnaireTemplate(tenant_id=tenant.id, **payload.model_dump()); db.add(row)
    else:
        for key, value in payload.model_dump().items(): setattr(row, key, value)
    db.flush()
    audit(db, "questionnaire_template_saved", "questionnaire_template", row.id,
          actor=session.user, tenant_id=tenant.id, request=request,
          detail={"form_type": form_type})
    db.commit()
    return questionnaire_json(row)


@app.post("/api/public/portal/{raw_token}/questionnaires/{form_type}")
def submit_questionnaire(raw_token: str, form_type: str,
                         payload: QuestionnaireSubmitIn, request: Request,
                         db: Session = Depends(get_db)):
    tenant, booking, journey = public_portal(raw_token, db)
    template = db.scalar(select(QuestionnaireTemplate).where(
        QuestionnaireTemplate.tenant_id == tenant.id,
        QuestionnaireTemplate.form_type == form_type,
        QuestionnaireTemplate.is_active.is_(True)))
    if not template:
        raise HTTPException(404, "This form is not ready yet")
    for question in template.questions or []:
        if question.get("required") and not str(payload.answers.get(question["id"], "")).strip():
            raise HTTPException(422, f"Please answer: {question['label']}")
    row = db.scalar(select(QuestionnaireSubmission).where(
        QuestionnaireSubmission.tenant_id == tenant.id,
        QuestionnaireSubmission.booking_id == booking.id,
        QuestionnaireSubmission.form_type == form_type))
    snapshot = {"name": template.name, "introduction": template.introduction,
                "questions": template.questions or []}
    if row:
        row.template_snapshot = snapshot; row.answers = payload.answers; row.submitted_at = utcnow()
    else:
        row = QuestionnaireSubmission(tenant_id=tenant.id, booking_id=booking.id,
                                      form_type=form_type, template_snapshot=snapshot,
                                      answers=payload.answers)
        db.add(row)
    trigger_workflow(db, tenant, booking, "questionnaire_submitted")
    if form_type == "booking":
        trigger_workflow(db, tenant, booking, "booking_form_received")
    audit(db, "questionnaire_submitted", "booking", booking.id,
          tenant_id=tenant.id, request=request, detail={"form_type": form_type})
    db.commit()
    return {"ok": True, "form_type": form_type, "submitted_at": row.submitted_at.isoformat()}


def safe_document_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return clean[:100] or "document"


def pdf_response(story: list, filename: str) -> Response:
    stream = io.BytesIO()
    document = SimpleDocTemplate(stream, pagesize=A4, rightMargin=18 * mm,
                                 leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                                 title=filename)
    document.build(story)
    return Response(stream.getvalue(), media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{safe_document_name(filename)}.pdf"',
        "Cache-Control": "private, no-store",
    })


def invoice_pdf(tenant: Tenant, booking: Booking, invoice: BookingInvoice, db: Session) -> Response:
    styles = getSampleStyleSheet(); normal = styles["BodyText"]
    story = [Paragraph(html.escape(tenant.display_name), styles["Title"]),
             Paragraph(f"Invoice {html.escape(invoice.number)}", styles["Heading1"]),
             Spacer(1, 5 * mm),
             Paragraph(f"For: {html.escape(booking.title)}", normal),
             Paragraph(f"Wedding date: {booking.event_date.strftime('%A %d %B %Y') if booking.event_date else 'To be confirmed'}", normal),
             Paragraph(f"Venue: {html.escape(booking.venue or 'To be confirmed')}", normal),
             Spacer(1, 5 * mm)]
    rows = [["Description", "Amount"]] + [[html.escape(str(item.get("label", "Item"))),
                                              f"£{int(item.get('price_pence', 0))/100:,.2f}"]
                                             for item in invoice.line_items or []]
    rows += [["Invoice total", f"£{invoice.total_pence/100:,.2f}"],
             ["Paid", f"£{invoice.paid_pence/100:,.2f}"],
             ["Outstanding", f"£{max(0, invoice.total_pence-invoice.paid_pence)/100:,.2f}"]]
    table = Table(rows, colWidths=[130 * mm, 35 * mm])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d2926")),
                               ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                               ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                               ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
                               ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#deded8")),
                               ("PADDING", (0, 0), (-1, -1), 8)]))
    story += [table, Spacer(1, 6 * mm),
              Paragraph(f"Status: {html.escape(invoice.status.replace('_', ' ').title())}", styles["Heading3"]),
              Paragraph("This invoice and its payment history are held securely in the photographer's Ivory Digital Booking Studio.", normal)]
    return pdf_response(story, f"{booking.title}-{booking.event_date or 'date-tbc'}-{invoice.number}")


def contract_pdf(tenant: Tenant, booking: Booking, contract: BookingContract) -> Response:
    styles = getSampleStyleSheet(); normal = styles["BodyText"]
    body = [Paragraph(html.escape(line) or "&nbsp;", normal) for line in contract.body_snapshot.splitlines()]
    story = [Paragraph(html.escape(tenant.display_name), styles["Title"]),
             Paragraph(html.escape(contract.title), styles["Heading1"]),
             Paragraph(f"For {html.escape(booking.title)} · version {contract.version}", normal),
             Spacer(1, 6 * mm), *body, Spacer(1, 8 * mm),
             Paragraph(f"Couple signature: {html.escape(contract.client_name or 'Not yet signed')}"
                       f"{(' — '+contract.client_signed_at.strftime('%d %B %Y, %H:%M UTC')) if contract.client_signed_at else ''}", styles["Heading3"]),
             Paragraph(f"Supplier signature: {html.escape(contract.supplier_name or 'Not yet countersigned')}"
                       f"{(' — '+contract.supplier_signed_at.strftime('%d %B %Y, %H:%M UTC')) if contract.supplier_signed_at else ''}", styles["Heading3"])]
    return pdf_response(story, f"{booking.title}-{booking.event_date or 'date-tbc'}-agreement")


def questionnaire_pdf(tenant: Tenant, booking: Booking,
                      submission: QuestionnaireSubmission) -> Response:
    styles = getSampleStyleSheet(); normal = styles["BodyText"]
    snapshot = submission.template_snapshot or {}
    story = [Paragraph(html.escape(tenant.display_name), styles["Title"]),
             Paragraph(html.escape(snapshot.get("name") or submission.form_type.replace("_", " ").title()), styles["Heading1"]),
             Paragraph(f"{html.escape(booking.title)} · {booking.event_date.strftime('%A %d %B %Y') if booking.event_date else 'Date to be confirmed'}", normal),
             Spacer(1, 5 * mm)]
    labels = {item.get("id"): item.get("label") for item in snapshot.get("questions", [])}
    rows = [["Question", "Answer"]] + [[html.escape(str(labels.get(key, key))),
                                          Paragraph(html.escape(str(value)).replace("\n", "<br/>"), normal)]
                                         for key, value in (submission.answers or {}).items()]
    table = Table(rows, colWidths=[65 * mm, 100 * mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d2926")),
                               ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                               ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#deded8")),
                               ("PADDING", (0, 0), (-1, -1), 8)]))
    story.append(table)
    return pdf_response(story, f"{booking.title}-{booking.event_date or 'date-tbc'}-{submission.form_type}")


@app.get("/api/studio/invoices/{invoice_id}/pdf")
def download_studio_invoice(invoice_id: str, context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    invoice = db.scalar(select(BookingInvoice).where(
        BookingInvoice.id == invoice_id, BookingInvoice.tenant_id == tenant.id))
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    return invoice_pdf(tenant, studio_booking(db, tenant.id, invoice.booking_id), invoice, db)


@app.get("/api/public/portal/{raw_token}/invoices/{invoice_id}/pdf")
def download_public_invoice(raw_token: str, invoice_id: str, db: Session = Depends(get_db)):
    tenant, booking, journey = public_portal(raw_token, db)
    invoice = db.scalar(select(BookingInvoice).where(
        BookingInvoice.id == invoice_id, BookingInvoice.tenant_id == tenant.id,
        BookingInvoice.booking_id == booking.id))
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    return invoice_pdf(tenant, booking, invoice, db)


@app.get("/api/studio/bookings/{booking_id}/contract/pdf")
def download_studio_contract(booking_id: str, context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context; booking = studio_booking(db, tenant.id, booking_id)
    contract = db.scalar(select(BookingContract).where(
        BookingContract.tenant_id == tenant.id, BookingContract.booking_id == booking.id))
    if not contract:
        raise HTTPException(404, "Contract not found")
    return contract_pdf(tenant, booking, contract)


@app.get("/api/public/portal/{raw_token}/contract/pdf")
def download_public_contract(raw_token: str, db: Session = Depends(get_db)):
    tenant, booking, journey = public_portal(raw_token, db)
    contract = db.scalar(select(BookingContract).where(
        BookingContract.tenant_id == tenant.id, BookingContract.booking_id == booking.id))
    if not contract:
        raise HTTPException(404, "Contract not found")
    return contract_pdf(tenant, booking, contract)


@app.get("/api/studio/bookings/{booking_id}/questionnaires/{form_type}/pdf")
def download_questionnaire(booking_id: str, form_type: str,
                           context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context; booking = studio_booking(db, tenant.id, booking_id)
    submission = db.scalar(select(QuestionnaireSubmission).where(
        QuestionnaireSubmission.tenant_id == tenant.id,
        QuestionnaireSubmission.booking_id == booking.id,
        QuestionnaireSubmission.form_type == form_type))
    if not submission:
        raise HTTPException(404, "Completed form not found")
    return questionnaire_pdf(tenant, booking, submission)


@app.get("/api/studio/calendar")
def calendar_status(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    connection = db.get(TenantCalendarConnection, tenant.id)
    blocks = db.scalars(select(TenantDateBlock).where(
        TenantDateBlock.tenant_id == tenant.id,
        TenantDateBlock.archived_at.is_(None)).order_by(TenantDateBlock.start_date)).all()
    return {"platform_configured": google_configured(),
            "connected": bool(connection and connection.refresh_token_encrypted),
            "google_account_email": connection.google_account_email if connection else "",
            "calendar_id": connection.calendar_id if connection else "primary",
            "calendar_name": connection.calendar_name if connection else "Primary calendar",
            "last_error": connection.last_error if connection else "",
            "blocks": [{"id": row.id, "start_date": row.start_date.isoformat(),
                        "end_date": row.end_date.isoformat(), "label": row.label,
                        "notes": row.notes, "calendar": row.calendar_state or {}}
                       for row in blocks]}


@app.post("/api/studio/calendar/connect")
def start_google_calendar_connect(request: Request,
                                  session: UserSession = Depends(require_csrf),
                                  db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    if not google_configured():
        raise HTTPException(409, "Ivory Digital has not enabled the Google Calendar connection yet")
    raw_state = opaque_token(32)
    db.add(TenantCalendarOAuthState(state_hash=token_hash(raw_state), tenant_id=tenant.id,
                                    user_id=session.user.id,
                                    expires_at=utcnow() + timedelta(minutes=15)))
    audit(db, "google_calendar_connect_started", "tenant", tenant.id,
          actor=session.user, tenant_id=tenant.id, request=request)
    db.commit()
    url = f"{GOOGLE_AUTH_URL}?{urlencode({'client_id': settings.google_calendar_client_id, 'redirect_uri': google_redirect_uri(), 'response_type': 'code', 'scope': GOOGLE_SCOPES, 'access_type': 'offline', 'prompt': 'consent', 'include_granted_scopes': 'true', 'state': raw_state})}"
    return {"authorization_url": url}


@app.get("/api/integrations/google-calendar/callback")
def google_calendar_callback(code: str = "", state: str = "", error: str = "",
                             db: Session = Depends(get_db)):
    set_database_tenant(db, platform_admin=True)
    oauth_state = db.scalar(select(TenantCalendarOAuthState).where(
        TenantCalendarOAuthState.state_hash == token_hash(state))) if state else None
    if (not oauth_state or oauth_state.used_at or aware(oauth_state.expires_at) <= utcnow() or error or not code):
        return RedirectResponse(f"{settings.studio_url.rstrip('/')}?google_calendar=error", status_code=303)
    tenant_id = oauth_state.tenant_id
    oauth_state.used_at = utcnow(); db.flush(); set_database_tenant(db, tenant_id)
    try:
        response = httpx.post(GOOGLE_TOKEN_URL, data={
            "client_id": settings.google_calendar_client_id,
            "client_secret": settings.google_calendar_client_secret,
            "code": code, "grant_type": "authorization_code",
            "redirect_uri": google_redirect_uri(),
        }, timeout=settings.google_calendar_timeout_seconds)
        response.raise_for_status(); tokens = response.json()
        refresh_token = tokens.get("refresh_token")
        access_token = tokens.get("access_token")
        if not refresh_token or not access_token:
            raise RuntimeError("Google did not return the required calendar permission")
        info = httpx.get(GOOGLE_USERINFO_URL,
                         headers={"Authorization": f"Bearer {access_token}"},
                         timeout=settings.google_calendar_timeout_seconds)
        email = info.json().get("email", "") if info.status_code < 400 else ""
        connection = db.get(TenantCalendarConnection, tenant_id)
        if not connection:
            connection = TenantCalendarConnection(tenant_id=tenant_id); db.add(connection)
        connection.google_account_email = str(email)[:254]
        connection.refresh_token_encrypted = encrypt_secret(str(refresh_token))
        connection.scope = str(tokens.get("scope") or GOOGLE_SCOPES)
        connection.connected_at = utcnow(); connection.last_error = ""
        audit(db, "google_calendar_connected", "tenant", tenant_id,
              tenant_id=tenant_id, detail={"google_account_email": email})
        db.commit()
        return RedirectResponse(f"{settings.studio_url.rstrip('/')}?google_calendar=connected", status_code=303)
    except Exception as exc:
        connection = db.get(TenantCalendarConnection, tenant_id)
        if connection:
            connection.last_error = str(exc)[:500]
        db.commit()
        return RedirectResponse(f"{settings.studio_url.rstrip('/')}?google_calendar=error", status_code=303)


@app.get("/api/studio/calendar/calendars")
def google_calendar_list(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    connection = db.get(TenantCalendarConnection, tenant.id)
    if not connection or not connection.refresh_token_encrypted:
        raise HTTPException(409, "Connect Google Calendar first")
    try:
        response = google_request("GET", "/users/me/calendarList", google_access_token(connection))
        response.raise_for_status()
    except Exception as exc:
        raise HTTPException(422, "Google Calendar could not be reached. Reconnect and try again") from exc
    return [{"id": item.get("id"), "name": item.get("summary") or item.get("id"),
             "primary": bool(item.get("primary")), "access_role": item.get("accessRole")}
            for item in response.json().get("items", []) if item.get("accessRole") in {"owner", "writer"}]


@app.put("/api/studio/calendar/settings")
def save_calendar_settings(payload: CalendarSettingsIn, request: Request,
                           session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    connection = db.get(TenantCalendarConnection, tenant.id)
    if not connection or not connection.refresh_token_encrypted:
        raise HTTPException(409, "Connect Google Calendar first")
    connection.calendar_id = payload.calendar_id; connection.calendar_name = payload.calendar_name
    mark_onboarding(tenant, "calendar")
    audit(db, "google_calendar_selected", "tenant", tenant.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"calendar_id": payload.calendar_id, "calendar_name": payload.calendar_name})
    db.commit()
    return {"ok": True}


@app.post("/api/studio/calendar/disconnect")
def disconnect_calendar(request: Request, session: UserSession = Depends(require_csrf),
                        db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    connection = db.get(TenantCalendarConnection, tenant.id)
    if connection:
        db.delete(connection)
    audit(db, "google_calendar_disconnected", "tenant", tenant.id,
          actor=session.user, tenant_id=tenant.id, request=request)
    db.commit()
    return {"ok": True}


@app.post("/api/studio/calendar/sync")
def sync_all_calendar(request: Request, session: UserSession = Depends(require_csrf),
                      db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    results = []
    bookings = db.scalars(select(Booking).where(Booking.tenant_id == tenant.id)).all()
    for booking in bookings:
        results.append(sync_booking_calendar_safely(db, tenant, booking, booking_journey(db, booking)))
    blocks = db.scalars(select(TenantDateBlock).where(TenantDateBlock.tenant_id == tenant.id)).all()
    for block in blocks:
        results.append(sync_date_block_safely(db, tenant, block))
    audit(db, "google_calendar_sync_requested", "tenant", tenant.id,
          actor=session.user, tenant_id=tenant.id, request=request,
          detail={"records": len(results)})
    db.commit()
    return {"ok": True, "results": results}


@app.post("/api/studio/date-blocks", status_code=201)
def create_date_block(payload: DateBlockIn, request: Request,
                      session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    if payload.end_date < payload.start_date:
        raise HTTPException(422, "The end date cannot be before the start date")
    block = TenantDateBlock(tenant_id=tenant.id, **payload.model_dump())
    db.add(block); db.flush(); sync_date_block_safely(db, tenant, block)
    audit(db, "date_block_created", "date_block", block.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"start_date": payload.start_date.isoformat(), "end_date": payload.end_date.isoformat()})
    db.commit()
    return {"id": block.id, "start_date": block.start_date.isoformat(),
            "end_date": block.end_date.isoformat(), "label": block.label,
            "notes": block.notes, "calendar": block.calendar_state or {}}


@app.delete("/api/studio/date-blocks/{block_id}")
def archive_date_block(block_id: str, request: Request,
                       session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    block = db.scalar(select(TenantDateBlock).where(
        TenantDateBlock.id == block_id, TenantDateBlock.tenant_id == tenant.id))
    if not block:
        raise HTTPException(404, "Date block not found")
    block.archived_at = utcnow(); sync_date_block_safely(db, tenant, block)
    audit(db, "date_block_archived", "date_block", block.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit()
    return {"ok": True, "calendar": block.calendar_state or {}}


@app.put("/api/studio/workflow-steps/{step_id}/mode")
def set_workflow_mode(step_id: str, payload: WorkflowModeIn, request: Request,
                      session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    step = db.scalar(select(WorkflowStep).where(
        WorkflowStep.id == step_id, WorkflowStep.tenant_id == tenant.id))
    if not step:
        raise HTTPException(404, "Workflow step not found")
    row = db.scalar(select(WorkflowStepControl).where(
        WorkflowStepControl.step_id == step.id,
        WorkflowStepControl.tenant_id == tenant.id))
    if not row:
        row = WorkflowStepControl(step_id=step.id, tenant_id=tenant.id); db.add(row)
    row.mode = payload.mode; row.apply_to_existing = payload.apply_to_existing
    step.is_paused = payload.mode == "off"
    if payload.apply_to_existing:
        actions = db.scalars(select(WorkflowAction).where(
            WorkflowAction.tenant_id == tenant.id,
            WorkflowAction.step_id == step.id,
            WorkflowAction.completed_at.is_(None))).all()
        for action in actions:
            action.mode = payload.mode
            action.status = "cancelled" if payload.mode == "off" else (
                "paused" if tenant.automations_paused else "review" if payload.mode == "review" else "pending" if payload.mode == "task" else "queued")
    audit(db, "workflow_mode_updated", "workflow_step", step.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"mode": payload.mode, "apply_to_existing": payload.apply_to_existing})
    db.commit()
    return {"step_id": step.id, "mode": row.mode, "apply_to_existing": row.apply_to_existing}


@app.get("/api/studio/workflow-actions")
def list_workflow_actions(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    rows = db.scalars(select(WorkflowAction).where(
        WorkflowAction.tenant_id == tenant.id).order_by(
        WorkflowAction.completed_at.is_(None).desc(), WorkflowAction.due_at)).all()
    return [{"id": row.id, "booking_id": row.booking_id, "enquiry_id": row.enquiry_id,
             "step_id": row.step_id, "trigger": row.trigger_key, "mode": row.mode,
             "due_at": row.due_at.isoformat(), "status": row.status,
             "payload": row.payload or {},
             "completed_at": row.completed_at.isoformat() if row.completed_at else None}
            for row in rows]


@app.post("/api/studio/workflow-actions/{action_id}/approve")
def approve_workflow_action(action_id: str, request: Request,
                            session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(WorkflowAction).where(
        WorkflowAction.id == action_id, WorkflowAction.tenant_id == tenant.id))
    if not row:
        raise HTTPException(404, "Workflow item not found")
    if row.status not in {"review", "paused", "error"}:
        raise HTTPException(409, "This workflow item is not waiting for approval")
    details = dict(row.payload or {}); details["approved_at"] = utcnow().isoformat(); details["resume_status"] = "queued"
    row.payload = details; row.status = "paused" if tenant.automations_paused else "queued"
    audit(db, "workflow_action_approved", "workflow_action", row.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit()
    return {"ok": True, "status": row.status}


@app.post("/api/studio/workflow-actions/{action_id}/complete")
def complete_workflow_action(action_id: str, request: Request,
                             session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(WorkflowAction).where(
        WorkflowAction.id == action_id, WorkflowAction.tenant_id == tenant.id))
    if not row:
        raise HTTPException(404, "Workflow item not found")
    if row.status in {"sent", "completed", "cancelled"}:
        raise HTTPException(409, "This workflow item is already closed")
    row.status = "completed"; row.completed_at = utcnow()
    audit(db, "workflow_action_completed", "workflow_action", row.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit()
    return {"ok": True, "status": row.status}


@app.put("/api/studio/bookings/{booking_id}/workflow-control")
def set_booking_workflow_control(booking_id: str, payload: WorkflowBookingControlIn,
                                 request: Request, session: UserSession = Depends(require_csrf),
                                 db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id); journey = booking_journey(db, booking)
    step = db.scalar(select(WorkflowStep).where(
        WorkflowStep.id == payload.step_id, WorkflowStep.tenant_id == tenant.id))
    if not step:
        raise HTTPException(404, "Workflow step not found")
    controls = dict(journey.workflow_controls or {})
    controls[step.id] = {"paused": payload.paused, "updated_at": utcnow().isoformat()}
    journey.workflow_controls = controls
    actions = db.scalars(select(WorkflowAction).where(
        WorkflowAction.tenant_id == tenant.id, WorkflowAction.booking_id == booking.id,
        WorkflowAction.step_id == step.id, WorkflowAction.completed_at.is_(None))).all()
    for action in actions:
        action.status = "paused" if payload.paused else (
            "paused" if tenant.automations_paused else "review" if action.mode == "review" else "pending" if action.mode == "task" else "queued")
    audit(db, "booking_workflow_control_updated", "booking", booking.id,
          actor=session.user, tenant_id=tenant.id, request=request,
          detail={"step_id": step.id, "paused": payload.paused})
    db.commit()
    return {"ok": True, "workflow_controls": controls}


@app.post("/api/studio/bookings/{booking_id}/complete")
def complete_booking(booking_id: str, payload: BookingCompleteIn, request: Request,
                     session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id); journey = booking_journey(db, booking)
    journey.completed_at = utcnow() if payload.completed else None
    booking.status = "completed" if payload.completed else "confirmed"
    if payload.completed:
        trigger_workflow(db, tenant, booking, "wedding_completed")
    sync_booking_calendar_safely(db, tenant, booking, journey)
    audit(db, "booking_completed" if payload.completed else "booking_reopened", "booking", booking.id,
          actor=session.user, tenant_id=tenant.id, request=request)
    result = journey_json(db, booking, journey)
    db.commit()
    return result


@app.post("/api/studio/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: str, payload: BookingCancelIn, request: Request,
                   session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id); journey = booking_journey(db, booking)
    if booking.status == "completed":
        raise HTTPException(409, "Reopen the completed wedding before cancelling it")
    booking.status = "cancelled"; journey.cancelled_at = utcnow()
    actions = db.scalars(select(WorkflowAction).where(
        WorkflowAction.tenant_id == tenant.id, WorkflowAction.booking_id == booking.id,
        WorkflowAction.completed_at.is_(None))).all()
    for action in actions:
        action.status = "cancelled"; action.completed_at = utcnow()
    sync_booking_calendar_safely(db, tenant, booking, journey)
    audit(db, "booking_cancelled", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"reason": payload.reason})
    result = journey_json(db, booking, journey)
    db.commit()
    return result


@app.get("/api/public/business/{slug}")
def public_business(slug: str, db: Session = Depends(get_db)):
    row = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if not row or row.status in {TenantStatus.SUSPENDED, TenantStatus.CANCELLED}:
        raise HTTPException(404, "This private client area is not available")
    if row.status == TenantStatus.TRIAL and aware(row.trial_ends_at) <= utcnow():
        raise HTTPException(404, "This private client area is not available")
    branding = row.branding or {}
    return {
        "slug": row.slug,
        "display_name": branding.get("display_name") or row.display_name,
        "accent_colour": branding.get("accent_colour") or "#a9782e",
        "welcome_message": branding.get("welcome_message") or "Welcome to your private booking area.",
        "portal_status": "journey_ready",
    }


def public_tenant(slug: str, db: Session) -> Tenant:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if not tenant or tenant.status in {TenantStatus.SUSPENDED, TenantStatus.CANCELLED}:
        raise HTTPException(404, "This enquiry form is not available")
    if tenant.status == TenantStatus.TRIAL and aware(tenant.trial_ends_at) <= utcnow():
        raise HTTPException(404, "This enquiry form is not available")
    set_database_tenant(db, tenant.id)
    return tenant


@app.get("/api/public/business/{slug}/enquiry-form")
def public_enquiry_form(slug: str, db: Session = Depends(get_db)):
    tenant = public_tenant(slug, db)
    row = db.get(EnquiryFormConfig, tenant.id)
    if not row or not row.is_published:
        raise HTTPException(404, "This enquiry form has not been published yet")
    packages = db.scalars(select(ServicePackage).where(
        ServicePackage.tenant_id == tenant.id, ServicePackage.is_active.is_(True)
    ).order_by(ServicePackage.sort_order, ServicePackage.created_at)).all()
    branding = tenant.branding or {}
    questions = ensure_default_enquiry_questions(db, tenant, row)
    question_data = []
    for question in questions:
        if not question.is_active:
            continue
        item = enquiry_question_json(question)
        if question.system_key == "package_interest":
            item["options"] = [package.name for package in packages]
        if question.question_type == "venue":
            item["venue_search"] = {"provider": "google_places", "configured": False,
                                    "manual_entry_available": True}
        question_data.append(item)
    result = enquiry_form_json(row, tenant)
    result.update({"display_name": branding.get("display_name") or tenant.display_name,
                   "accent_colour": branding.get("accent_colour") or "#a9782e",
                   "packages": [{"id": item.id, "name": item.name,
                                  "price_pence": item.price_pence} for item in packages],
                   "questions": question_data})
    result.pop("public_url", None)
    return result


@app.get("/api/public/business/{slug}/availability/{event_date}")
def public_date_availability(slug: str, event_date: date, db: Session = Depends(get_db)):
    tenant = public_tenant(slug, db)
    blocked = db.scalar(select(TenantDateBlock.id).where(
        TenantDateBlock.tenant_id == tenant.id,
        TenantDateBlock.archived_at.is_(None),
        TenantDateBlock.start_date <= event_date,
        TenantDateBlock.end_date >= event_date).limit(1))
    booked = db.scalar(select(Booking.id).where(
        Booking.tenant_id == tenant.id, Booking.event_date == event_date,
        Booking.status.in_(["confirmed", "completed"])).limit(1))
    return {"date": event_date.isoformat(), "available": not bool(blocked or booked),
            "message": ("That date currently looks available."
                        if not blocked and not booked
                        else "That date is not available. Please get in touch if you would like to discuss alternatives.")}


@app.post("/api/public/business/{slug}/enquiries", status_code=201)
def submit_public_enquiry(slug: str, payload: PublicEnquiryIn, request: Request,
                          db: Session = Depends(get_db)):
    tenant = public_tenant(slug, db)
    config = db.get(EnquiryFormConfig, tenant.id)
    if not config or not config.is_published:
        raise HTTPException(404, "This enquiry form has not been published yet")
    if payload.website:
        return {"ok": True, "message": config.success_message}
    questions = ensure_default_enquiry_questions(db, tenant, config)
    supplied = payload.model_dump()
    built_in_values = {key: supplied.get(key) for key, *_ in DEFAULT_ENQUIRY_QUESTIONS}
    for question in questions:
        if not question.is_active:
            continue
        raw_value = built_in_values.get(question.system_key) if question.system_key else payload.answers.get(question.id, "")
        value = str(raw_value or "").strip()
        if question.is_required and not value:
            raise HTTPException(422, f"Please answer: {question.label}")
        if question.question_type in {"single_choice", "multiple_choice"} and question.options and value:
            selected = [part.strip() for part in value.split("|") if part.strip()]
            if any(part not in question.options for part in selected):
                raise HTTPException(422, f"Choose one of the available answers for: {question.label}")
    row = Enquiry(tenant_id=tenant.id, **payload.model_dump(exclude={"website", "answers"}))
    db.add(row); db.flush()
    for question in questions:
        if question.system_key or not question.is_active:
            continue
        answer = str(payload.answers.get(question.id, "")).strip()
        if answer:
            db.add(EnquiryAnswer(tenant_id=tenant.id, enquiry_id=row.id,
                                 question_id=question.id, question_label=question.label,
                                 answer=answer, sort_order=question.sort_order))
    trigger_enquiry_workflow(db, tenant, row)
    audit(db, "enquiry_received", "enquiry", row.id, tenant_id=tenant.id,
          request=request, detail={"workflow_trigger_recorded": True,
                                   "automatic_sending_paused": tenant.automations_paused})
    db.commit()
    return {"ok": True, "enquiry_id": row.id, "message": config.success_message,
            "automatic_reply": "paused" if tenant.automations_paused else "not_enabled"}
