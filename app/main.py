import base64
import imaplib
import ipaddress
import io
import socket
import smtplib
import ssl
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
import qrcode
import qrcode.image.svg
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .database import Base, SessionLocal, engine, get_db
from .models import (AuditLog, Booking, Client, Enquiry, EnquiryAnswer,
                     EnquiryFormConfig, EnquiryFormQuestion, Invitation,
                     MailboxSetting, Membership,
                     MembershipRole, PackageAddOn, ServicePackage, Tenant,
                     TenantStatus, User, UserSession, Workflow, WorkflowRevision,
                     WorkflowStep)
from .schemas import (AutomationPauseIn, BookingCreateIn, BrandingPatchIn,
                      ClientCreateIn, EnquiryFormIn, EnquiryQuestionIn,
                      InvitationAcceptIn, LoginIn, AddOnIn, MailboxSettingsIn,
                      PackageIn, PublicEnquiryIn,
                      TenantCreateIn, TenantStatusIn, TotpConfirmIn,
                      WorkflowIn, WorkflowStepIn)
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
    version="0.3.1-phase-three-form-builder",
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
    return {"status": "ok", "build": "2026.09.18-phase-three-form-builder", "service": "ivory-booking-saas"}


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


def step_json(row: WorkflowStep) -> dict:
    return {"id": row.id, "workflow_id": row.workflow_id, "name": row.name,
            "trigger_event": row.trigger_event, "timing_direction": row.timing_direction,
            "offset_value": row.offset_value, "offset_unit": row.offset_unit,
            "action_type": row.action_type, "subject": row.subject,
            "message_body": row.message_body, "task_title": row.task_title,
            "is_paused": row.is_paused, "sort_order": row.sort_order}


def workflow_json(row: Workflow, db: Session) -> dict:
    steps = db.scalars(select(WorkflowStep).where(
        WorkflowStep.tenant_id == row.tenant_id, WorkflowStep.workflow_id == row.id
    ).order_by(WorkflowStep.sort_order, WorkflowStep.created_at)).all()
    return {"id": row.id, "name": row.name, "description": row.description,
            "is_active": row.is_active, "sort_order": row.sort_order,
            "revision": row.revision, "steps": [step_json(item) for item in steps]}


def save_workflow_revision(db: Session, workflow: Workflow, actor: User) -> None:
    db.add(WorkflowRevision(tenant_id=workflow.tenant_id, workflow_id=workflow.id,
                            revision=workflow.revision,
                            snapshot=workflow_json(workflow, db), actor_user_id=actor.id))
    workflow.revision += 1


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
        "phase": "Foundation ready — booking workflow arrives in the next phase",
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
    rows = db.scalars(select(Workflow).where(Workflow.tenant_id == tenant.id)
                      .order_by(Workflow.sort_order, Workflow.created_at)).all()
    return [workflow_json(row, db) for row in rows]


@app.post("/api/studio/workflows", status_code=201)
def create_workflow(payload: WorkflowIn, request: Request,
                    session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = Workflow(tenant_id=membership.tenant_id, **payload.model_dump())
    db.add(row); db.flush()
    audit(db, "workflow_created", "workflow", row.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit()
    return workflow_json(row, db)


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
    db.commit()
    return workflow_json(row, db)


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
    db.add(row); db.flush(); mark_onboarding(tenant, "templates")
    audit(db, "workflow_step_created", "workflow_step", row.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"forced_paused": True})
    db.commit()
    return step_json(row)


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
        "portal_status": "ready_for_phase_two",
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
    audit(db, "enquiry_received", "enquiry", row.id, tenant_id=tenant.id,
          request=request, detail={"workflow_trigger_recorded": True,
                                   "automatic_sending_paused": tenant.automations_paused})
    db.commit()
    return {"ok": True, "enquiry_id": row.id, "message": config.success_message,
            "automatic_reply": "paused" if tenant.automations_paused else "not_enabled"}
