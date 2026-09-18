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

