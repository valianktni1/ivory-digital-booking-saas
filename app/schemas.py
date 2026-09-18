from datetime import date
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=500)
    code: str | None = Field(default=None, min_length=6, max_length=32)


class TotpConfirmIn(BaseModel):
    code: str = Field(min_length=6, max_length=12)


class TenantCreateIn(BaseModel):
    display_name: str = Field(min_length=2, max_length=160)
    slug: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    owner_email: EmailStr
    owner_name: str = Field(min_length=2, max_length=160)
    timezone: str = Field(default="Europe/London", min_length=3, max_length=80)


class TenantStatusIn(BaseModel):
    status: Literal["trial", "active", "suspended", "cancelled"]
    reason: str = Field(min_length=3, max_length=500)


class AutomationPauseIn(BaseModel):
    paused: bool
    reason: str = Field(min_length=3, max_length=500)


class InvitationAcceptIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=14, max_length=500)


class ClientCreateIn(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(default="", max_length=100)
    partner_name: str | None = Field(default=None, max_length=160)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=50)


class BookingCreateIn(BaseModel):
    client_id: str
    title: str = Field(min_length=2, max_length=200)
    event_date: date | None = None
    venue: str | None = Field(default=None, max_length=240)


class BrandingPatchIn(BaseModel):
    display_name: str = Field(min_length=2, max_length=160)
    accent_colour: str = Field(default="#a9782e", pattern=r"^#[0-9A-Fa-f]{6}$")
    welcome_message: str = Field(default="Welcome to your private booking area.", max_length=500)

    @field_validator("welcome_message")
    @classmethod
    def strip_welcome(cls, value: str) -> str:
        return value.strip()


class PackageIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    short_description: str = Field(default="", max_length=300)
    price_pence: int = Field(ge=0, le=10_000_000)
    booking_fee_pence: int = Field(default=10000, ge=0, le=10_000_000)
    balance_due_days: int = Field(default=45, ge=0, le=730)
    inclusions: list[str] = Field(default_factory=list, max_length=40)
    is_featured: bool = False
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=10000)

    @field_validator("inclusions")
    @classmethod
    def clean_inclusions(cls, values: list[str]) -> list[str]:
        return [item.strip()[:240] for item in values if item.strip()]


class AddOnIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=400)
    price_pence: int = Field(default=0, ge=0, le=10_000_000)
    selection_mode: Literal["optional", "mandatory"] = "optional"
    mandatory_reason: str = Field(default="", max_length=300)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=10000)

    @field_validator("mandatory_reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        return value.strip()


class WorkflowIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=400)
    is_active: bool = False
    sort_order: int = Field(default=0, ge=0, le=10000)


class WorkflowStepIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    trigger_event: Literal[
        "enquiry_received", "quote_sent", "quote_accepted", "booking_fee_paid",
        "contract_signed", "questionnaire_submitted", "balance_due",
        "balance_paid", "wedding_date", "wedding_completed"
    ]
    timing_direction: Literal["before", "after", "immediately"] = "after"
    offset_value: int = Field(default=0, ge=0, le=3650)
    offset_unit: Literal["minutes", "hours", "days", "weeks"] = "days"
    action_type: Literal["manual_task", "email"] = "manual_task"
    subject: str = Field(default="", max_length=220)
    message_body: str = Field(default="", max_length=20000)
    task_title: str = Field(default="", max_length=220)
    is_paused: bool = True
    sort_order: int = Field(default=0, ge=0, le=10000)


class EnquiryFormIn(BaseModel):
    heading: str = Field(min_length=2, max_length=180)
    introduction: str = Field(default="", max_length=1000)
    submit_label: str = Field(default="Send my enquiry", min_length=2, max_length=80)
    success_message: str = Field(default="Thank you - your enquiry has arrived safely.", min_length=2, max_length=1000)
    ask_partner_name: bool = True
    ask_phone: bool = True
    ask_venue: bool = True
    ask_package_interest: bool = True
    ask_message: bool = True
    is_published: bool = False


class PublicEnquiryIn(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    partner_name: str = Field(default="", max_length=160)
    email: EmailStr
    phone: str = Field(default="", max_length=50)
    event_date: date | None = None
    venue: str = Field(default="", max_length=240)
    package_interest: str = Field(default="", max_length=160)
    message: str = Field(default="", max_length=4000)
    website: str = Field(default="", max_length=200)  # Honeypot; must remain empty.
    answers: dict[str, str] = Field(default_factory=dict)

    @field_validator("answers")
    @classmethod
    def limit_answers(cls, values: dict[str, str]) -> dict[str, str]:
        if len(values) > 80:
            raise ValueError("Too many enquiry answers")
        return {str(key)[:36]: str(value)[:4000] for key, value in values.items()}


class EnquiryQuestionIn(BaseModel):
    label: str = Field(min_length=2, max_length=180)
    help_text: str = Field(default="", max_length=300)
    question_type: Literal[
        "short_text", "long_text", "email", "phone", "date",
        "single_choice", "multiple_choice", "yes_no", "venue"
    ] = "short_text"
    is_required: bool = False
    is_active: bool = True
    options: list[str] = Field(default_factory=list, max_length=30)
    sort_order: int = Field(default=0, ge=0, le=10000)

    @field_validator("options")
    @classmethod
    def clean_options(cls, values: list[str]) -> list[str]:
        return [item.strip()[:160] for item in values if item.strip()]


class MailboxSettingsIn(BaseModel):
    from_name: str = Field(min_length=2, max_length=160)
    email_address: EmailStr
    smtp_host: str = Field(min_length=3, max_length=255)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_security: Literal["ssl", "starttls", "none"] = "ssl"
    smtp_username: str = Field(min_length=1, max_length=254)
    smtp_password: str = Field(default="", max_length=1000)
    imap_host: str = Field(min_length=3, max_length=255)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_security: Literal["ssl", "starttls", "none"] = "ssl"
    imap_username: str = Field(min_length=1, max_length=254)
    imap_password: str = Field(default="", max_length=1000)
