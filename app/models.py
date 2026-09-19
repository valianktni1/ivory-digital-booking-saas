import enum
import secrets
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class TenantStatus(str, enum.Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class MembershipRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    STAFF = "staff"


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    owner_email: Mapped[str] = mapped_column(String(254), index=True)
    status: Mapped[TenantStatus] = mapped_column(Enum(TenantStatus), default=TenantStatus.TRIAL, index=True)
    trial_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    trial_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    timezone: Mapped[str] = mapped_column(String(80), default="Europe/London")
    storage_key: Mapped[str] = mapped_column(String(64), unique=True, default=lambda: secrets.token_hex(20))
    onboarding: Mapped[dict] = mapped_column(JSON, default=dict)
    branding: Mapped[dict] = mapped_column(JSON, default=dict)
    automations_paused: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery_code_hashes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[MembershipRole] = mapped_column(Enum(MembershipRole), default=MembershipRole.STAFF)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    tenant: Mapped[Tenant] = relationship()
    user: Mapped[User] = relationship()


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    role: Mapped[MembershipRole] = mapped_column(Enum(MembershipRole), default=MembershipRole.OWNER)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    tenant: Mapped[Tenant] = relationship()


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    assurance: Mapped[str] = mapped_column(String(20), default="password")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    user: Mapped[User] = relationship()


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    email: Mapped[str] = mapped_column(String(254), primary_key=True)
    failed_count: Mapped[int] = mapped_column(default=0)
    last_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    subject_type: Mapped[str] = mapped_column(String(80))
    subject_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_client_tenant_email"),
        Index("ix_clients_tenant_name", "tenant_id", "last_name", "first_name"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100), default="")
    partner_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    email: Mapped[str] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (Index("ix_bookings_tenant_event", "tenant_id", "event_date"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    venue: Mapped[str | None] = mapped_column(String(240), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="enquiry", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    client: Mapped[Client] = relationship()


class ServicePackage(Base):
    __tablename__ = "service_packages"
    __table_args__ = (
        Index("ix_service_packages_tenant_order", "tenant_id", "sort_order"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160))
    short_description: Mapped[str] = mapped_column(String(300), default="")
    price_pence: Mapped[int] = mapped_column(default=0)
    booking_fee_pence: Mapped[int] = mapped_column(default=10000)
    balance_due_days: Mapped[int] = mapped_column(default=45)
    inclusions: Mapped[list] = mapped_column(JSON, default=list)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class PackageAddOn(Base):
    __tablename__ = "package_add_ons"
    __table_args__ = (
        Index("ix_package_add_ons_tenant_order", "tenant_id", "sort_order"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(String(400), default="")
    price_pence: Mapped[int] = mapped_column(default=0)
    selection_mode: Mapped[str] = mapped_column(String(20), default="optional")
    mandatory_reason: Mapped[str] = mapped_column(String(300), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Workflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        Index("ix_workflows_tenant_order", "tenant_id", "sort_order"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(String(400), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(default=0)
    revision: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        Index("ix_workflow_steps_workflow_order", "workflow_id", "sort_order"),
        Index("ix_workflow_steps_tenant_trigger", "tenant_id", "trigger_event"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160))
    trigger_event: Mapped[str] = mapped_column(String(50))
    timing_direction: Mapped[str] = mapped_column(String(20), default="after")
    offset_value: Mapped[int] = mapped_column(default=0)
    offset_unit: Mapped[str] = mapped_column(String(20), default="days")
    action_type: Mapped[str] = mapped_column(String(30), default="manual_task")
    subject: Mapped[str] = mapped_column(String(220), default="")
    message_body: Mapped[str] = mapped_column(Text, default="")
    task_title: Mapped[str] = mapped_column(String(220), default="")
    is_paused: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class WorkflowRevision(Base):
    __tablename__ = "workflow_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(default=1)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EnquiryFormConfig(Base):
    __tablename__ = "enquiry_form_configs"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    heading: Mapped[str] = mapped_column(String(180), default="Tell us about your wedding")
    introduction: Mapped[str] = mapped_column(Text, default="We would love to hear what you are planning.")
    submit_label: Mapped[str] = mapped_column(String(80), default="Send my enquiry")
    success_message: Mapped[str] = mapped_column(Text, default="Thank you - your enquiry has arrived safely.")
    ask_partner_name: Mapped[bool] = mapped_column(Boolean, default=True)
    ask_phone: Mapped[bool] = mapped_column(Boolean, default=True)
    ask_venue: Mapped[bool] = mapped_column(Boolean, default=True)
    ask_package_interest: Mapped[bool] = mapped_column(Boolean, default=True)
    ask_message: Mapped[bool] = mapped_column(Boolean, default=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class EnquiryFormQuestion(Base):
    __tablename__ = "enquiry_form_questions"
    __table_args__ = (
        Index("ix_enquiry_form_questions_tenant_order", "tenant_id", "sort_order"),
        UniqueConstraint("tenant_id", "system_key", name="uq_enquiry_question_system_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    system_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    label: Mapped[str] = mapped_column(String(180))
    help_text: Mapped[str] = mapped_column(String(300), default="")
    question_type: Mapped[str] = mapped_column(String(30), default="short_text")
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    is_protected: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    options: Mapped[list] = mapped_column(JSON, default=list)
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Enquiry(Base):
    __tablename__ = "enquiries"
    __table_args__ = (Index("ix_enquiries_tenant_created", "tenant_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    partner_name: Mapped[str] = mapped_column(String(160), default="")
    email: Mapped[str] = mapped_column(String(254))
    phone: Mapped[str] = mapped_column(String(50), default="")
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    venue: Mapped[str] = mapped_column(String(240), default="")
    package_interest: Mapped[str] = mapped_column(String(160), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="new", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EnquiryAnswer(Base):
    __tablename__ = "enquiry_answers"
    __table_args__ = (Index("ix_enquiry_answers_enquiry_order", "enquiry_id", "sort_order"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    enquiry_id: Mapped[str] = mapped_column(ForeignKey("enquiries.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id: Mapped[str] = mapped_column(String(36))
    question_label: Mapped[str] = mapped_column(String(180))
    answer: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(default=0)


class MailboxSetting(Base):
    __tablename__ = "mailbox_settings"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    from_name: Mapped[str] = mapped_column(String(160), default="")
    email_address: Mapped[str] = mapped_column(String(254), default="")
    smtp_host: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(default=465)
    smtp_security: Mapped[str] = mapped_column(String(20), default="ssl")
    smtp_username: Mapped[str] = mapped_column(String(254), default="")
    smtp_password_encrypted: Mapped[str] = mapped_column(Text, default="")
    imap_host: Mapped[str] = mapped_column(String(255), default="")
    imap_port: Mapped[int] = mapped_column(default=993)
    imap_security: Mapped[str] = mapped_column(String(20), default="ssl")
    imap_username: Mapped[str] = mapped_column(String(254), default="")
    imap_password_encrypted: Mapped[str] = mapped_column(Text, default="")
    smtp_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    imap_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class BookingJourney(Base):
    __tablename__ = "booking_journeys"
    booking_id: Mapped[str] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    enquiry_id: Mapped[str | None] = mapped_column(ForeignKey("enquiries.id", ondelete="SET NULL"), nullable=True, index=True)
    portal_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    portal_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    quote_state: Mapped[dict] = mapped_column(JSON, default=dict)
    accepted_quote: Mapped[dict] = mapped_column(JSON, default=dict)
    workflow_controls: Mapped[dict] = mapped_column(JSON, default=dict)
    calendar_state: Mapped[dict] = mapped_column(JSON, default=dict)
    booking_fee_pence: Mapped[int] = mapped_column(default=10000)
    balance_due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    special_payment_arrangement: Mapped[bool] = mapped_column(Boolean, default=False)
    special_payment_note: Mapped[str] = mapped_column(Text, default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class TenantInvoiceCounter(Base):
    __tablename__ = "tenant_invoice_counters"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    next_sequence: Mapped[int] = mapped_column(default=1)


class BookingInvoice(Base):
    __tablename__ = "booking_invoices"
    __table_args__ = (UniqueConstraint("tenant_id", "number", name="uq_booking_invoice_tenant_number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    booking_id: Mapped[str] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(index=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    issue_date: Mapped[date] = mapped_column(Date, default=date.today)
    booking_fee_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    total_pence: Mapped[int] = mapped_column(default=0)
    paid_pence: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(30), default="unpaid", index=True)
    line_items: Mapped[list] = mapped_column(JSON, default=list)
    payment_schedule: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    void_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class BookingQuoteRevision(Base):
    __tablename__ = "booking_quote_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    booking_id: Mapped[str] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True)
    previous_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(String(500), default="")
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BookingPayment(Base):
    __tablename__ = "booking_payments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("booking_invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    amount_pence: Mapped[int] = mapped_column()
    paid_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    payment_type: Mapped[str] = mapped_column(String(40), default="bank_transfer")
    reference: Mapped[str] = mapped_column(String(160), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TenantContractTemplate(Base):
    __tablename__ = "tenant_contract_templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), default="Wedding photography agreement")
    version: Mapped[int] = mapped_column(default=1)
    body: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class BookingContract(Base):
    __tablename__ = "booking_contracts"
    __table_args__ = (UniqueConstraint("tenant_id", "booking_id", name="uq_booking_contract_tenant_booking"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    booking_id: Mapped[str] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str | None] = mapped_column(ForeignKey("tenant_contract_templates.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(180))
    version: Mapped[int] = mapped_column(default=1)
    body_snapshot: Mapped[str] = mapped_column(Text)
    client_name: Mapped[str] = mapped_column(String(180), default="")
    client_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_ip: Mapped[str] = mapped_column(String(64), default="")
    supplier_name: Mapped[str] = mapped_column(String(180), default="")
    supplier_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class QuestionnaireTemplate(Base):
    __tablename__ = "questionnaire_templates"
    __table_args__ = (UniqueConstraint("tenant_id", "form_type", name="uq_questionnaire_tenant_type"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    form_type: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(160))
    introduction: Mapped[str] = mapped_column(Text, default="")
    questions: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class QuestionnaireSubmission(Base):
    __tablename__ = "questionnaire_submissions"
    __table_args__ = (UniqueConstraint("tenant_id", "booking_id", "form_type", name="uq_submission_booking_type"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    booking_id: Mapped[str] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True)
    form_type: Mapped[str] = mapped_column(String(40), index=True)
    template_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TenantCalendarConnection(Base):
    __tablename__ = "tenant_calendar_connections"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    google_account_email: Mapped[str] = mapped_column(String(254), default="")
    calendar_id: Mapped[str] = mapped_column(String(500), default="primary")
    calendar_name: Mapped[str] = mapped_column(String(200), default="Primary calendar")
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(Text, default="")
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")


class TenantCalendarOAuthState(Base):
    __tablename__ = "tenant_calendar_oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TenantDateBlock(Base):
    __tablename__ = "tenant_date_blocks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    label: Mapped[str] = mapped_column(String(160), default="Unavailable")
    notes: Mapped[str] = mapped_column(Text, default="")
    calendar_state: Mapped[dict] = mapped_column(JSON, default=dict)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkflowStepControl(Base):
    __tablename__ = "workflow_step_controls"
    step_id: Mapped[str] = mapped_column(ForeignKey("workflow_steps.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), default="off")
    apply_to_existing: Mapped[bool] = mapped_column(Boolean, default=False)


class WorkflowAction(Base):
    __tablename__ = "workflow_actions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "booking_id", "step_id", "trigger_key", name="uq_workflow_action_once"),
        UniqueConstraint("tenant_id", "enquiry_id", "step_id", "trigger_key", name="uq_enquiry_workflow_action_once"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    booking_id: Mapped[str | None] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), nullable=True, index=True)
    enquiry_id: Mapped[str | None] = mapped_column(ForeignKey("enquiries.id", ondelete="CASCADE"), nullable=True, index=True)
    step_id: Mapped[str] = mapped_column(ForeignKey("workflow_steps.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger_key: Mapped[str] = mapped_column(String(60), index=True)
    mode: Mapped[str] = mapped_column(String(20))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="paused", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class HelpArticle(Base):
    __tablename__ = "help_articles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    category: Mapped[str] = mapped_column(String(80), default="Getting started", index=True)
    summary: Mapped[str] = mapped_column(String(400), default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    contexts: Mapped[list] = mapped_column(JSON, default=list)
    action_label: Mapped[str] = mapped_column(String(100), default="")
    action_route: Mapped[str] = mapped_column(String(80), default="")
    tour_key: Mapped[str] = mapped_column(String(80), default="")
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class TenantSubscription(Base):
    __tablename__ = "tenant_subscriptions"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    plan_name: Mapped[str] = mapped_column(String(120), default="Ivory Booking Studio")
    price_pence: Mapped[int] = mapped_column(default=0)
    billing_cycle: Mapped[str] = mapped_column(String(20), default="monthly")
    billing_status: Mapped[str] = mapped_column(String(30), default="trial", index=True)
    trial_days_granted: Mapped[int] = mapped_column(default=30)
    next_payment_due: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    grace_days: Mapped[int] = mapped_column(default=7)
    auto_suspend: Mapped[bool] = mapped_column(Boolean, default=True)
    provider: Mapped[str] = mapped_column(String(30), default="manual")
    provider_customer_id: Mapped[str] = mapped_column(String(200), default="")
    provider_subscription_id: Mapped[str] = mapped_column(String(200), default="")
    last_payment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspension_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class PlatformBillingPayment(Base):
    __tablename__ = "platform_billing_payments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    amount_pence: Mapped[int] = mapped_column(nullable=False)
    paid_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payment_method: Mapped[str] = mapped_column(String(30), default="bank_transfer")
    reference: Mapped[str] = mapped_column(String(180), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    covers_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
