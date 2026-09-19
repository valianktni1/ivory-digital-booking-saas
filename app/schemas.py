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


class TrialExtensionIn(BaseModel):
    days: Literal[30, 60, 90, 120]
    note: str = Field(min_length=3, max_length=500)


class BillingSettingsIn(BaseModel):
    plan_name: str = Field(min_length=2, max_length=120)
    price_pence: int = Field(default=0, ge=0, le=10_000_000)
    billing_cycle: Literal["monthly", "annual", "custom"] = "monthly"
    next_payment_due: date | None = None
    grace_days: int = Field(default=7, ge=0, le=60)
    auto_suspend: bool = True


class PlatformPaymentIn(BaseModel):
    amount_pence: int = Field(gt=0, le=10_000_000)
    paid_date: date = Field(default_factory=date.today)
    payment_method: Literal["bank_transfer", "card", "stripe", "cash", "other"] = "bank_transfer"
    reference: str = Field(default="", max_length=180)
    notes: str = Field(default="", max_length=1000)
    covers_until: date | None = None
    reactivate: bool = True


class AccountAccessIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    next_payment_due: date | None = None


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
    information_url: str = Field(default="", max_length=1000)
    is_featured: bool = False
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=10000)

    @field_validator("inclusions")
    @classmethod
    def clean_inclusions(cls, values: list[str]) -> list[str]:
        return [item.strip()[:240] for item in values if item.strip()]

    @field_validator("information_url")
    @classmethod
    def clean_package_url(cls, value: str) -> str:
        value = value.strip()
        if value and not value.lower().startswith("https://"):
            raise ValueError("The information link must begin with https://")
        return value


class AddOnIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=400)
    information_url: str = Field(default="", max_length=1000)
    price_pence: int = Field(default=0, ge=0, le=10_000_000)
    selection_mode: Literal["optional", "mandatory"] = "optional"
    mandatory_reason: str = Field(default="", max_length=300)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=10000)

    @field_validator("mandatory_reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        return value.strip()

    @field_validator("information_url")
    @classmethod
    def clean_add_on_url(cls, value: str) -> str:
        value = value.strip()
        if value and not value.lower().startswith("https://"):
            raise ValueError("The information link must begin with https://")
        return value


class WorkflowIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=400)
    is_active: bool = False
    sort_order: int = Field(default=0, ge=0, le=10000)


class WorkflowStepIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    trigger_event: Literal[
        "enquiry_received", "quote_sent", "quote_accepted", "booking_form_received",
        "booking_fee_due", "booking_fee_paid", "agreement_signed", "contract_signed",
        "questionnaire_submitted", "balance_due",
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


class EnquiryConvertIn(BaseModel):
    title: str = Field(default="", max_length=200)


class QuoteDraftIn(BaseModel):
    package_ids: list[str] = Field(default_factory=list, max_length=20)
    add_on_ids: list[str] = Field(default_factory=list, max_length=60)
    custom_items: list[dict] = Field(default_factory=list, max_length=30)
    message: str = Field(default="", max_length=4000)
    expires_on: date | None = None

    @field_validator("custom_items")
    @classmethod
    def validate_custom_items(cls, values: list[dict]) -> list[dict]:
        cleaned = []
        for item in values:
            label = str(item.get("label", "")).strip()[:180]
            if not label:
                continue
            price = int(item.get("price_pence", 0))
            if price < 0 or price > 10_000_000:
                raise ValueError("Custom item prices must be between £0 and £100,000")
            cleaned.append({"label": label, "price_pence": price})
        return cleaned


class QuoteAcceptIn(BaseModel):
    package_id: str
    add_on_ids: list[str] = Field(default_factory=list, max_length=60)
    client_name: str = Field(min_length=2, max_length=180)


class QuoteAmendmentIn(BaseModel):
    label: str = Field(min_length=2, max_length=180)
    price_pence: int = Field(default=0, ge=0, le=10_000_000)
    reason: str = Field(min_length=3, max_length=500)


class PaymentRecordIn(BaseModel):
    amount_pence: int = Field(gt=0, le=10_000_000)
    paid_date: date = Field(default_factory=date.today)
    payment_type: Literal["bank_transfer", "cash", "card", "other"] = "bank_transfer"
    reference: str = Field(default="", max_length=160)
    notes: str = Field(default="", max_length=1000)


class InvoiceVoidIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class SpecialPaymentIn(BaseModel):
    enabled: bool
    note: str = Field(default="", max_length=1000)


class ContractTemplateIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    body: str = Field(min_length=20, max_length=100_000)
    is_active: bool = True


class ContractIssueIn(BaseModel):
    template_id: str


class ContractSignIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=180)
    agreed: bool


class QuestionnaireTemplateIn(BaseModel):
    form_type: Literal["booking", "final_timings"]
    name: str = Field(min_length=2, max_length=160)
    introduction: str = Field(default="", max_length=4000)
    questions: list[dict] = Field(default_factory=list, max_length=100)
    is_active: bool = True

    @field_validator("questions")
    @classmethod
    def validate_questions(cls, values: list[dict]) -> list[dict]:
        cleaned = []
        identifiers: set[str] = set()
        allowed = {"short_text", "long_text", "email", "phone", "number", "date", "time", "yes_no", "single_choice", "multiple_choice"}
        for index, item in enumerate(values):
            label = str(item.get("label", "")).strip()[:240]
            if not label:
                continue
            kind = str(item.get("type", "short_text"))
            if kind not in allowed:
                raise ValueError("Unsupported questionnaire answer type")
            section = str(item.get("section") or "general").strip()[:50]
            section_title = str(item.get("section_title") or "Your details").strip()[:160]
            identifier = str(item.get("id") or f"q{index + 1}")[:50]
            if identifier in identifiers:
                raise ValueError("Every questionnaire question must have a unique identifier")
            identifiers.add(identifier)
            options = [str(value).strip()[:160] for value in item.get("options", []) if str(value).strip()][:30]
            if kind in {"single_choice", "multiple_choice"} and len(options) < 2:
                raise ValueError("Choice questions need at least two answer options")
            cleaned.append({"id": identifier,
                            "label": label, "type": kind,
                            "required": bool(item.get("required", False)),
                            "help_text": str(item.get("help_text") or "").strip()[:500],
                            "placeholder": str(item.get("placeholder") or "").strip()[:300],
                            "section": section or "general",
                            "section_title": section_title or "Your details",
                            "options": options})
        return cleaned


class QuestionnaireSubmitIn(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)

    @field_validator("answers")
    @classmethod
    def validate_answers(cls, values: dict[str, str]) -> dict[str, str]:
        if len(values) > 100:
            raise ValueError("Too many questionnaire answers")
        return {str(key)[:50]: str(value)[:10_000] for key, value in values.items()}


class DateBlockIn(BaseModel):
    start_date: date
    end_date: date
    label: str = Field(default="Unavailable", min_length=2, max_length=160)
    notes: str = Field(default="", max_length=2000)


class WorkflowModeIn(BaseModel):
    mode: Literal["automatic", "review", "task", "off"]
    apply_to_existing: bool = False


class WorkflowBookingControlIn(BaseModel):
    step_id: str
    paused: bool


class BookingCompleteIn(BaseModel):
    completed: bool = True


class BookingCancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class CalendarSettingsIn(BaseModel):
    calendar_id: str = Field(default="primary", min_length=1, max_length=500)
    calendar_name: str = Field(default="Primary calendar", min_length=1, max_length=200)


class HelpAskIn(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    context: str = Field(default="home", max_length=80, pattern=r"^[a-z0-9_-]+$")


class HelpArticleIn(BaseModel):
    slug: str = Field(min_length=3, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=3, max_length=180)
    category: str = Field(default="Getting started", min_length=2, max_length=80)
    summary: str = Field(default="", max_length=400)
    body: str = Field(min_length=10, max_length=20_000)
    keywords: list[str] = Field(default_factory=list, max_length=40)
    contexts: list[str] = Field(default_factory=list, max_length=20)
    action_label: str = Field(default="", max_length=100)
    action_route: str = Field(default="", max_length=80, pattern=r"^[a-z0-9_-]*$")
    tour_key: str = Field(default="", max_length=80, pattern=r"^[a-z0-9_-]*$")
    is_published: bool = True
    sort_order: int = Field(default=0, ge=0, le=10000)

    @field_validator("keywords", "contexts")
    @classmethod
    def clean_help_lists(cls, values: list[str]) -> list[str]:
        return [str(value).strip().lower()[:100] for value in values if str(value).strip()]
