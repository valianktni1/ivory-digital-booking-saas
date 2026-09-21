import base64
import email
import html
import imaplib
import ipaddress
import io
import socket
import smtplib
import ssl
import re
import secrets
import shutil
from email.header import decode_header
from email.utils import getaddresses, parsedate_to_datetime
from urllib.parse import quote, urlencode
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
import qrcode
import qrcode.image.svg
import httpx
from fastapi.responses import FileResponse, RedirectResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, inspect, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .database import Base, SessionLocal, engine, get_db
from .models import (AuditLog, Booking, BookingContract, BookingDocument, BookingInvoice,
                     BookingJourney, BookingPayment, Client, Enquiry, EnquiryAnswer,
                     BookingNote, EmailMessage, EmailTemplate,
                     EnquiryFormConfig, EnquiryFormQuestion, Invitation,
                     HelpArticle, MailboxSetting, Membership,
                     MembershipRole, PackageAddOn, QuestionnaireSubmission,
                     PlatformBillingPayment,
                     QuestionnaireTemplate, ServicePackage, Tenant,
                     TenantSubscription,
                     TenantCalendarConnection, TenantCalendarOAuthState,
                     TenantContractTemplate, TenantEmailBranding,
                     TenantDateBlock, TenantInvoiceCounter, BookingQuoteRevision,
                     StudioNotification, StudioTask,
                     TenantStatus, User, UserSession, Workflow, WorkflowRevision,
                     WorkflowAction, WorkflowStep, WorkflowStepControl)
from .schemas import (AccountAccessIn, AutomationPauseIn, BillingSettingsIn,
                      BookingCreateIn, BookingRescheduleIn, BookingUpdateIn, BrandingPatchIn,
                      BookingCancelIn, BookingCompleteIn, CalendarSettingsIn, ClientCreateIn,
                      ContractIssueIn, ContractSignIn, ContractTemplateIn,
                      DateBlockIn, EnquiryConvertIn, EnquiryFormIn, EnquiryQuestionIn,
                      EmailBrandingIn, EmailTemplateIn, EnquiryCloseIn,
                      HelpArticleIn, HelpAskIn,
                      InvitationAcceptIn, LoginIn, AddOnIn, MailboxSettingsIn,
                      ManualEmailIn, NoteIn, PackageIn, PaymentRecordIn, PlatformPaymentIn, PublicEnquiryIn,
                      QuestionnaireSubmitIn, QuestionnaireTemplateIn,
                      QuoteAcceptIn, QuoteAmendmentIn, QuoteDraftIn, QuoteEmailSendIn, SpecialPaymentIn,
                      TenantCreateIn, TenantStatusIn, TotpConfirmIn, TrialExtensionIn,
                      TaskIn, TaskUpdateIn, WorkflowActionReviewIn,
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
from .questionnaire_defaults import DEFAULT_QUESTIONNAIRES, default_questionnaire
from .messaging import merge_message, send_tenant_email


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


DEFAULT_HELP_ARTICLES = (
    {
        "slug": "start-setting-up-my-studio", "title": "Where should I start?",
        "category": "Getting started", "contexts": ["home"], "tour_key": "home",
        "keywords": ["start", "setup", "first", "begin", "checklist", "new studio"],
        "summary": "Work through the setup checklist in a calm, safe order.",
        "body": "Start with Business & brand, then publish your enquiry form and add your packages. Next, create your agreement and forms, connect your mailbox, and review the supplied workflow.\n\nEverything begins safely paused. Building your Studio cannot accidentally contact a couple.",
        "action_label": "Return to setup checklist", "action_route": "home", "sort_order": 10,
    },
    {
        "slug": "publish-enquiry-form", "title": "How do I publish my enquiry form?",
        "category": "Enquiries", "contexts": ["enquiry", "enquiries"], "tour_key": "enquiry",
        "keywords": ["publish", "enquiry form", "inquiry form", "questions", "website link", "form live"],
        "summary": "Prepare the questions, preview the page, then deliberately publish it.",
        "body": "Open Enquiry form from Setup. Write your heading and welcome, review the protected contact and wedding questions, and add any questions of your own. Use Preview form to check the couple's view.\n\nWhen you are happy, switch on Publish this enquiry form and save. The public address can then be linked from your website.",
        "action_label": "Open enquiry form", "action_route": "enquiry", "sort_order": 20,
    },
    {
        "slug": "share-or-embed-enquiry-form", "title": "How do I put my enquiry form on my website?",
        "category": "Enquiries", "contexts": ["enquiry"], "tour_key": "enquiry",
        "keywords": ["share enquiry form", "copy link", "iframe", "embed", "website", "wordpress", "elementor", "qr code"],
        "summary": "Use a direct link, website button, responsive embed or downloadable QR code.",
        "body": "Open Enquiry form and find Share your enquiry form. Use Copy direct link for social media, emails or an ordinary website button.\n\nFor the form to appear inside your website, copy the responsive embed code and paste it into an HTML or code block. WordPress and Elementor users can paste it into an HTML widget. You can also copy a ready-made enquiry button or download a QR code. Publish the form before sharing any option.",
        "action_label": "Open enquiry sharing", "action_route": "enquiry", "sort_order": 25,
    },
    {
        "slug": "turn-enquiry-into-wedding", "title": "How do I start a booking from an enquiry?",
        "category": "Enquiries", "contexts": ["enquiries", "weddings"], "tour_key": "enquiries",
        "keywords": ["convert", "enquiry", "inquiry", "start journey", "make booking", "new wedding"],
        "summary": "Open the enquiry and manage its quote without moving it into Weddings.",
        "body": "Open Enquiries and select the couple. Their details, quote and email history stay together there while you prepare and send the quote.\n\nThe enquiry moves into Weddings only when the couple accepts their quote or you deliberately choose Mark as booked.",
        "action_label": "View enquiries", "action_route": "enquiries", "sort_order": 30,
    },
    {
        "slug": "create-and-send-quote", "title": "How do I prepare a quote?",
        "category": "Quotes", "contexts": ["enquiries", "weddings"], "tour_key": "enquiries",
        "keywords": ["quote", "quotation", "send quote", "package choice", "client link", "prepare quote"],
        "summary": "Choose what to offer, save the draft, review the exact email and send deliberately.",
        "body": "Open Enquiries and select the couple. Tick the packages and extras you want to offer, add any custom item or discount, then save the draft. Saving never sends an email.\n\nChoose Save & review email to check the recipient, subject, message and secure link. Personal changes affect only this couple. The quote is sent only when you press Send quote now.",
        "action_label": "Open enquiries", "action_route": "enquiries", "sort_order": 40,
    },
    {
        "slug": "change-accepted-quote", "title": "Can I change a quote after it is accepted?",
        "category": "Quotes", "contexts": ["weddings", "payments"],
        "keywords": ["edit accepted quote", "change quote", "add free album", "amend quote", "fully paid", "locked"],
        "summary": "Yes, until the invoice is fully paid, with a permanent audit trail.",
        "body": "Open the wedding and use Add something before full payment beneath the accepted quote. Enter the description, price — including £0 for a complimentary item — and the reason for the change.\n\nThe original accepted snapshot is preserved and the invoice is updated. Once paid in full, commercial changes are locked for safety.",
        "action_label": "Open weddings", "action_route": "weddings", "sort_order": 50,
    },
    {
        "slug": "record-payment", "title": "How do I record a payment?",
        "category": "Payments", "contexts": ["payments", "weddings"], "tour_key": "payments",
        "keywords": ["payment", "deposit", "booking fee", "bank transfer", "cash", "paid", "balance"],
        "summary": "Record money only after you have actually received it.",
        "body": "Open Payments, or open the couple's wedding and find their invoice. Select Record payment received, enter the amount and date, choose the payment method, and add a reference if helpful.\n\nThe invoice balance updates immediately. The system never marks money as received on its own.",
        "action_label": "Open payments", "action_route": "payments", "sort_order": 60,
    },
    {
        "slug": "secure-date-without-booking-fee", "title": "What if they are paying later or on the day?",
        "category": "Payments", "contexts": ["weddings", "calendar"],
        "keywords": ["pay later", "pay on day", "no deposit", "no booking fee", "secure date", "special arrangement"],
        "summary": "Record a special payment arrangement to secure the date deliberately.",
        "body": "Open the wedding and select Record an agreed pay-later arrangement in the Calendar panel. Add a private note explaining what you agreed.\n\nThis deliberately secures the date and allows it to appear in availability and the connected calendar without pretending that a payment was received.",
        "action_label": "Open weddings", "action_route": "weddings", "sort_order": 70,
    },
    {
        "slug": "issue-and-sign-contract", "title": "How do contracts work?",
        "category": "Contracts & forms", "contexts": ["documents", "weddings"], "tour_key": "documents",
        "keywords": ["contract", "agreement", "sign", "signature", "countersign", "terms"],
        "summary": "The active agreement is issued when a quote is accepted, then completed automatically when the couple signs.",
        "body": "Create and save your active agreement under Contracts & forms. When a couple accepts their quote, Studio adds a fixed snapshot of that agreement to their private booking area alongside their accepted package, extras and questionnaires.\n\nThe couple reads and signs it once. Studio then countersigns automatically using your saved business signature name and emails the couple a completed PDF copy. Both sides can also download the completed PDF from the wedding workspace or private booking area.",
        "action_label": "Open contracts & forms", "action_route": "documents", "sort_order": 80,
    },
    {
        "slug": "questionnaires-and-final-timings", "title": "How do booking and final-timings forms work?",
        "category": "Contracts & forms", "contexts": ["documents", "weddings"],
        "keywords": ["questionnaire", "booking form", "final timings", "questions", "completed form", "download pdf"],
        "summary": "Start with complete wedding forms, then edit every section and question.",
        "body": "Open Contracts & forms and choose either Booking Questionnaire or Final Wedding Timings. Both begin with a complete photographer-designed starter form. You can add, edit, remove and reorder questions, group them into clearly named sections, choose the answer type, add helpful guidance and decide whether an answer is required.\n\nSave each form separately when it is ready. The couple completes it inside their secure portal and can return to update it if plans change. Submitted answers appear in the wedding journey and can be downloaded as clearly named PDFs. Restoring the starter changes only your current draft — forms already submitted by couples keep their original snapshot.",
        "action_label": "Open contracts & forms", "action_route": "documents", "sort_order": 90,
    },
    {
        "slug": "package-addon-information-links", "title": "Can I link packages or extras to more information?",
        "category": "Packages & pricing", "contexts": ["packages", "weddings"], "tour_key": "packages",
        "keywords": ["package link", "addon link", "add-on link", "website", "more information", "album details", "learn more"],
        "summary": "Add an optional webpage link that couples can open before choosing.",
        "body": "Open Packages & pricing, then add or edit a package or add-on. Paste the full secure webpage address into More information webpage — it must begin with https://.\n\nWhen that item is included in a quote, the couple sees a clear information link beside it. The page opens separately, so their quote and choices remain safely open. Leave the field blank when no extra page is needed.",
        "action_label": "Open packages & pricing", "action_route": "packages", "sort_order": 95,
    },
    {
        "slug": "workflow-modes-explained", "title": "What do the four workflow modes mean?",
        "category": "Emails & workflow", "contexts": ["workflow", "weddings"], "tour_key": "workflow",
        "keywords": ["automatic", "review first", "task only", "disabled", "workflow mode", "email timing"],
        "summary": "Choose how much control you want for every individual step.",
        "body": "Disabled does nothing. Task only creates a private reminder and never contacts the couple. Review first prepares the action but waits for your approval. Automatic can send at the chosen time — but only after Ivory Digital's master safety pause has been released.\n\nYou can use a different mode for every step.",
        "action_label": "Open emails & workflow", "action_route": "workflow", "sort_order": 100,
    },
    {
        "slug": "schedule-wedding-check-in-emails", "title": "How do I schedule wedding check-in emails?",
        "category": "Emails & workflow", "contexts": ["workflow", "weddings"], "tour_key": "workflow",
        "keywords": ["check in", "scheduled email", "120 days", "90 days", "60 days", "30 days", "final timings"],
        "summary": "Create separate emails for 120, 90, 60 or 30 days before the wedding.",
        "body": "Open Emails & workflow and choose New email. Pick 120, 90, 60 or 30 days before the wedding, write the message or start from a saved template, then choose whether it waits for review or sends automatically.\n\nYou can add several emails — for example one at 120 days, another at 60 days and another at 30 days. The Final Timings form option is available only for the 30-day email. It adds a secure button that opens that couple's actual Final Timings questionnaire.",
        "action_label": "Open emails & workflow", "action_route": "workflow", "sort_order": 105,
    },
    {
        "slug": "pause-one-follow-up", "title": "How do I pause one follow-up for one couple?",
        "category": "Emails & workflow", "contexts": ["weddings", "workflow"],
        "keywords": ["pause follow up", "stop first email", "one couple", "individual step", "resume reminder"],
        "summary": "Pause only the unwanted step without disturbing later reminders.",
        "body": "Open the couple's wedding and find Workflow in the side panel. Expand Pause individual steps and pause only the message you do not want.\n\nOther steps remain unchanged. Return to the same control to resume that step for the couple later.",
        "action_label": "Open weddings", "action_route": "weddings", "sort_order": 110,
    },
    {
        "slug": "connect-business-email", "title": "How do I connect my email account?",
        "category": "Email connection", "contexts": ["mailbox", "workflow"], "tour_key": "mailbox",
        "keywords": ["smtp", "imap", "email setup", "mailbox", "connect email", "password", "send receive"],
        "summary": "Add the provider's SMTP and IMAP details, then test each direction.",
        "body": "Open Email connection and enter the sender name, email address, SMTP details for outgoing mail and IMAP details for incoming mail. Save the connection, then test outgoing and incoming separately.\n\nConnecting or testing a mailbox never releases automatic workflows. Some providers require an app password rather than the normal mailbox password.",
        "action_label": "Open email connection", "action_route": "mailbox", "sort_order": 120,
    },
    {
        "slug": "connect-google-calendar", "title": "How do I connect Google Calendar?",
        "category": "Calendar", "contexts": ["calendar", "home"], "tour_key": "calendar",
        "keywords": ["google calendar", "connect calendar", "sync", "calendar account", "events"],
        "summary": "Connect your own Google account, choose a calendar and keep client invitations switched off.",
        "body": "Open Calendar and choose Connect Google Calendar. Sign into the Google account you want to use, approve the requested calendar access, then return to Studio and choose the writable calendar for this business.\n\nSecured weddings and blocked dates sync as private one-way events. Couples are never added as guests, so Google does not email them. Sync safely retries anything waiting or needing attention.",
        "action_label": "Open calendar", "action_route": "calendar", "sort_order": 130,
    },
    {
        "slug": "block-holiday-dates", "title": "How do I block holidays or unavailable dates?",
        "category": "Calendar", "contexts": ["calendar"],
        "keywords": ["block date", "holiday", "unavailable", "time away", "multiple days", "website checker"],
        "summary": "Block one day or a date range from the Calendar screen.",
        "body": "Open Calendar, enter the first and last unavailable dates, give the block a clear label, and save it. Use the same date twice for a single day.\n\nThe block is included in public availability immediately and is added to Google Calendar when a connection is available.",
        "action_label": "Block dates", "action_route": "calendar", "sort_order": 140,
    },
    {
        "slug": "find-a-venue-and-directions", "title": "How do venue search and directions work?",
        "category": "Enquiries", "contexts": ["enquiry", "enquiries", "weddings"],
        "keywords": ["venue", "google places", "address", "maps", "directions", "sat nav"],
        "summary": "Couples can select an exact venue and you can open directions from their wedding.",
        "body": "On the public enquiry form, the couple starts typing a venue name or address and chooses the exact Google result. Manual entry remains available if their venue is not listed.\n\nThe venue name, address and Google Place reference stay with that tenant's enquiry and carry into the wedding. Open the wedding and choose Get directions to launch Google Maps without retyping the address.",
        "action_label": "Open enquiry form", "action_route": "enquiry", "sort_order": 145,
    },
    {
        "slug": "complete-wedding", "title": "How do I complete a wedding?",
        "category": "Weddings", "contexts": ["weddings"],
        "keywords": ["complete wedding", "archive wedding", "finished", "mark complete", "checklist"],
        "summary": "Use the single Wedding complete button when your work is finished.",
        "body": "Open the wedding and select Wedding complete. The booking is marked complete and future workflow actions are stopped.\n\nThere is no blocking checklist: the decision remains yours. Existing invoices, contracts, forms and history stay attached for your records.",
        "action_label": "Open weddings", "action_route": "weddings", "sort_order": 150,
    },
    {
        "slug": "packages-and-extras", "title": "How do packages and add-ons work?",
        "category": "Packages & pricing", "contexts": ["packages", "weddings"], "tour_key": "packages",
        "keywords": ["package", "add on", "extra", "mandatory", "booking fee", "balance due", "pricing"],
        "summary": "Create reusable packages and keep ordinary extras optional.",
        "body": "Open Packages & pricing to add your services, booking fee, balance timing and included items. Add albums, extra hours and other options under Add-ons.\n\nOrdinary extras remain optional and unselected. Use Mandatory only when a charge genuinely cannot be removed, such as agreed travel, and explain why.",
        "action_label": "Open packages & pricing", "action_route": "packages", "sort_order": 160,
    },
    {
        "slug": "nothing-sends-without-you", "title": "Could anything send before I am ready?",
        "category": "Safety", "contexts": ["home", "workflow", "mailbox"],
        "keywords": ["send automatically", "safety pause", "nothing sends", "accidental email", "go live"],
        "summary": "No. New studios and starter workflow steps begin safely paused.",
        "body": "Every new studio starts with Ivory Digital's master automation pause switched on, and every supplied workflow step starts Disabled. Connecting email or editing a template does not change either safety control.\n\nAutomatic delivery is possible only when a step is set to Automatic and the platform master pause has been deliberately released.",
        "action_label": "Review workflow", "action_route": "workflow", "sort_order": 170,
    },
    {
        "slug": "use-today-workspace", "title": "What should I deal with today?",
        "category": "Daily workspace", "contexts": ["home", "enquiries", "weddings"], "tour_key": "home",
        "keywords": ["today", "dashboard", "attention", "tasks", "what next", "daily work"],
        "summary": "See new enquiries, approvals, replies, payments and tasks in one place.",
        "body": "Open Home and scroll to Today. The counters and cards bring together new enquiries, workflow reviews, unread client updates, payments due, private tasks and upcoming weddings.\n\nChoose any card to open the relevant couple or working screen. The setup checklist remains separate, so day-to-day work does not get buried beneath settings.",
        "action_label": "Open Today", "action_route": "home", "sort_order": 180,
    },
    {
        "slug": "use-communications-centre", "title": "Where can I see replies and prepared emails?",
        "category": "Communications", "contexts": ["communications", "weddings", "workflow"], "tour_key": "communications",
        "keywords": ["email history", "inbox", "reply", "review queue", "sent emails", "communications"],
        "summary": "Use Communications for inbox replies, review-first messages, templates and your signature.",
        "body": "Open Communications. Inbox shows messages sent through Studio and replies refreshed from your connected mailbox. Review queue holds workflow emails that need your approval and private tasks that need completing.\n\nTemplates are shortcuts for personal messages and do not send automatically. Signature controls the professional sign-off, logo and award badge added to outgoing Studio email.",
        "action_label": "Open communications", "action_route": "communications", "sort_order": 190,
    },
    {
        "slug": "edit-move-and-organise-wedding", "title": "How do I edit or move a wedding?",
        "category": "Weddings", "contexts": ["weddings", "calendar"], "tour_key": "weddings",
        "keywords": ["edit couple", "change email", "move wedding", "new date", "reschedule", "change venue"],
        "summary": "Edit ordinary details directly, or use Move date for a safe audited reschedule.",
        "body": "Open Weddings and choose the couple. Edit details changes their names, contact information, venue or wedding information. Use Move date when the wedding itself is rescheduled.\n\nMove date checks bookings and unavailable periods first. You can move financial due dates by the same number of days, and Studio also updates future wedding-date reminders and the connected Google Calendar event.",
        "action_label": "Open weddings", "action_route": "weddings", "sort_order": 200,
    },
    {
        "slug": "wedding-notes-tasks-and-files", "title": "Where do I keep notes, tasks and files?",
        "category": "Weddings", "contexts": ["weddings", "home"], "tour_key": "weddings",
        "keywords": ["private note", "task", "reminder", "upload file", "document", "wedding files"],
        "summary": "Keep private working information inside the couple's wedding workspace.",
        "body": "Open the wedding. Overview contains private notes and tasks, while Files keeps PDFs, images, Word files and spreadsheets beside the couple. Notes, tasks and files are Studio-only and never appear in the couple portal.\n\nThe Notes & activity tab also shows the permanent audit history, making it easier to understand what changed and when.",
        "action_label": "Open weddings", "action_route": "weddings", "sort_order": 210,
    },
    {
        "slug": "contact-ivory-digital", "title": "I still need help",
        "category": "Ivory Digital support", "contexts": ["home"],
        "keywords": ["support", "contact", "human", "help me", "problem", "not working", "stuck"],
        "summary": "Contact Ivory Digital when you need a human pair of eyes.",
        "body": "If the answer here does not solve it, contact Ivory Digital and explain what you were trying to do, which screen you were on, and what happened. A screenshot is helpful, but never include a password or recovery code.\n\nEmail sales@ivorydigital.uk and your question can also help improve this guide for every studio.",
        "action_label": "Email Ivory Digital", "action_route": "", "sort_order": 999,
    },
)


DEFAULT_EMAIL_TEMPLATES = (
    {
        "name": "Wedding quote",
        "category": "Quote",
        "subject": "Your wedding quote from {{business_name}}",
        "body": (
            "Hi {{couple_first_name}},\n\n"
            "Thank you for getting in touch about your wedding on {{wedding_date}} at {{venue}}. "
            "I have put together your personal quote and package choices.\n\n"
            "Use the secure button below to compare the packages, choose any extras and accept "
            "the option that feels right for you both.\n\n"
            "{{client_portal_link}}\n\n"
            "If you have any questions at all, just reply to this email."
        ),
    },
    {
        "name": "Personal enquiry reply",
        "category": "Enquiry",
        "subject": "Thank you for your wedding enquiry",
        "body": (
            "Hi {{couple_first_name}},\n\n"
            "Thank you for getting in touch about your wedding on {{wedding_date}} at {{venue}}. "
            "It sounds lovely. I am going through the details personally and will come back to you shortly.\n\n"
            "If there is anything else you would like me to know, simply reply here."
        ),
    },
    {
        "name": "Check the quote arrived",
        "category": "Quote follow-up",
        "subject": "Just checking your wedding quote arrived",
        "body": (
            "Hi {{couple_first_name}},\n\n"
            "I just wanted to make sure your wedding quote arrived safely. There is no pressure at all - "
            "I simply did not want it to be missed.\n\n"
            "Your secure quote is here:\n{{client_portal_link}}\n\n"
            "Please feel free to ask me anything."
        ),
    },
    {
        "name": "Booking confirmed",
        "category": "Booking",
        "subject": "Your wedding booking is confirmed",
        "body": (
            "Hi {{couple_first_name}},\n\n"
            "Thank you - your wedding booking for {{wedding_date}} at {{venue}} is now confirmed. "
            "I am genuinely looking forward to being part of it.\n\n"
            "You can return to your private booking area whenever you need it:\n"
            "{{client_portal_link}}"
        ),
    },
    {
        "name": "Contract signed by both parties",
        "category": "Contract",
        "subject": "Your completed wedding agreement from {{business_name}}",
        "body": (
            "Hi {{couple_first_name}},\n\n"
            "Thank you. Your wedding agreement has now been signed by you and countersigned "
            "automatically on behalf of {{business_name}}.\n\n"
            "A completed PDF copy is attached to this email for your records. You can also return "
            "to your private booking area at any time using the secure button below.\n\n"
            "{{client_portal_link}}"
        ),
    },
    {
        "name": "Friendly balance reminder",
        "category": "Payment",
        "subject": "A quick reminder about your wedding balance",
        "body": (
            "Hi {{couple_first_name}},\n\n"
            "Just a friendly reminder that the remaining balance for your wedding on {{wedding_date}} "
            "is due soon. If you have already paid it, thank you and please ignore this message.\n\n"
            "If you need to check anything with me, just reply here."
        ),
    },
    {
        "name": "Wedding planning check-in",
        "category": "Wedding check-in",
        "subject": "A little check-in before your wedding",
        "body": (
            "Hi {{couple_first_name}},\n\n"
            "I just wanted to check in and say hello as your wedding gets closer. "
            "I am still here, everything is safely in the diary, and I hope all the planning is going well.\n\n"
            "If there is anything you would like to ask or update me about, simply reply to this email."
        ),
    },
    {
        "name": "Final timings request",
        "category": "Final timings",
        "subject": "Your final wedding timings",
        "body": (
            "Hi {{couple_first_name}},\n\n"
            "Your wedding is getting close, so it is time to complete your final timings form. "
            "Please use the secure button below when you are ready.\n\n"
            "{{final_timings_link}}\n\n"
            "If anything is still being decided, fill in what you know and you can return to update it later."
        ),
    },
)


def ensure_starter_email_templates(db: Session, tenant: Tenant) -> None:
    onboarding = dict(tenant.onboarding or {})
    current_version = int(onboarding.get("email_templates_seed_version") or 0)
    if current_version >= 3:
        return
    existing_names = set(db.scalars(select(EmailTemplate.name).where(
        EmailTemplate.tenant_id == tenant.id)).all())
    first_seed = not onboarding.get("email_templates_seeded") and not existing_names
    if first_seed:
        starter_rows = DEFAULT_EMAIL_TEMPLATES
    else:
        upgrade_names = set()
        if current_version < 2:
            upgrade_names.add("Contract signed by both parties")
        if current_version < 3:
            upgrade_names.update({"Wedding planning check-in", "Final timings request"})
        starter_rows = tuple(row for row in DEFAULT_EMAIL_TEMPLATES
                             if row["name"] in upgrade_names)
    for values in starter_rows:
        if values["name"] not in existing_names:
            db.add(EmailTemplate(tenant_id=tenant.id, is_active=True, **values))
    onboarding["email_templates_seeded"] = True
    onboarding["email_templates_seed_version"] = 3
    tenant.onboarding = onboarding
    db.flush()


def ensure_help_catalog(db: Session) -> None:
    existing_rows = {row.slug: row for row in db.scalars(select(HelpArticle)).all()}
    added = False
    for item in DEFAULT_HELP_ARTICLES:
        if item["slug"] not in existing_rows:
            db.add(HelpArticle(is_published=True, **item)); added = True
    # Replace only our known, now-obsolete starter wording. A photographer's
    # own edited help article is left untouched.
    questionnaire_help = existing_rows.get("questionnaires-and-final-timings")
    if questionnaire_help and "enter one question per line" in questionnaire_help.body.lower():
        current = next(item for item in DEFAULT_HELP_ARTICLES if item["slug"] == questionnaire_help.slug)
        questionnaire_help.title = current["title"]
        questionnaire_help.summary = current["summary"]
        questionnaire_help.body = current["body"]
        questionnaire_help.keywords = current["keywords"]
        added = True
    calendar_help = existing_rows.get("connect-google-calendar")
    if calendar_help and "approve the requested calendar access, then return to studio" in calendar_help.body.lower() and "choose the writable calendar" not in calendar_help.body.lower():
        current = next(item for item in DEFAULT_HELP_ARTICLES if item["slug"] == calendar_help.slug)
        calendar_help.title = current["title"]
        calendar_help.summary = current["summary"]
        calendar_help.body = current["body"]
        calendar_help.keywords = current["keywords"]
    contract_help = existing_rows.get("issue-and-sign-contract")
    if contract_help and "photographer countersigns" in contract_help.summary.lower():
        current = next(item for item in DEFAULT_HELP_ARTICLES if item["slug"] == contract_help.slug)
        contract_help.summary = current["summary"]
        contract_help.body = current["body"]
        contract_help.keywords = current["keywords"]
        added = True
        added = True
    for slug, marker in (("turn-enquiry-into-wedding", "start client journey"),
                         ("create-and-send-quote", "prepare quote link")):
        row = existing_rows.get(slug)
        if row and marker in row.body.lower():
            current = next(item for item in DEFAULT_HELP_ARTICLES if item["slug"] == slug)
            for key in ("title", "category", "summary", "body", "keywords", "contexts",
                        "action_label", "action_route", "tour_key", "sort_order"):
                setattr(row, key, current.get(key, getattr(row, key)))
            added = True
    if added:
        db.commit()


def ensure_subscription(db: Session, tenant: Tenant) -> TenantSubscription:
    row = db.get(TenantSubscription, tenant.id)
    if row:
        return row
    trial_days = max(1, (aware(tenant.trial_ends_at).date() - aware(tenant.trial_started_at).date()).days)
    row = TenantSubscription(
        tenant_id=tenant.id,
        billing_status=tenant.status.value,
        trial_days_granted=trial_days,
    )
    db.add(row)
    db.flush()
    return row


def ensure_all_subscriptions(db: Session) -> None:
    added = False
    for tenant in db.scalars(select(Tenant)).all():
        if not db.get(TenantSubscription, tenant.id):
            ensure_subscription(db, tenant)
            added = True
    if added:
        db.commit()


def ensure_compatibility_columns(db: Session) -> None:
    """Add backwards-compatible release fields without replacing tenant data."""
    if db.bind is None:
        return
    if db.bind.dialect.name == "postgresql":
        db.execute(text("ALTER TABLE service_packages ADD COLUMN IF NOT EXISTS information_url VARCHAR(1000) NOT NULL DEFAULT ''"))
        db.execute(text("ALTER TABLE package_add_ons ADD COLUMN IF NOT EXISTS information_url VARCHAR(1000) NOT NULL DEFAULT ''"))
        db.execute(text("ALTER TABLE package_add_ons ADD COLUMN IF NOT EXISTS eligible_package_ids JSON NOT NULL DEFAULT '[]'::json"))
        db.execute(text("ALTER TABLE enquiries ADD COLUMN IF NOT EXISTS venue_details JSON NOT NULL DEFAULT '{}'::json"))
        db.execute(text("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS venue_details JSON NOT NULL DEFAULT '{}'::json"))
        db.execute(text("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS is_provisional BOOLEAN NOT NULL DEFAULT FALSE"))
        db.execute(text("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_bookings_is_provisional ON bookings (is_provisional)"))
        db.execute(text("ALTER TABLE tenant_calendar_connections ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ"))
    elif db.bind.dialect.name == "sqlite":
        for table_name in ("service_packages", "package_add_ons"):
            columns = {row[1] for row in db.execute(text(f"PRAGMA table_info({table_name})"))}
            if "information_url" not in columns:
                db.execute(text(f"ALTER TABLE {table_name} ADD COLUMN information_url VARCHAR(1000) NOT NULL DEFAULT ''"))
        add_on_columns = {row[1] for row in db.execute(text("PRAGMA table_info(package_add_ons)"))}
        if add_on_columns and "eligible_package_ids" not in add_on_columns:
            db.execute(text("ALTER TABLE package_add_ons ADD COLUMN eligible_package_ids JSON NOT NULL DEFAULT '[]'"))
        for table_name in ("enquiries", "bookings"):
            columns = {row[1] for row in db.execute(text(f"PRAGMA table_info({table_name})"))}
            if columns and "venue_details" not in columns:
                db.execute(text(f"ALTER TABLE {table_name} ADD COLUMN venue_details JSON NOT NULL DEFAULT '{{}}'"))
        booking_columns = {row[1] for row in db.execute(text("PRAGMA table_info(bookings)"))}
        if booking_columns and "is_provisional" not in booking_columns:
            db.execute(text("ALTER TABLE bookings ADD COLUMN is_provisional BOOLEAN NOT NULL DEFAULT 0"))
        if booking_columns and "promoted_at" not in booking_columns:
            db.execute(text("ALTER TABLE bookings ADD COLUMN promoted_at DATETIME"))
        calendar_columns = {row[1] for row in db.execute(text("PRAGMA table_info(tenant_calendar_connections)"))}
        if calendar_columns and "last_synced_at" not in calendar_columns:
            db.execute(text("ALTER TABLE tenant_calendar_connections ADD COLUMN last_synced_at DATETIME"))
    # Earlier releases marked an enquiry converted as soon as quote work began.
    # Reclassify only unaccepted/uninvoiced records as provisional; confirmed
    # commercial records always remain real Weddings.
    if inspect(db.bind).has_table("booking_journeys"):
        journeys = db.scalars(select(BookingJourney).where(
            BookingJourney.enquiry_id.is_not(None))).all()
        for journey in journeys:
            enquiry = db.get(Enquiry, journey.enquiry_id)
            booking = db.get(Booking, journey.booking_id)
            if not enquiry or not booking:
                continue
            has_invoice = bool(db.scalar(select(BookingInvoice.id).where(
                BookingInvoice.booking_id == booking.id).limit(1)))
            accepted = bool(journey.accepted_quote)
            if not accepted and not has_invoice and booking.status in {
                    "enquiry", "quote_preparation", "awaiting_quote_acceptance"}:
                booking.is_provisional = True
                quote_status = (journey.quote_state or {}).get("status")
                enquiry.status = ("quote_sent" if quote_status == "sent"
                                  else "quote_draft" if (journey.quote_state or {}).get("packages")
                                  else "new")
            else:
                booking.is_provisional = False
                enquiry.status = "booked"
                booking.promoted_at = booking.promoted_at or utcnow()
    db.commit()


def ensure_questionnaire_templates(db: Session, tenant: Tenant) -> None:
    existing = {row.form_type for row in db.scalars(select(QuestionnaireTemplate).where(
        QuestionnaireTemplate.tenant_id == tenant.id)).all()}
    for form_type in ("booking", "final_timings"):
        if form_type not in existing:
            db.add(QuestionnaireTemplate(tenant_id=tenant.id, **default_questionnaire(form_type)))
    db.flush()


def ensure_all_questionnaire_templates(db: Session) -> None:
    for tenant in db.scalars(select(Tenant)).all():
        ensure_questionnaire_templates(db, tenant)
    db.commit()


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
        ensure_compatibility_columns(db)
        bootstrap_platform_admin(db)
        ensure_help_catalog(db)
        ensure_all_subscriptions(db)
        ensure_all_questionnaire_templates(db)
        install_postgres_rls(db)
        db.commit()
    yield


app = FastAPI(
    title="Ivory Digital Booking System",
    version="0.5.6.2-scheduled-emails",
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
    return {"status": "ok", "build": "2026.09.21-phase-five-six-two-scheduled-emails", "service": "ivory-booking-saas"}


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


def pause_open_workflow_actions(db: Session, tenant_id: str) -> None:
    actions = db.scalars(select(WorkflowAction).where(
        WorkflowAction.tenant_id == tenant_id,
        WorkflowAction.completed_at.is_(None),
        WorkflowAction.status.notin_(["sent", "completed", "cancelled"]))).all()
    for action in actions:
        details = dict(action.payload or {})
        if action.status != "paused":
            details["resume_status"] = action.status
        action.status = "paused"
        action.payload = details


def subscription_json(row: TenantSubscription, tenant: Tenant) -> dict:
    today = date.today()
    trial_ends = aware(tenant.trial_ends_at).date()
    overdue_days = max(0, (today - row.next_payment_due).days) if row.next_payment_due else 0
    return {
        "tenant_id": tenant.id,
        "plan_name": row.plan_name,
        "price_pence": row.price_pence,
        "billing_cycle": row.billing_cycle,
        "billing_status": row.billing_status,
        "account_status": tenant.status.value,
        "trial_days_granted": row.trial_days_granted,
        "trial_ends_at": tenant.trial_ends_at.isoformat(),
        "trial_days_remaining": max(0, (trial_ends - today).days),
        "next_payment_due": row.next_payment_due.isoformat() if row.next_payment_due else None,
        "overdue_days": overdue_days,
        "grace_days": row.grace_days,
        "auto_suspend": row.auto_suspend,
        "provider": row.provider,
        "last_payment_at": row.last_payment_at.isoformat() if row.last_payment_at else None,
        "suspended_at": row.suspended_at.isoformat() if row.suspended_at else None,
        "suspension_reason": row.suspension_reason,
    }


def platform_payment_json(row: PlatformBillingPayment) -> dict:
    return {
        "id": row.id,
        "amount_pence": row.amount_pence,
        "paid_date": row.paid_date.isoformat(),
        "payment_method": row.payment_method,
        "reference": row.reference,
        "notes": row.notes,
        "covers_until": row.covers_until.isoformat() if row.covers_until else None,
        "created_at": row.created_at.isoformat(),
    }


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
        "branding": row.branding or {},
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
    subscriptions = list(db.scalars(select(TenantSubscription)).all())
    month_start = date.today().replace(day=1)
    collected_this_month = db.scalar(select(func.coalesce(func.sum(PlatformBillingPayment.amount_pence), 0)).where(
        PlatformBillingPayment.paid_date >= month_start)) or 0
    monthly_recurring = sum(
        row.price_pence if row.billing_cycle == "monthly" else row.price_pence // 12
        if row.billing_cycle == "annual" else 0
        for row in subscriptions if row.billing_status in {"active", "past_due"}
    )
    return {
        "businesses": len(tenants),
        "trial": statuses["trial"],
        "active": statuses["active"],
        "suspended": statuses["suspended"],
        "pending_invitations": pending_invites,
        "automatic_messages_paused": sum(1 for row in tenants if row.automations_paused),
        "monthly_recurring_pence": monthly_recurring,
        "collected_this_month_pence": collected_this_month,
        "past_due": sum(1 for row in subscriptions if row.billing_status == "past_due"),
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
        onboarding={"business": False, "branding": False, "enquiry_form": False,
                    "packages": False, "templates": False, "mailbox": False,
                    "calendar": False},
        branding={"display_name": payload.display_name.strip(), "accent_colour": "#a9782e",
                  "welcome_message": "Welcome to your private booking area."},
        automations_paused=True,
    )
    db.add(tenant)
    db.flush()
    db.add(TenantSubscription(
        tenant_id=tenant.id,
        billing_status="trial",
        trial_days_granted=settings.trial_days,
    ))
    ensure_questionnaire_templates(db, tenant)
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
    subscription = ensure_subscription(db, tenant)
    subscription.billing_status = tenant.status.value
    if tenant.status in {TenantStatus.SUSPENDED, TenantStatus.CANCELLED}:
        tenant.automations_paused = True
        subscription.suspended_at = utcnow()
        subscription.suspension_reason = payload.reason
        pause_open_workflow_actions(db, tenant.id)
    elif tenant.status == TenantStatus.ACTIVE:
        subscription.suspended_at = None
        subscription.suspension_reason = ""
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


@app.get("/api/manager/billing")
def manager_billing(_: User = Depends(platform_admin), db: Session = Depends(get_db)):
    tenants = list(db.scalars(select(Tenant).order_by(Tenant.display_name)).all())
    accounts = []
    for tenant in tenants:
        subscription = ensure_subscription(db, tenant)
        accounts.append({
            "tenant": tenant_json(tenant, db),
            "subscription": subscription_json(subscription, tenant),
        })
    month_start = date.today().replace(day=1)
    collected = db.scalar(select(func.coalesce(func.sum(PlatformBillingPayment.amount_pence), 0)).where(
        PlatformBillingPayment.paid_date >= month_start)) or 0
    recurring = sum(
        item["subscription"]["price_pence"]
        if item["subscription"]["billing_cycle"] == "monthly"
        else item["subscription"]["price_pence"] // 12
        if item["subscription"]["billing_cycle"] == "annual"
        else 0
        for item in accounts
        if item["subscription"]["billing_status"] in {"active", "past_due"}
    )
    db.commit()
    return {
        "summary": {
            "projected_monthly_pence": recurring,
            "collected_this_month_pence": collected,
            "trials": sum(1 for item in accounts if item["subscription"]["billing_status"] == "trial"),
            "past_due": sum(1 for item in accounts if item["subscription"]["billing_status"] == "past_due"),
            "suspended": sum(1 for item in accounts if item["tenant"]["status"] == "suspended"),
        },
        "accounts": accounts,
    }


@app.get("/api/manager/tenants/{tenant_id}/billing")
def tenant_billing(tenant_id: str, _: User = Depends(platform_admin), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Business not found")
    subscription = ensure_subscription(db, tenant)
    payments = db.scalars(select(PlatformBillingPayment).where(
        PlatformBillingPayment.tenant_id == tenant.id
    ).order_by(PlatformBillingPayment.paid_date.desc(), PlatformBillingPayment.created_at.desc())).all()
    result = {
        "tenant": tenant_json(tenant, db),
        "subscription": subscription_json(subscription, tenant),
        "payments": [platform_payment_json(row) for row in payments],
    }
    db.commit()
    return result


@app.put("/api/manager/tenants/{tenant_id}/billing")
def update_tenant_billing(tenant_id: str, payload: BillingSettingsIn, request: Request,
                          admin: User = Depends(platform_admin_write), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Business not found")
    subscription = ensure_subscription(db, tenant)
    before = subscription_json(subscription, tenant)
    subscription.plan_name = payload.plan_name.strip()
    subscription.price_pence = payload.price_pence
    subscription.billing_cycle = payload.billing_cycle
    subscription.next_payment_due = payload.next_payment_due
    subscription.grace_days = payload.grace_days
    subscription.auto_suspend = payload.auto_suspend
    audit(db, "billing_settings_changed", "tenant_subscription", tenant.id,
          actor=admin, tenant_id=tenant.id, request=request,
          detail={"before": before, "after": payload.model_dump(mode="json")})
    db.commit()
    return subscription_json(subscription, tenant)


@app.post("/api/manager/tenants/{tenant_id}/trial")
def extend_tenant_trial(tenant_id: str, payload: TrialExtensionIn, request: Request,
                        admin: User = Depends(platform_admin_write), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Business not found")
    old_end = tenant.trial_ends_at.isoformat()
    tenant.status = TenantStatus.TRIAL
    tenant.trial_ends_at = utcnow() + timedelta(days=payload.days)
    tenant.automations_paused = True
    subscription = ensure_subscription(db, tenant)
    subscription.billing_status = "trial"
    subscription.trial_days_granted = payload.days
    subscription.next_payment_due = None
    subscription.suspended_at = None
    subscription.suspension_reason = ""
    pause_open_workflow_actions(db, tenant.id)
    audit(db, "trial_extended", "tenant", tenant.id, actor=admin,
          tenant_id=tenant.id, request=request,
          detail={"days": payload.days, "old_end": old_end,
                  "new_end": tenant.trial_ends_at.isoformat(), "note": payload.note})
    db.commit()
    return {"tenant": tenant_json(tenant, db), "subscription": subscription_json(subscription, tenant)}


@app.post("/api/manager/tenants/{tenant_id}/billing/payments", status_code=201)
def record_platform_payment(tenant_id: str, payload: PlatformPaymentIn, request: Request,
                            admin: User = Depends(platform_admin_write), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Business not found")
    subscription = ensure_subscription(db, tenant)
    payment = PlatformBillingPayment(
        tenant_id=tenant.id,
        amount_pence=payload.amount_pence,
        paid_date=payload.paid_date,
        payment_method=payload.payment_method,
        reference=payload.reference.strip(),
        notes=payload.notes.strip(),
        covers_until=payload.covers_until,
        created_by_user_id=admin.id,
    )
    db.add(payment)
    subscription.last_payment_at = utcnow()
    if payload.covers_until:
        subscription.next_payment_due = payload.covers_until
    if payload.reactivate:
        tenant.status = TenantStatus.ACTIVE
        subscription.billing_status = "active"
        subscription.suspended_at = None
        subscription.suspension_reason = ""
        tenant.automations_paused = True
        pause_open_workflow_actions(db, tenant.id)
    db.flush()
    audit(db, "platform_payment_recorded", "platform_billing_payment", payment.id,
          actor=admin, tenant_id=tenant.id, request=request,
          detail={"amount_pence": payment.amount_pence, "paid_date": payment.paid_date.isoformat(),
                  "method": payment.payment_method, "reference": payment.reference,
                  "reactivated": payload.reactivate})
    result = platform_payment_json(payment)
    db.commit()
    return result


@app.post("/api/manager/tenants/{tenant_id}/billing/suspend")
def suspend_tenant_account(tenant_id: str, payload: AccountAccessIn, request: Request,
                           admin: User = Depends(platform_admin_write), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Business not found")
    old = tenant.status.value
    tenant.status = TenantStatus.SUSPENDED
    tenant.automations_paused = True
    subscription = ensure_subscription(db, tenant)
    subscription.billing_status = "suspended"
    subscription.suspended_at = utcnow()
    subscription.suspension_reason = payload.reason.strip()
    if payload.next_payment_due:
        subscription.next_payment_due = payload.next_payment_due
    pause_open_workflow_actions(db, tenant.id)
    audit(db, "tenant_suspended", "tenant", tenant.id, actor=admin,
          tenant_id=tenant.id, request=request,
          detail={"from": old, "reason": payload.reason, "data_deleted": False})
    db.commit()
    return {"tenant": tenant_json(tenant, db), "subscription": subscription_json(subscription, tenant)}


@app.post("/api/manager/tenants/{tenant_id}/billing/reactivate")
def reactivate_tenant_account(tenant_id: str, payload: AccountAccessIn, request: Request,
                              admin: User = Depends(platform_admin_write), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Business not found")
    old = tenant.status.value
    tenant.status = TenantStatus.ACTIVE
    tenant.automations_paused = True
    subscription = ensure_subscription(db, tenant)
    subscription.billing_status = "active"
    subscription.suspended_at = None
    subscription.suspension_reason = ""
    if payload.next_payment_due:
        subscription.next_payment_due = payload.next_payment_due
    pause_open_workflow_actions(db, tenant.id)
    audit(db, "tenant_reactivated", "tenant", tenant.id, actor=admin,
          tenant_id=tenant.id, request=request,
          detail={"from": old, "reason": payload.reason,
                  "next_payment_due": subscription.next_payment_due.isoformat()
                  if subscription.next_payment_due else None,
                  "automations_remain_paused": True})
    db.commit()
    return {"tenant": tenant_json(tenant, db), "subscription": subscription_json(subscription, tenant)}


@app.get("/api/manager/tenants/{tenant_id}/support")
def support_view(tenant_id: str, request: Request,
                 admin: User = Depends(platform_admin), db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Business not found")
    subscription = ensure_subscription(db, tenant)
    client_count = db.scalar(select(func.count(Client.id)).where(Client.tenant_id == tenant.id)) or 0
    booking_count = db.scalar(select(func.count(Booking.id)).where(
        Booking.tenant_id == tenant.id, Booking.is_provisional.is_(False))) or 0
    audit(db, "support_view_opened", "tenant", tenant.id, actor=admin,
          tenant_id=tenant.id, request=request,
          detail={"scope": "health_and_counts_only"})
    db.commit()
    return {"tenant": tenant_json(tenant, db), "billing": subscription_json(subscription, tenant),
            "client_count": client_count,
            "booking_count": booking_count, "storage_key": tenant.storage_key,
            "data_access": "No couple details opened"}


@app.get("/api/manager/audit")
def manager_audit(_: User = Depends(platform_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100)).all()
    return [{"id": row.id, "action": row.action, "subject_type": row.subject_type,
             "subject_id": row.subject_id, "tenant_id": row.tenant_id,
             "detail": row.detail, "created_at": row.created_at.isoformat()} for row in rows]


def help_article_json(row: HelpArticle, include_body: bool = True) -> dict:
    result = {
        "id": row.id, "slug": row.slug, "title": row.title,
        "category": row.category, "summary": row.summary,
        "keywords": row.keywords or [], "contexts": row.contexts or [],
        "action_label": row.action_label, "action_route": row.action_route,
        "tour_key": row.tour_key, "is_published": row.is_published,
        "sort_order": row.sort_order, "updated_at": row.updated_at.isoformat(),
    }
    if include_body:
        result["body"] = row.body
    return result


@app.get("/api/manager/help/articles")
def manager_help_articles(_: User = Depends(platform_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(HelpArticle).order_by(
        HelpArticle.sort_order, HelpArticle.category, HelpArticle.title)).all()
    return [help_article_json(row) for row in rows]


@app.post("/api/manager/help/articles", status_code=201)
def create_help_article(payload: HelpArticleIn, request: Request,
                        admin: User = Depends(platform_admin_write), db: Session = Depends(get_db)):
    if db.scalar(select(HelpArticle.id).where(HelpArticle.slug == payload.slug)):
        raise HTTPException(409, "That help article address is already in use")
    row = HelpArticle(**payload.model_dump())
    db.add(row); db.flush()
    audit(db, "help_article_created", "help_article", row.id, actor=admin,
          request=request, detail={"slug": row.slug, "title": row.title})
    result = help_article_json(row)
    db.commit()
    return result


@app.put("/api/manager/help/articles/{article_id}")
def update_help_article(article_id: str, payload: HelpArticleIn, request: Request,
                        admin: User = Depends(platform_admin_write), db: Session = Depends(get_db)):
    row = db.get(HelpArticle, article_id)
    if not row:
        raise HTTPException(404, "Help article not found")
    duplicate = db.scalar(select(HelpArticle.id).where(
        HelpArticle.slug == payload.slug, HelpArticle.id != row.id))
    if duplicate:
        raise HTTPException(409, "That help article address is already in use")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    audit(db, "help_article_updated", "help_article", row.id, actor=admin,
          request=request, detail={"slug": row.slug, "title": row.title,
                                   "published": row.is_published})
    result = help_article_json(row)
    db.commit()
    return result


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


HELP_STOP_WORDS = {
    "a", "about", "an", "and", "are", "can", "do", "for", "from", "how",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "the", "this",
    "to", "what", "when", "where", "with",
}


def help_words(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.lower())
            if len(word) > 1 and word not in HELP_STOP_WORDS}


def help_match_score(article: HelpArticle, question: str, context: str) -> int:
    normal = " ".join(re.findall(r"[a-z0-9]+", question.lower()))
    tokens = help_words(question)
    title_words = help_words(article.title)
    keyword_text = " ".join(str(value).lower() for value in (article.keywords or []))
    keyword_words = help_words(keyword_text)
    content_words = help_words(f"{article.summary} {article.body}")
    score = len(tokens & title_words) * 5
    score += len(tokens & keyword_words) * 4
    score += min(len(tokens & content_words), 5)
    for keyword in article.keywords or []:
        phrase = " ".join(re.findall(r"[a-z0-9]+", str(keyword).lower()))
        if phrase and phrase in normal:
            score += 8
    if context in (article.contexts or []):
        score += 3
    if article.action_route == context:
        score += 1
    return score


def help_suggestions(rows: list[HelpArticle], context: str,
                     exclude_id: str | None = None, limit: int = 4) -> list[dict]:
    ordered = sorted(rows, key=lambda row: (
        0 if context in (row.contexts or []) else 1,
        row.sort_order, row.title.lower()))
    return [help_article_json(row, include_body=False) for row in ordered
            if row.id != exclude_id][:limit]


@app.get("/api/studio/help/articles")
def studio_help_articles(context: str = "home", _=Depends(studio_context),
                         db: Session = Depends(get_db)):
    rows = list(db.scalars(select(HelpArticle).where(
        HelpArticle.is_published.is_(True)).order_by(
        HelpArticle.sort_order, HelpArticle.title)).all())
    return {
        "context": context,
        "articles": [help_article_json(row, include_body=False) for row in rows],
        "suggestions": help_suggestions(rows, context),
        "privacy": "Questions are answered inside Ivory Digital and are not sent to an outside AI service.",
    }


@app.post("/api/studio/help/ask")
def ask_studio_help(payload: HelpAskIn, _session: UserSession = Depends(require_csrf),
                    db: Session = Depends(get_db)):
    # Resolve membership first so a signed-in user can never use this as a public endpoint.
    membership_for(db, _session.user)
    rows = list(db.scalars(select(HelpArticle).where(
        HelpArticle.is_published.is_(True)).order_by(HelpArticle.sort_order)).all())
    ranked = sorted(((help_match_score(row, payload.question, payload.context), row)
                     for row in rows), key=lambda item: (-item[0], item[1].sort_order))
    score, match = ranked[0] if ranked else (0, None)
    if not match or score < 4:
        return {
            "matched": False,
            "answer": "I could not find a confident answer to that yet. Try one of the suggested questions below, or contact Ivory Digital and we will help personally.",
            "suggestions": help_suggestions(rows, payload.context),
            "support_email": "sales@ivorydigital.uk",
        }
    article = help_article_json(match)
    return {
        "matched": True,
        "confidence": "high" if score >= 16 else "good" if score >= 9 else "possible",
        "answer": match.body,
        "article": article,
        "suggestions": help_suggestions(rows, payload.context, match.id, 3),
        "support_email": "sales@ivorydigital.uk",
    }


def package_json(row: ServicePackage) -> dict:
    return {"id": row.id, "name": row.name, "short_description": row.short_description,
            "price_pence": row.price_pence, "booking_fee_pence": row.booking_fee_pence,
            "balance_due_days": row.balance_due_days, "inclusions": row.inclusions or [],
            "information_url": row.information_url or "",
            "is_featured": row.is_featured, "is_active": row.is_active,
            "sort_order": row.sort_order}


def add_on_json(row: PackageAddOn) -> dict:
    return {"id": row.id, "name": row.name, "description": row.description,
            "information_url": row.information_url or "",
            "price_pence": row.price_pence, "selection_mode": row.selection_mode,
            "mandatory_reason": row.mandatory_reason,
            "eligible_package_ids": row.eligible_package_ids or [],
            "is_active": row.is_active,
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
    ("Send final timings form", "wedding_date", "before", 30, "days", "email", "Your final wedding timings", "Hi {{couple_first_name}},\n\nYour wedding is getting close. Please complete the final timings form in your private client area when you are ready.\n\n{{final_timings_link}}\n\n{{business_name}}"),
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
        final_step = db.scalar(select(WorkflowStep).where(
            WorkflowStep.tenant_id == tenant.id,
            WorkflowStep.workflow_id == workflow.id,
            WorkflowStep.name == "Send final timings form").limit(1))
        legacy_body = ("Hi {{couple_first_name}},\n\nYour wedding is getting close. "
                       "Please complete the final timings form in your private client area when you are ready.\n\n"
                       "{{business_name}}")
        if final_step and final_step.message_body == legacy_body:
            final_step.message_body = legacy_body.replace(
                "\n\n{{business_name}}", "\n\n{{final_timings_link}}\n\n{{business_name}}")
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


def validate_add_on_packages(db: Session, tenant_id: str, payload: AddOnIn) -> None:
    wanted = set(payload.eligible_package_ids)
    if not wanted:
        return
    found = set(db.scalars(select(ServicePackage.id).where(
        ServicePackage.tenant_id == tenant_id,
        ServicePackage.id.in_(wanted))).all())
    if found != wanted:
        raise HTTPException(422, "One or more eligible packages are not available in this studio")


def validate_workflow_step(payload: WorkflowStepIn) -> None:
    if "{{final_timings_link}}" not in payload.message_body:
        return
    is_thirty_day_email = (
        payload.action_type == "email"
        and payload.trigger_event == "wedding_date"
        and payload.timing_direction == "before"
        and payload.offset_unit == "days"
        and payload.offset_value == 30
    )
    if not is_thirty_day_email:
        raise HTTPException(
            422, "The Final Timings form can only be included in an email 30 days before the wedding"
        )


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


def google_places_config() -> dict:
    regions = [item.strip().lower() for item in settings.google_places_region_codes.split(",")
               if item.strip()][:15]
    return {"configured": bool(settings.google_maps_browser_api_key),
            "api_key": settings.google_maps_browser_api_key,
            "region_codes": regions or ["gb"],
            "manual_entry_available": True}


def venue_details_dict(value) -> dict:
    if value is None:
        return {}
    data = value.model_dump() if hasattr(value, "model_dump") else dict(value)
    return {key: item for key, item in data.items() if item not in {None, ""}}


def venue_maps_url(venue: str | None, details: dict | None = None) -> str | None:
    details = details or {}
    destination = details.get("formatted_address") or details.get("name") or venue
    if not destination:
        return None
    params = {"api": "1", "destination": str(destination)}
    if details.get("place_id"):
        params["destination_place_id"] = str(details["place_id"])
    return f"https://www.google.com/maps/dir/?{urlencode(params)}"


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
    booking_count = db.scalar(select(func.count(Booking.id)).where(
        Booking.tenant_id == tenant.id, Booking.is_provisional.is_(False))) or 0
    enquiry_count = db.scalar(select(func.count(Enquiry.id)).where(
        Enquiry.tenant_id == tenant.id,
        Enquiry.status.notin_(["closed", "booked"]))) or 0
    return {
        "user": {"full_name": session.user.full_name, "email": session.user.email,
                 "role": membership.role.value},
        "tenant": tenant_json(tenant, db),
        "onboarding": tenant.onboarding or {},
        "client_count": client_count,
        "booking_count": booking_count,
        "enquiry_count": enquiry_count,
        "phase": "Complete journey with guided help and enquiry sharing",
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
    validate_add_on_packages(db, tenant.id, payload)
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
    validate_add_on_packages(db, tenant.id, payload)
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
    validate_workflow_step(payload)
    membership, tenant = studio_write_context(session, db)
    workflow = db.scalar(select(Workflow).where(Workflow.id == workflow_id,
                         Workflow.tenant_id == membership.tenant_id))
    if not workflow: raise HTTPException(404, "Workflow not found")
    save_workflow_revision(db, workflow, session.user)
    data = payload.model_dump()
    data["is_paused"] = True  # Configuration cannot accidentally start sending.
    row = WorkflowStep(tenant_id=tenant.id, workflow_id=workflow.id, **data)
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
    validate_workflow_step(payload)
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


@app.get("/api/studio/enquiry-form/qr")
def download_enquiry_qr(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    config = db.get(EnquiryFormConfig, tenant.id)
    if not config or not config.is_published:
        raise HTTPException(409, "Publish the enquiry form before downloading its QR code")
    public_url = f"{settings.client_url.rstrip('/')}/{tenant.slug}/enquire"
    buffer = io.BytesIO()
    qrcode.make(public_url, image_factory=qrcode.image.svg.SvgPathImage).save(buffer)
    filename = f"{tenant.slug}-enquiry-form-qr.svg"
    return Response(content=buffer.getvalue(), media_type="image/svg+xml",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/studio/enquiries")
def list_enquiries(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    rows = db.scalars(select(Enquiry).where(Enquiry.tenant_id == tenant.id)
                      .order_by(Enquiry.created_at.desc()).limit(250)).all()
    result = []
    for row in rows:
        journey = db.scalar(select(BookingJourney).where(
            BookingJourney.tenant_id == tenant.id,
            BookingJourney.enquiry_id == row.id))
        booking = db.get(Booking, journey.booking_id) if journey else None
        quote = dict(journey.quote_state or {}) if journey else {}
        answers = db.scalars(select(EnquiryAnswer).where(
            EnquiryAnswer.tenant_id == tenant.id, EnquiryAnswer.enquiry_id == row.id
        ).order_by(EnquiryAnswer.sort_order)).all()
        display_status = row.status
        if (booking and booking.is_provisional and quote.get("status") == "sent"
                and quote.get("expires_on")):
            try:
                if date.fromisoformat(quote["expires_on"]) < date.today():
                    display_status = "quote_expired"
            except (TypeError, ValueError):
                pass
        result.append({"id": row.id, "first_name": row.first_name, "partner_name": row.partner_name,
             "email": row.email, "phone": row.phone,
             "event_date": row.event_date.isoformat() if row.event_date else None,
             "venue": row.venue, "venue_details": row.venue_details or {},
             "venue_maps_url": venue_maps_url(row.venue, row.venue_details),
             "package_interest": row.package_interest,
             "message": row.message, "status": display_status,
             "booking_id": booking.id if booking else None,
             "is_provisional": booking.is_provisional if booking else True,
             "quote_status": quote.get("status"),
             "quote_updated_at": quote.get("updated_at"),
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
    row = Booking(tenant_id=membership.tenant_id,
                  **payload.model_dump(exclude={"venue_details"}),
                  venue_details=venue_details_dict(payload.venue_details))
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


def note_json(row: BookingNote) -> dict:
    return {"id": row.id, "body": row.body, "created_at": row.created_at.isoformat()}


def task_json(row: StudioTask) -> dict:
    return {"id": row.id, "booking_id": row.booking_id, "title": row.title,
            "notes": row.notes, "due_date": row.due_date.isoformat() if row.due_date else None,
            "status": row.status,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "created_at": row.created_at.isoformat()}


def document_json(row: BookingDocument) -> dict:
    return {"id": row.id, "booking_id": row.booking_id,
            "name": row.original_name, "content_type": row.content_type,
            "size_bytes": row.size_bytes, "description": row.description,
            "created_at": row.created_at.isoformat(),
            "download_url": f"/api/studio/documents/{row.id}"}


def email_json(row: EmailMessage, include_body: bool = True) -> dict:
    result = {"id": row.id, "booking_id": row.booking_id,
              "direction": row.direction, "folder": row.folder,
              "sender": row.sender, "recipient": row.recipient,
              "subject": row.subject, "status": row.status, "error": row.error,
              "is_read": row.is_read, "attachments": row.attachments or [],
              "sent_at": row.sent_at.isoformat()}
    if include_body:
        result.update({"body_text": row.body_text, "body_html": row.body_html})
    return result


def create_studio_notification(db: Session, tenant: Tenant, booking: Booking | None,
                               kind: str, title: str, body: str) -> StudioNotification:
    branding = db.get(TenantEmailBranding, tenant.id)
    row = StudioNotification(
        tenant_id=tenant.id, booking_id=booking.id if booking else None,
        kind=kind, title=title, body=body,
        email_status="queued" if branding and branding.owner_notifications_enabled else "not_requested",
    )
    db.add(row)
    return row


def contract_json(row: BookingContract | None) -> dict | None:
    if not row:
        return None
    return {"id": row.id, "title": row.title, "version": row.version,
            "body": row.body_snapshot, "client_name": row.client_name,
            "client_signed_at": row.client_signed_at.isoformat() if row.client_signed_at else None,
            "supplier_name": row.supplier_name,
            "supplier_signed_at": row.supplier_signed_at.isoformat() if row.supplier_signed_at else None}


def issue_active_contract_snapshot(db: Session, tenant: Tenant,
                                   booking: Booking) -> BookingContract | None:
    """Attach the latest active agreement without inventing legal wording."""
    template = db.scalar(select(TenantContractTemplate).where(
        TenantContractTemplate.tenant_id == tenant.id,
        TenantContractTemplate.is_active.is_(True)
    ).order_by(TenantContractTemplate.updated_at.desc()).limit(1))
    if not template:
        return None
    row = db.scalar(select(BookingContract).where(
        BookingContract.tenant_id == tenant.id,
        BookingContract.booking_id == booking.id))
    if row and (row.client_signed_at or row.supplier_signed_at):
        return row
    if row:
        row.template_id = template.id
        row.title = template.name
        row.version = template.version
        row.body_snapshot = template.body
    else:
        row = BookingContract(
            tenant_id=tenant.id, booking_id=booking.id,
            template_id=template.id, title=template.name,
            version=template.version, body_snapshot=template.body,
        )
        db.add(row)
    db.flush()
    return row


def automatic_supplier_name(db: Session, tenant: Tenant) -> str:
    branding = db.get(TenantEmailBranding, tenant.id)
    if branding and (branding.signature_name or "").strip():
        return branding.signature_name.strip()
    owner = db.scalar(select(User).join(
        Membership, Membership.user_id == User.id
    ).where(
        Membership.tenant_id == tenant.id,
        Membership.role == MembershipRole.OWNER,
        User.is_active.is_(True),
    ).order_by(Membership.created_at).limit(1))
    return owner.full_name.strip() if owner and owner.full_name.strip() else tenant.display_name


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
    notes = db.scalars(select(BookingNote).where(
        BookingNote.tenant_id == booking.tenant_id,
        BookingNote.booking_id == booking.id).order_by(BookingNote.created_at.desc())).all()
    tasks = db.scalars(select(StudioTask).where(
        StudioTask.tenant_id == booking.tenant_id,
        StudioTask.booking_id == booking.id).order_by(StudioTask.status, StudioTask.due_date)).all()
    documents = db.scalars(select(BookingDocument).where(
        BookingDocument.tenant_id == booking.tenant_id,
        BookingDocument.booking_id == booking.id).order_by(BookingDocument.created_at.desc())).all()
    emails = db.scalars(select(EmailMessage).where(
        EmailMessage.tenant_id == booking.tenant_id,
        EmailMessage.booking_id == booking.id).order_by(EmailMessage.sent_at.desc()).limit(100)).all()
    activity = db.scalars(select(AuditLog).where(
        AuditLog.tenant_id == booking.tenant_id,
        AuditLog.subject_id == booking.id).order_by(AuditLog.created_at.desc()).limit(100)).all()
    result = {"id": booking.id, "title": booking.title,
              "event_date": booking.event_date.isoformat() if booking.event_date else None,
              "venue": booking.venue, "venue_details": booking.venue_details or {},
              "venue_maps_url": venue_maps_url(booking.venue, booking.venue_details),
              "status": booking.status, "is_provisional": booking.is_provisional,
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
              "notes": [note_json(item) for item in notes],
              "tasks": [task_json(item) for item in tasks],
              "documents": [document_json(item) for item in documents],
              "emails": [email_json(item) for item in emails],
              "activity": [{"id": item.id, "action": item.action,
                            "detail": item.detail or {},
                            "created_at": item.created_at.isoformat()} for item in activity],
              "workflow_actions": [{"id": item.id, "step_id": item.step_id,
                                      "trigger_key": item.trigger_key,
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
        action_payload = {"name": step.name, "action_type": step.action_type,
                          "subject": step.subject, "message_body": step.message_body,
                          "task_title": step.task_title,
                          "resume_status": resume_status}
        if step.action_type == "email" and "{{final_timings_link}}" in step.message_body:
            final_link = f"{portal_url(journey)}#final-timings"
            action_payload.update({"client_portal_link": final_link,
                                   "final_timings_link": final_link,
                                   "action_label": "Complete your final timings"})
        elif step.action_type == "email" and "{{client_portal_link}}" in step.message_body:
            action_payload.update({"client_portal_link": portal_url(journey),
                                   "action_label": "Open your private booking"})
        db.add(WorkflowAction(tenant_id=tenant.id, booking_id=booking.id,
                              step_id=step.id, trigger_key=trigger, mode=mode,
                              due_at=due, status=status_value,
                              payload=action_payload))
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
    params = {"sendUpdates": "none"} if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} else None
    return httpx.request(method, f"{GOOGLE_CALENDAR_API}{path}",
                         headers={"Authorization": f"Bearer {access_token}"},
                         params=params, json=payload,
                         timeout=settings.google_calendar_timeout_seconds)


def google_calendar_choices(connection: TenantCalendarConnection) -> list[dict]:
    response = google_request("GET", "/users/me/calendarList", google_access_token(connection))
    response.raise_for_status()
    return [{"id": item.get("id"), "name": item.get("summary") or item.get("id"),
             "primary": bool(item.get("primary")), "access_role": item.get("accessRole")}
            for item in response.json().get("items", [])
            if item.get("id") and item.get("accessRole") in {"owner", "writer"}]


def booking_calendar_payload(booking: Booking) -> dict:
    if not booking.event_date:
        raise RuntimeError("The wedding date has not been set")
    event_id = f"b{token_hash(booking.id)[:40]}"
    details = booking.venue_details or {}
    location = details.get("formatted_address") or booking.venue or ""
    directions = venue_maps_url(booking.venue, details)
    description = f"Couple: {booking.title}\nVenue: {booking.venue or 'To be confirmed'}"
    if directions:
        description += f"\nDirections: {directions}"
    description += "\n\nManaged by Ivory Digital Booking Studio."
    return {"id": event_id, "summary": f"Wedding — {booking.title}",
            "description": description, "location": location,
            "start": {"date": booking.event_date.isoformat()},
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
        calendar_path = f"/calendars/{quote(connection.calendar_id or 'primary', safe='')}/events"
        path = f"{calendar_path}/{quote(event_id, safe='')}"
        if should_exist:
            response = google_request("PUT", path, access, booking_calendar_payload(booking))
            if response.status_code == 404:
                response = google_request("POST", calendar_path, access, booking_calendar_payload(booking))
            if response.status_code >= 400:
                raise RuntimeError("Google Calendar did not accept the wedding event")
            body = response.json()
            connection.last_synced_at = utcnow(); connection.last_error = ""
            state = {"status": "synced", "desired_action": None, "event_id": event_id,
                     "calendar_id": connection.calendar_id, "html_link": body.get("htmlLink"),
                     "last_synced_at": connection.last_synced_at.isoformat(), "last_error": None}
        else:
            response = google_request("DELETE", path, access)
            if response.status_code not in {204, 404, 410}:
                raise RuntimeError("Google Calendar did not remove the wedding event")
            connection.last_synced_at = utcnow(); connection.last_error = ""
            state = {"status": "removed", "desired_action": None,
                     "removed_event_id": event_id, "last_synced_at": connection.last_synced_at.isoformat(),
                     "last_error": None}
    except Exception as exc:
        if connection:
            connection.last_error = str(exc)[:500]
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
        calendar_path = f"/calendars/{quote(connection.calendar_id or 'primary', safe='')}/events"
        path = f"{calendar_path}/{quote(event_id, safe='')}"
        if should_exist:
            payload = {"id": event_id, "summary": f"Unavailable — {block.label}",
                       "description": "\n".join(filter(None, [block.notes, "Managed by Ivory Digital Booking Studio."])),
                       "start": {"date": block.start_date.isoformat()},
                       "end": {"date": (block.end_date + timedelta(days=1)).isoformat()},
                       "transparency": "opaque", "extendedProperties": {"private": {"ivory_date_block_id": block.id}}}
            response = google_request("PUT", path, access, payload)
            if response.status_code == 404:
                response = google_request("POST", calendar_path, access, payload)
            if response.status_code >= 400:
                raise RuntimeError("Google Calendar did not accept the blocked dates")
            connection.last_synced_at = utcnow(); connection.last_error = ""
            state = {"status": "synced", "desired_action": None, "event_id": event_id,
                     "calendar_id": connection.calendar_id, "html_link": response.json().get("htmlLink"),
                     "last_synced_at": connection.last_synced_at.isoformat(), "last_error": None}
        else:
            response = google_request("DELETE", path, access)
            if response.status_code not in {204, 404, 410}:
                raise RuntimeError("Google Calendar did not remove the blocked dates")
            connection.last_synced_at = utcnow(); connection.last_error = ""
            state = {"status": "removed", "desired_action": None,
                     "removed_event_id": event_id, "last_synced_at": connection.last_synced_at.isoformat(), "last_error": None}
    except Exception as exc:
        if connection:
            connection.last_error = str(exc)[:500]
        state = {**current, "status": "error", "desired_action": desired,
                 "event_id": event_id, "last_attempt_at": utcnow().isoformat(),
                 "last_error": str(exc)[:500]}
    block.calendar_state = state
    return state


def sync_tenant_calendar_records(db: Session, tenant: Tenant) -> list[dict]:
    results = []
    bookings = db.scalars(select(Booking).where(Booking.tenant_id == tenant.id)).all()
    for booking in bookings:
        results.append(sync_booking_calendar_safely(db, tenant, booking, booking_journey(db, booking)))
    blocks = db.scalars(select(TenantDateBlock).where(TenantDateBlock.tenant_id == tenant.id)).all()
    for block in blocks:
        results.append(sync_date_block_safely(db, tenant, block))
    return results


def enquiry_workspace(db: Session, tenant: Tenant, enquiry: Enquiry,
                      title: str = "") -> tuple[Booking, BookingJourney, bool]:
    """Return the private quote workspace without promoting it to Weddings."""
    existing_journey = db.scalar(select(BookingJourney).where(
        BookingJourney.tenant_id == tenant.id, BookingJourney.enquiry_id == enquiry.id))
    if existing_journey:
        return studio_booking(db, tenant.id, existing_journey.booking_id), existing_journey, False
    email = normalise_email(enquiry.email)
    client = db.scalar(select(Client).where(Client.tenant_id == tenant.id, Client.email == email))
    if not client:
        client = Client(tenant_id=tenant.id, first_name=enquiry.first_name,
                        partner_name=enquiry.partner_name or None, email=email,
                        phone=enquiry.phone or None)
        db.add(client); db.flush()
    display_title = title.strip() or " & ".join(filter(None, [enquiry.first_name, enquiry.partner_name]))
    booking = Booking(tenant_id=tenant.id, client_id=client.id, title=display_title,
                      event_date=enquiry.event_date, venue=enquiry.venue or None,
                      venue_details=enquiry.venue_details or {}, status="enquiry",
                      is_provisional=True)
    db.add(booking); db.flush()
    journey = booking_journey(db, booking); journey.enquiry_id = enquiry.id
    return booking, journey, True


@app.post("/api/studio/enquiries/{enquiry_id}/workspace")
def open_enquiry_workspace(enquiry_id: str, payload: EnquiryConvertIn, request: Request,
                           session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    enquiry = db.scalar(select(Enquiry).where(Enquiry.id == enquiry_id,
                        Enquiry.tenant_id == tenant.id))
    if not enquiry:
        raise HTTPException(404, "Enquiry not found")
    if enquiry.status == "booked":
        existing = db.scalar(select(BookingJourney).where(
            BookingJourney.tenant_id == tenant.id, BookingJourney.enquiry_id == enquiry.id))
        if not existing:
            raise HTTPException(409, "This enquiry is already booked")
        return journey_json(db, studio_booking(db, tenant.id, existing.booking_id), existing)
    booking, journey, created = enquiry_workspace(db, tenant, enquiry, payload.title)
    if created:
        audit(db, "enquiry_workspace_created", "booking", booking.id, actor=session.user,
              tenant_id=tenant.id, request=request, detail={"enquiry_id": enquiry.id})
    result = journey_json(db, booking, journey)
    db.commit()
    return result


# Backwards-compatible alias used by older studio assets. It now opens the
# provisional workspace and deliberately does not move the enquiry.
@app.post("/api/studio/enquiries/{enquiry_id}/convert", status_code=201)
def convert_enquiry(enquiry_id: str, payload: EnquiryConvertIn, request: Request,
                    session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    return open_enquiry_workspace(enquiry_id, payload, request, session, db)


@app.post("/api/studio/enquiries/{enquiry_id}/mark-booked")
def mark_enquiry_booked(enquiry_id: str, payload: EnquiryConvertIn, request: Request,
                        session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    enquiry = db.scalar(select(Enquiry).where(
        Enquiry.id == enquiry_id, Enquiry.tenant_id == tenant.id))
    if not enquiry:
        raise HTTPException(404, "Enquiry not found")
    booking, journey, _ = enquiry_workspace(db, tenant, enquiry, payload.title)
    booking.is_provisional = False
    booking.promoted_at = booking.promoted_at or utcnow()
    if booking.status in {"enquiry", "quote_preparation", "awaiting_quote_acceptance"}:
        booking.status = "confirmed"
    enquiry.status = "booked"
    cancel_open_enquiry_actions(db, tenant.id, enquiry.id, booking.id)
    sync_booking_calendar_safely(db, tenant, booking, journey)
    audit(db, "enquiry_marked_booked", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"enquiry_id": enquiry.id})
    result = journey_json(db, booking, journey)
    db.commit()
    return result


@app.get("/api/studio/bookings")
def list_bookings(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    rows = db.scalars(select(Booking).where(
                      Booking.tenant_id == tenant.id,
                      Booking.is_provisional.is_(False))
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
    required_ids = set(payload.required_add_on_ids)
    selected_ids.update(required_ids)
    offered_packages = {item.id for item in package_rows}
    selected_ids.update(item.id for item in add_on_rows
                        if item.selection_mode == "mandatory"
                        and (not item.eligible_package_ids
                             or bool(offered_packages & set(item.eligible_package_ids))))
    if any(item_id not in allowed_addons for item_id in selected_ids):
        raise HTTPException(422, "One of the selected add-ons is no longer available")
    if not required_ids.issubset(selected_ids):
        raise HTTPException(422, "A compulsory extra must also be included in the quote")
    incompatible = [allowed_addons[item_id].name for item_id in selected_ids
                    if allowed_addons[item_id].eligible_package_ids
                    and not (offered_packages & set(allowed_addons[item_id].eligible_package_ids))]
    if incompatible:
        raise HTTPException(422, f"{incompatible[0]} is not available with any offered package")
    add_on_snapshots = []
    for item in add_on_rows:
        if item.id not in selected_ids:
            continue
        snapshot = add_on_json(item)
        if item.id in required_ids or item.selection_mode == "mandatory":
            snapshot["selection_mode"] = "mandatory"
            snapshot["required_for_quote"] = True
        else:
            snapshot["selection_mode"] = "optional"
            snapshot["required_for_quote"] = False
        add_on_snapshots.append(snapshot)
    return {"status": "draft", "packages": [package_json(item) for item in package_rows],
            "add_ons": add_on_snapshots,
            "custom_items": payload.custom_items, "message": payload.message,
            "expires_on": payload.expires_on.isoformat() if payload.expires_on else None,
            "updated_at": utcnow().isoformat()}


def enquiry_for_journey(db: Session, journey: BookingJourney) -> Enquiry | None:
    if not journey.enquiry_id:
        return None
    return db.scalar(select(Enquiry).where(
        Enquiry.id == journey.enquiry_id,
        Enquiry.tenant_id == journey.tenant_id))


def cancel_open_enquiry_actions(db: Session, tenant_id: str, enquiry_id: str,
                                booking_id: str | None = None) -> int:
    """Stop only this enquiry's unsent workflow work after it is closed/booked."""
    links = [WorkflowAction.enquiry_id == enquiry_id]
    if booking_id:
        links.append(WorkflowAction.booking_id == booking_id)
    rows = db.scalars(select(WorkflowAction).where(
        WorkflowAction.tenant_id == tenant_id,
        or_(*links),
        WorkflowAction.completed_at.is_(None),
        WorkflowAction.status.notin_(["sent", "completed", "cancelled", "skipped"]))).all()
    for action in rows:
        action.status = "cancelled"
        action.completed_at = utcnow()
    return len(rows)


def quote_email_copy(db: Session, tenant: Tenant, booking: Booking,
                     journey: BookingJourney) -> dict:
    ensure_starter_email_templates(db, tenant)
    client = db.scalar(select(Client).where(
        Client.id == booking.client_id, Client.tenant_id == tenant.id))
    template = db.scalar(select(EmailTemplate).where(
        EmailTemplate.tenant_id == tenant.id,
        EmailTemplate.is_active.is_(True),
        or_(func.lower(EmailTemplate.category) == "quote",
            func.lower(EmailTemplate.name).like("%quote%"))
    ).order_by(EmailTemplate.updated_at.desc()).limit(1))
    subject = template.subject if template else "Your wedding quote from {{business_name}}"
    body = template.body if template else (
        "Hi {{couple_first_name}},\n\n"
        "Thank you for getting in touch. I have prepared your wedding quote and package choices.\n\n"
        "View your private quote here: {{client_portal_link}}\n\n"
        "If you have any questions, just reply to this email."
    )
    link = portal_url(journey)
    if "{{client_portal_link}}" not in body and link not in body:
        body = f"{body.rstrip()}\n\nView your private quote here: {{client_portal_link}}"
    extra = {"client_portal_link": link}
    return {
        "recipient": client.email if client else "",
        "subject": merge_message(subject, tenant, booking, client, extra),
        "body": merge_message(body, tenant, booking, client, extra),
        "portal_url": link,
        "template_id": template.id if template else None,
        "master_template_unchanged": True,
    }


@app.put("/api/studio/bookings/{booking_id}/quote")
def save_quote(booking_id: str, payload: QuoteDraftIn, request: Request,
               session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id); journey = booking_journey(db, booking)
    if journey.accepted_quote:
        raise HTTPException(409, "This quote has been accepted and its snapshot cannot be changed")
    journey.quote_state = quote_snapshot(db, tenant.id, payload)
    booking.status = "quote_preparation"
    enquiry = enquiry_for_journey(db, journey)
    if enquiry and booking.is_provisional:
        enquiry.status = "quote_draft"
    audit(db, "quote_draft_saved", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    result = journey_json(db, booking, journey)
    db.commit()
    return result


@app.get("/api/studio/bookings/{booking_id}/quote/email-preview")
def preview_quote_email(booking_id: str, context=Depends(studio_context),
                        db: Session = Depends(get_db)):
    _, _, tenant = context
    booking = studio_booking(db, tenant.id, booking_id); journey = booking_journey(db, booking)
    quote_state = dict(journey.quote_state or {})
    if not quote_state.get("packages"):
        raise HTTPException(422, "Add at least one package before reviewing the quote email")
    return quote_email_copy(db, tenant, booking, journey)


@app.post("/api/studio/bookings/{booking_id}/quote/send")
def send_quote(booking_id: str, request: Request, payload: QuoteEmailSendIn | None = None,
               session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id); journey = booking_journey(db, booking)
    quote = dict(journey.quote_state or {})
    if not quote.get("packages"):
        raise HTTPException(422, "Add at least one package before preparing the quote link")
    if journey.accepted_quote:
        raise HTTPException(409, "This quote has already been accepted")
    mailbox = db.get(MailboxSetting, tenant.id)
    if not mailbox or not mailbox.smtp_verified_at or not mailbox.smtp_password_encrypted:
        raise HTTPException(409, "Verify the Studio outgoing email connection before sending this quote")
    client = db.scalar(select(Client).where(
        Client.id == booking.client_id, Client.tenant_id == tenant.id))
    if not client:
        raise HTTPException(404, "The enquiry contact could not be found")
    copy = quote_email_copy(db, tenant, booking, journey)
    subject = payload.subject.strip() if payload else copy["subject"]
    body = payload.body.strip() if payload else copy["body"]
    link = copy["portal_url"]
    if link not in body and "{{client_portal_link}}" not in body:
        body = f"{body.rstrip()}\n\nView your private quote here: {link}"
    try:
        message = send_tenant_email(
            db, tenant, mailbox, client.email, subject, body,
            booking=booking, client=client, template_id=copy["template_id"],
            extra={"client_portal_link": link, "action_label": "Open your wedding quote"},
        )
    except Exception as exc:
        audit(db, "quote_email_failed", "booking", booking.id, actor=session.user,
              tenant_id=tenant.id, request=request,
              detail={"error_type": type(exc).__name__})
        db.commit()
        raise HTTPException(422, "The quote email could not be sent. Check Email connection and try again") from exc
    quote.update({"status": "sent", "sent_at": utcnow().isoformat(),
                  "email_message_id": message.id})
    journey.quote_state = quote; booking.status = "awaiting_quote_acceptance"
    enquiry = enquiry_for_journey(db, journey)
    if enquiry and booking.is_provisional:
        enquiry.status = "quote_sent"
    trigger_workflow(db, tenant, booking, "quote_sent")
    audit(db, "quote_email_sent", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"recipient": client.email, "email_message_id": message.id,
                  "automatic_sending_paused": tenant.automations_paused})
    db.commit()
    return {"ok": True, "portal_url": portal_url(journey),
            "delivery": "email", "automatic_email_sent": True,
            "email_message_id": message.id}


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
def get_public_portal(raw_token: str, request: Request, db: Session = Depends(get_db)):
    tenant, booking, journey = public_portal(raw_token, db)
    quote_state = dict(journey.quote_state or {})
    expired = False
    if quote_state.get("status") == "sent" and quote_state.get("expires_on"):
        try:
            expired = date.fromisoformat(quote_state["expires_on"]) < date.today()
        except (TypeError, ValueError):
            expired = False
    if expired:
        quote_state["status"] = "expired"
        journey.quote_state = quote_state
        enquiry = enquiry_for_journey(db, journey)
        if enquiry and booking.is_provisional:
            enquiry.status = "quote_expired"
        audit(db, "quote_expired", "booking", booking.id, tenant_id=tenant.id, request=request)
        db.commit()
    elif quote_state.get("status") == "sent" and not quote_state.get("viewed_at"):
        quote_state["viewed_at"] = utcnow().isoformat()
        journey.quote_state = quote_state
        enquiry = enquiry_for_journey(db, journey)
        if enquiry and booking.is_provisional:
            enquiry.status = "quote_viewed"
        audit(db, "quote_viewed", "booking", booking.id, tenant_id=tenant.id, request=request)
        db.commit()
    data = journey_json(db, booking, journey, include_portal_url=False)
    data["business"] = {"display_name": (tenant.branding or {}).get("display_name") or tenant.display_name,
                        "accent_colour": (tenant.branding or {}).get("accent_colour") or "#a9782e",
                        "welcome_message": (tenant.branding or {}).get("welcome_message") or "Welcome to your private booking area."}
    templates = [] if booking.is_provisional else db.scalars(select(QuestionnaireTemplate).where(
        QuestionnaireTemplate.tenant_id == tenant.id,
        QuestionnaireTemplate.is_active.is_(True)).order_by(QuestionnaireTemplate.form_type)).all()
    submissions = [] if booking.is_provisional else db.scalars(select(QuestionnaireSubmission).where(
        QuestionnaireSubmission.tenant_id == tenant.id,
        QuestionnaireSubmission.booking_id == booking.id)).all()
    submissions_by_type = {item.form_type: item for item in submissions}
    data["available_questionnaires"] = [
        {**questionnaire_json(row, submissions_by_type.get(row.form_type)),
         "submitted": row.form_type in submissions_by_type}
        for row in templates
    ]
    return data


@app.post("/api/public/portal/{raw_token}/quote/accept")
def accept_public_quote(raw_token: str, payload: QuoteAcceptIn, request: Request,
                        db: Session = Depends(get_db)):
    tenant, booking, journey = public_portal(raw_token, db)
    # Serialise acceptance on PostgreSQL so two near-simultaneous clicks cannot
    # create two invoices for the same quote. SQLite safely ignores the hint.
    journey = db.scalar(select(BookingJourney).where(
        BookingJourney.booking_id == journey.booking_id,
        BookingJourney.tenant_id == tenant.id).with_for_update())
    if not journey:
        raise HTTPException(404, "This private client area is not available")
    if journey.accepted_quote:
        raise HTTPException(409, "This quote has already been accepted")
    quote = dict(journey.quote_state or {})
    if quote.get("status") != "sent":
        raise HTTPException(409, "This quote is not ready to accept")
    if quote.get("expires_on") and date.fromisoformat(quote["expires_on"]) < date.today():
        quote["status"] = "expired"; journey.quote_state = quote
        enquiry = enquiry_for_journey(db, journey)
        if enquiry and booking.is_provisional:
            enquiry.status = "quote_expired"
        db.commit()
        raise HTTPException(409, "This quote has expired. Please ask the studio for an updated quote")
    package = next((item for item in quote.get("packages", []) if item["id"] == payload.package_id), None)
    if not package:
        raise HTTPException(422, "Choose one of the available packages")
    offered_addons = {item["id"]: item for item in quote.get("add_ons", [])}
    selected_ids = set(payload.add_on_ids)
    selected_ids.update(item["id"] for item in offered_addons.values()
                        if item["selection_mode"] == "mandatory"
                        and (not item.get("eligible_package_ids")
                             or package["id"] in item.get("eligible_package_ids", [])))
    if any(item_id not in offered_addons for item_id in selected_ids):
        raise HTTPException(422, "Choose only the available extras")
    unavailable = [offered_addons[item_id]["name"] for item_id in selected_ids
                   if offered_addons[item_id].get("eligible_package_ids")
                   and package["id"] not in offered_addons[item_id]["eligible_package_ids"]]
    if unavailable:
        raise HTTPException(422, f"{unavailable[0]} is not available with {package['name']}")
    selected_addons = [item for item in quote.get("add_ons", []) if item["id"] in selected_ids]
    line_items = [{"kind": "package", "label": package["name"], "price_pence": package["price_pence"]}]
    line_items += [{"kind": "add_on", "label": item["name"], "price_pence": item["price_pence"]} for item in selected_addons]
    line_items += [{"kind": "custom", **item} for item in quote.get("custom_items", [])]
    total = sum(int(item["price_pence"]) for item in line_items)
    if total < 0:
        raise HTTPException(422, "Discounts cannot reduce the accepted quote below £0")
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
    booking.is_provisional = False
    booking.promoted_at = booking.promoted_at or utcnow()
    contract = issue_active_contract_snapshot(db, tenant, booking)
    enquiry = enquiry_for_journey(db, journey)
    if enquiry:
        enquiry.status = "booked"
        cancel_open_enquiry_actions(db, tenant.id, enquiry.id, booking.id)
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
          request=request, detail={"invoice_number": number, "total_pence": total,
                                   "contract_issued": bool(contract)})
    if contract:
        audit(db, "contract_issued_automatically", "contract", contract.id,
              tenant_id=tenant.id, request=request,
              detail={"template_id": contract.template_id, "version": contract.version})
    else:
        create_studio_notification(
            db, tenant, booking, "contract_template_missing",
            f"Add an agreement for {booking.title}",
            "Their quote is accepted, but there is no active agreement template. Save one in Contracts & forms, then issue it from this wedding.",
        )
    create_studio_notification(
        db, tenant, booking, "quote_accepted",
        f"{booking.title} accepted their quote",
        (f"Their package has been accepted, invoice {number} was created and their agreement is ready to sign."
         if contract else
         f"Their package has been accepted and invoice {number} was created. Add an active agreement template before asking them to sign."),
    )
    result = invoice_json(invoice, db)
    db.commit()
    return {"ok": True, "invoice": result, "contract_ready": bool(contract),
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
    signed_at = utcnow()
    row.client_name = payload.full_name.strip(); row.client_signed_at = signed_at
    row.client_ip = client_ip(request) or ""
    row.supplier_name = automatic_supplier_name(db, tenant)
    row.supplier_signed_at = signed_at
    trigger_workflow(db, tenant, booking, "contract_signed")
    trigger_workflow(db, tenant, booking, "agreement_signed")
    audit(db, "contract_client_signed", "contract", row.id, tenant_id=tenant.id,
          request=request, detail={"signatory": row.client_name})
    audit(db, "contract_supplier_signed_automatically", "contract", row.id,
          tenant_id=tenant.id, request=request, detail={"signatory": row.supplier_name})
    ensure_starter_email_templates(db, tenant)
    client = db.scalar(select(Client).where(
        Client.id == booking.client_id, Client.tenant_id == tenant.id))
    mailbox = db.get(MailboxSetting, tenant.id)
    delivery = "not_configured"
    if client and mailbox and mailbox.smtp_verified_at and mailbox.smtp_password_encrypted:
        template = db.scalar(select(EmailTemplate).where(
            EmailTemplate.tenant_id == tenant.id,
            EmailTemplate.is_active.is_(True),
            or_(func.lower(EmailTemplate.category) == "contract",
                func.lower(EmailTemplate.name).like("%contract%signed%"))
        ).order_by(EmailTemplate.updated_at.desc()).limit(1))
        subject = template.subject if template else "Your completed wedding agreement from {{business_name}}"
        body = template.body if template else (
            "Hi {{couple_first_name}},\n\nYour wedding agreement has now been signed by both parties. "
            "A completed PDF copy is attached for your records.\n\n{{client_portal_link}}"
        )
        filename = f"{safe_document_name(booking.title)}-signed-agreement.pdf"
        completed_pdf = contract_pdf(tenant, booking, row).body
        try:
            message = send_tenant_email(
                db, tenant, mailbox, client.email, subject, body,
                booking=booking, client=client,
                template_id=template.id if template else None,
                extra={"client_portal_link": portal_url(journey),
                       "action_label": "Open your signed agreement"},
                attachments=[(filename, completed_pdf, "application/pdf")],
            )
            delivery = "sent"
            audit(db, "completed_contract_emailed", "contract", row.id,
                  tenant_id=tenant.id, request=request,
                  detail={"recipient": client.email, "email_message_id": message.id})
        except Exception as exc:
            delivery = "failed"
            audit(db, "completed_contract_email_failed", "contract", row.id,
                  tenant_id=tenant.id, request=request,
                  detail={"recipient": client.email, "error": str(exc)[:500]})
    create_studio_notification(
        db, tenant, booking, "contract_signed",
        f"{booking.title}'s agreement is complete",
        ("The couple signed, Studio countersigned automatically and their completed PDF was emailed."
         if delivery == "sent" else
         "The couple signed and Studio countersigned automatically. Their PDF email could not be sent, so download it from the wedding and send it manually."),
    )
    db.commit()
    return {**contract_json(row), "confirmation_email": delivery}


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
    ensure_questionnaire_templates(db, tenant)
    rows = db.scalars(select(QuestionnaireTemplate).where(
        QuestionnaireTemplate.tenant_id == tenant.id).order_by(QuestionnaireTemplate.form_type)).all()
    db.commit()
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


@app.post("/api/studio/questionnaire-templates/{form_type}/starter")
def restore_questionnaire_starter(form_type: str, request: Request,
                                  session: UserSession = Depends(require_csrf),
                                  db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    if form_type not in DEFAULT_QUESTIONNAIRES:
        raise HTTPException(404, "Form type not found")
    starter = default_questionnaire(form_type)
    row = db.scalar(select(QuestionnaireTemplate).where(
        QuestionnaireTemplate.tenant_id == tenant.id,
        QuestionnaireTemplate.form_type == form_type))
    if not row:
        row = QuestionnaireTemplate(tenant_id=tenant.id, **starter)
        db.add(row)
    else:
        row.name = starter["name"]
        row.introduction = starter["introduction"]
        row.questions = starter["questions"]
        row.is_active = True
    db.flush()
    audit(db, "questionnaire_starter_restored", "questionnaire_template", row.id,
          actor=session.user, tenant_id=membership.tenant_id, request=request,
          detail={"form_type": form_type, "questions": len(row.questions or [])})
    db.commit()
    return questionnaire_json(row)


@app.post("/api/public/portal/{raw_token}/questionnaires/{form_type}")
def submit_questionnaire(raw_token: str, form_type: str,
                         payload: QuestionnaireSubmitIn, request: Request,
                         db: Session = Depends(get_db)):
    tenant, booking, journey = public_portal(raw_token, db)
    if booking.is_provisional:
        raise HTTPException(409, "Booking forms become available after the quote is accepted")
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
    form_name = "booking questionnaire" if form_type == "booking" else "final wedding timings"
    create_studio_notification(
        db, tenant, booking, "form_submitted",
        f"{booking.title} updated their {form_name}",
        "Their latest answers are ready to review and download in the wedding workspace.",
    )
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
    journeys = db.scalars(select(BookingJourney).where(BookingJourney.tenant_id == tenant.id)).all()
    states = [row.calendar_state or {} for row in journeys] + [row.calendar_state or {} for row in blocks]
    return {"platform_configured": google_configured(),
            "connected": bool(connection and connection.refresh_token_encrypted),
            "google_account_email": connection.google_account_email if connection else "",
            "calendar_id": connection.calendar_id if connection else "primary",
            "calendar_name": connection.calendar_name if connection else "Primary calendar",
            "last_synced_at": connection.last_synced_at.isoformat() if connection and connection.last_synced_at else None,
            "last_error": connection.last_error if connection else "",
            "sync_summary": {"synced": sum(item.get("status") == "synced" for item in states),
                             "pending": sum(item.get("status") == "pending" for item in states),
                             "errors": sum(item.get("status") == "error" for item in states)},
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
        choices = google_calendar_choices(connection)
    except Exception as exc:
        connection.last_error = str(exc)[:500]; db.commit()
        raise HTTPException(422, "Google Calendar could not be reached. Reconnect and try again") from exc
    return choices


@app.put("/api/studio/calendar/settings")
def save_calendar_settings(payload: CalendarSettingsIn, request: Request,
                           session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    connection = db.get(TenantCalendarConnection, tenant.id)
    if not connection or not connection.refresh_token_encrypted:
        raise HTTPException(409, "Connect Google Calendar first")
    try:
        choices = google_calendar_choices(connection)
    except Exception as exc:
        connection.last_error = str(exc)[:500]; db.commit()
        raise HTTPException(422, "Google Calendar could not be reached. Reconnect and try again") from exc
    selected = next((item for item in choices if item["id"] == payload.calendar_id), None)
    if not selected:
        raise HTTPException(422, "Choose a calendar you can add events to")
    previous_calendar = connection.calendar_id
    cleanup_failures = 0
    if previous_calendar and previous_calendar != selected["id"]:
        access = google_access_token(connection)
        journeys = db.scalars(select(BookingJourney).where(BookingJourney.tenant_id == tenant.id)).all()
        blocks = db.scalars(select(TenantDateBlock).where(TenantDateBlock.tenant_id == tenant.id)).all()
        for owner in [*journeys, *blocks]:
            state = dict(owner.calendar_state or {})
            event_id = state.get("event_id")
            if not event_id or state.get("status") not in {"synced", "error", "pending"}:
                continue
            path = f"/calendars/{quote(previous_calendar, safe='')}/events/{quote(event_id, safe='')}"
            response = google_request("DELETE", path, access)
            if response.status_code not in {204, 404, 410}:
                cleanup_failures += 1
            owner.calendar_state = {**state, "status": "pending", "desired_action": "create_or_update",
                                    "html_link": None, "last_error": None}
    connection.calendar_id = selected["id"]; connection.calendar_name = selected["name"]
    results = sync_tenant_calendar_records(db, tenant)
    mark_onboarding(tenant, "calendar")
    audit(db, "google_calendar_selected", "tenant", tenant.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"calendar_id": selected["id"], "calendar_name": selected["name"],
                  "previous_calendar_id": previous_calendar, "records": len(results),
                  "old_event_cleanup_failures": cleanup_failures})
    db.commit()
    return {"ok": True, "calendar_id": selected["id"], "calendar_name": selected["name"],
            "results": results, "old_event_cleanup_failures": cleanup_failures}


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
    results = sync_tenant_calendar_records(db, tenant)
    audit(db, "google_calendar_sync_requested", "tenant", tenant.id,
          actor=session.user, tenant_id=tenant.id, request=request,
          detail={"records": len(results)})
    db.commit()
    return {"ok": True, "results": results}


@app.post("/api/studio/bookings/{booking_id}/calendar/sync")
def sync_one_booking_calendar(booking_id: str, request: Request,
                              session: UserSession = Depends(require_csrf),
                              db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id)
    result = sync_booking_calendar_safely(db, tenant, booking, booking_journey(db, booking))
    audit(db, "booking_calendar_sync_requested", "booking", booking.id,
          actor=session.user, tenant_id=tenant.id, request=request,
          detail={"status": result.get("status")})
    db.commit()
    return result


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


@app.put("/api/studio/workflow-actions/{action_id}/review")
def update_workflow_action_review(action_id: str, payload: WorkflowActionReviewIn,
                                  request: Request, session: UserSession = Depends(require_csrf),
                                  db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(WorkflowAction).where(
        WorkflowAction.id == action_id, WorkflowAction.tenant_id == tenant.id))
    if not row: raise HTTPException(404, "Workflow item not found")
    if row.status not in {"review", "paused", "error"}:
        raise HTTPException(409, "Only an unsent review item can be edited")
    details = dict(row.payload or {})
    details["subject"] = payload.subject; details["message_body"] = payload.message_body
    details["review_edited_at"] = utcnow().isoformat(); row.payload = details
    audit(db, "workflow_action_review_edited", "workflow_action", row.id,
          actor=session.user, tenant_id=tenant.id, request=request)
    db.commit(); return {"ok": True, "payload": row.payload}


@app.post("/api/studio/workflow-actions/{action_id}/skip")
def skip_workflow_action(action_id: str, request: Request,
                         session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(WorkflowAction).where(
        WorkflowAction.id == action_id, WorkflowAction.tenant_id == tenant.id))
    if not row: raise HTTPException(404, "Workflow item not found")
    if row.status in {"sent", "completed", "cancelled", "skipped"}:
        raise HTTPException(409, "This workflow item is already closed")
    row.status = "skipped"; row.completed_at = utcnow()
    audit(db, "workflow_action_skipped", "workflow_action", row.id,
          actor=session.user, tenant_id=tenant.id, request=request)
    db.commit(); return {"ok": True, "status": row.status}


@app.post("/api/studio/workflow-actions/{action_id}/retry")
def retry_workflow_action(action_id: str, request: Request,
                          session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(WorkflowAction).where(
        WorkflowAction.id == action_id, WorkflowAction.tenant_id == tenant.id))
    if not row: raise HTTPException(404, "Workflow item not found")
    if row.status != "error": raise HTTPException(409, "Only a failed workflow item can be retried")
    details = dict(row.payload or {}); details.pop("last_error", None); details["retried_at"] = utcnow().isoformat()
    row.payload = details; row.status = "paused" if tenant.automations_paused else (
        "review" if row.mode == "review" else "pending" if row.mode == "task" else "queued")
    audit(db, "workflow_action_retried", "workflow_action", row.id,
          actor=session.user, tenant_id=tenant.id, request=request)
    db.commit(); return {"ok": True, "status": row.status}


@app.post("/api/studio/workflows/{workflow_id}/duplicate", status_code=201)
def duplicate_workflow(workflow_id: str, request: Request,
                       session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    source = db.scalar(select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant.id))
    if not source: raise HTTPException(404, "Workflow not found")
    copy = Workflow(tenant_id=tenant.id, name=f"{source.name} copy"[:160],
                    description=source.description, is_active=False,
                    sort_order=source.sort_order + 1)
    db.add(copy); db.flush()
    steps = db.scalars(select(WorkflowStep).where(
        WorkflowStep.tenant_id == tenant.id, WorkflowStep.workflow_id == source.id
    ).order_by(WorkflowStep.sort_order)).all()
    for step in steps:
        clone = WorkflowStep(tenant_id=tenant.id, workflow_id=copy.id, name=step.name,
                             trigger_event=step.trigger_event, timing_direction=step.timing_direction,
                             offset_value=step.offset_value, offset_unit=step.offset_unit,
                             action_type=step.action_type, subject=step.subject,
                             message_body=step.message_body, task_title=step.task_title,
                             is_paused=True, sort_order=step.sort_order)
        db.add(clone); db.flush()
        db.add(WorkflowStepControl(step_id=clone.id, tenant_id=tenant.id, mode="off",
                                   apply_to_existing=False))
    audit(db, "workflow_duplicated", "workflow", copy.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"source_workflow_id": source.id})
    db.commit(); return workflow_json(copy, db)


@app.post("/api/studio/workflows/{workflow_id}/activate")
def activate_workflow(workflow_id: str, request: Request,
                      session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant.id))
    if not row: raise HTTPException(404, "Workflow not found")
    row.is_active = True
    audit(db, "workflow_activated", "workflow", row.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"existing_couples_changed": False})
    db.commit(); return workflow_json(row, db)


@app.post("/api/studio/workflows/{workflow_id}/pause")
def pause_workflow(workflow_id: str, request: Request,
                   session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant.id))
    if not row: raise HTTPException(404, "Workflow not found")
    row.is_active = False
    step_ids = list(db.scalars(select(WorkflowStep.id).where(
        WorkflowStep.tenant_id == tenant.id, WorkflowStep.workflow_id == row.id)).all())
    if step_ids:
        actions = db.scalars(select(WorkflowAction).where(
            WorkflowAction.tenant_id == tenant.id, WorkflowAction.step_id.in_(step_ids),
            WorkflowAction.completed_at.is_(None))).all()
        for action in actions:
            details = dict(action.payload or {}); details["resume_status"] = action.status
            action.payload = details; action.status = "paused"
    audit(db, "workflow_paused", "workflow", row.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit(); return workflow_json(row, db)


@app.delete("/api/studio/workflows/{workflow_id}")
def delete_workflow(workflow_id: str, request: Request,
                    session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant.id))
    if not row: raise HTTPException(404, "Workflow not found")
    if row.is_active: raise HTTPException(409, "Pause the workflow before deleting it")
    step_ids = list(db.scalars(select(WorkflowStep.id).where(
        WorkflowStep.tenant_id == tenant.id, WorkflowStep.workflow_id == row.id)).all())
    used = None
    if step_ids:
        used = db.scalar(select(WorkflowAction.id).where(
            WorkflowAction.tenant_id == tenant.id,
            WorkflowAction.step_id.in_(step_ids)).limit(1))
    if used:
        raise HTTPException(409, "This workflow has client history. Keep it paused so that history remains available")
    db.delete(row); audit(db, "workflow_deleted", "workflow", row.id, actor=session.user,
                          tenant_id=tenant.id, request=request)
    db.commit(); return {"ok": True}


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


@app.get("/api/studio/today")
def studio_today(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    today = date.today(); soon = today + timedelta(days=30)
    enquiries = db.scalars(select(Enquiry).where(
        Enquiry.tenant_id == tenant.id, Enquiry.status == "new"
    ).order_by(Enquiry.created_at)).all()
    actions = db.scalars(select(WorkflowAction).where(
        WorkflowAction.tenant_id == tenant.id,
        WorkflowAction.status.in_(["review", "error", "pending"])
    ).order_by(WorkflowAction.due_at).limit(50)).all()
    tasks = db.scalars(select(StudioTask).where(
        StudioTask.tenant_id == tenant.id, StudioTask.status == "open",
        (StudioTask.due_date.is_(None)) | (StudioTask.due_date <= soon)
    ).order_by(StudioTask.due_date).limit(50)).all()
    invoices = db.scalars(select(BookingInvoice).where(
        BookingInvoice.tenant_id == tenant.id,
        BookingInvoice.status.in_(["unpaid", "part_paid"]),
        BookingInvoice.due_date.is_not(None), BookingInvoice.due_date <= soon
    ).order_by(BookingInvoice.due_date).limit(50)).all()
    upcoming = db.scalars(select(Booking).where(
        Booking.tenant_id == tenant.id, Booking.event_date.is_not(None),
        Booking.is_provisional.is_(False),
        Booking.event_date >= today, Booking.event_date <= today + timedelta(days=60),
        Booking.status.notin_(["cancelled", "archived"])
    ).order_by(Booking.event_date).limit(20)).all()
    notifications = db.scalars(select(StudioNotification).where(
        StudioNotification.tenant_id == tenant.id,
        StudioNotification.is_read.is_(False)
    ).order_by(StudioNotification.created_at.desc()).limit(30)).all()
    booking_names = {row.id: row.title for row in db.scalars(select(Booking).where(
        Booking.tenant_id == tenant.id)).all()}
    invoice_booking = {row.id: booking_names.get(row.booking_id, "Wedding") for row in invoices}
    return {
        "counts": {"new_enquiries": len(enquiries),
                   "review": sum(1 for row in actions if row.status == "review"),
                   "failed": sum(1 for row in actions if row.status == "error"),
                   "tasks": len(tasks), "payments": len(invoices),
                   "updates": len(notifications)},
        "enquiries": [{"id": row.id, "names": " & ".join(filter(None, [row.first_name, row.partner_name])),
                       "event_date": row.event_date.isoformat() if row.event_date else None,
                       "venue": row.venue, "created_at": row.created_at.isoformat()} for row in enquiries],
        "actions": [{"id": row.id, "booking_id": row.booking_id,
                     "booking_name": booking_names.get(row.booking_id, "Enquiry"),
                     "status": row.status, "mode": row.mode,
                     "due_at": row.due_at.isoformat(), "payload": row.payload or {}} for row in actions],
        "tasks": [task_json(row) | {"booking_name": booking_names.get(row.booking_id, "General task")} for row in tasks],
        "payments": [{"id": row.id, "booking_id": row.booking_id,
                      "booking_name": invoice_booking[row.id], "number": row.number,
                      "due_date": row.due_date.isoformat(),
                      "outstanding_pence": max(0, row.total_pence - row.paid_pence),
                      "overdue": row.due_date < today} for row in invoices],
        "upcoming": [{"id": row.id, "title": row.title,
                      "event_date": row.event_date.isoformat(), "venue": row.venue,
                      "status": row.status} for row in upcoming],
        "notifications": [{"id": row.id, "booking_id": row.booking_id,
                           "kind": row.kind, "title": row.title, "body": row.body,
                           "created_at": row.created_at.isoformat()} for row in notifications],
    }


@app.post("/api/studio/notifications/{notification_id}/read")
def mark_notification_read(notification_id: str, request: Request,
                           session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(StudioNotification).where(
        StudioNotification.id == notification_id, StudioNotification.tenant_id == tenant.id))
    if not row:
        raise HTTPException(404, "Update not found")
    row.is_read = True
    db.commit()
    return {"ok": True}


@app.get("/api/studio/search")
def studio_search(q: str = "", context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    term = q.strip()
    if len(term) < 2:
        return {"results": []}
    like = f"%{term}%"
    searched_date = None
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            searched_date = datetime.strptime(term, pattern).date()
            break
        except ValueError:
            continue
    results: list[dict] = []
    booking_filters = [
        Booking.title.ilike(like), Booking.venue.ilike(like),
        Client.email.ilike(like), Client.phone.ilike(like),
    ]
    if searched_date:
        booking_filters.append(Booking.event_date == searched_date)
    bookings = db.execute(select(Booking, Client).join(Client, Client.id == Booking.client_id).where(
        Booking.tenant_id == tenant.id, Booking.is_provisional.is_(False), or_(*booking_filters)
    ).limit(12)).all()
    for booking, client in bookings:
        results.append({"type": "booking", "id": booking.id, "title": booking.title,
                        "subtitle": " · ".join(filter(None, [booking.event_date.isoformat() if booking.event_date else "Date TBC", booking.venue, client.email]))})
    enquiry_filters = [
        Enquiry.first_name.ilike(like), Enquiry.partner_name.ilike(like),
        Enquiry.email.ilike(like), Enquiry.venue.ilike(like),
    ]
    if searched_date:
        enquiry_filters.append(Enquiry.event_date == searched_date)
    enquiries = db.scalars(select(Enquiry).where(
        Enquiry.tenant_id == tenant.id, or_(*enquiry_filters)
    ).limit(8)).all()
    for row in enquiries:
        results.append({"type": "enquiry", "id": row.id,
                        "title": " & ".join(filter(None, [row.first_name, row.partner_name])),
                        "subtitle": " · ".join(filter(None, [row.event_date.isoformat() if row.event_date else "Date TBC", row.venue, row.email]))})
    booking_names = {row.id: row.title for row in db.scalars(select(Booking).where(
        Booking.tenant_id == tenant.id)).all()}
    invoices = db.scalars(select(BookingInvoice).where(
        BookingInvoice.tenant_id == tenant.id, BookingInvoice.number.ilike(like)).limit(8)).all()
    for row in invoices:
        results.append({"type": "booking", "id": row.booking_id,
                        "title": row.number, "subtitle": booking_names.get(row.booking_id, "Invoice")})
    return {"results": results[:24]}


@app.patch("/api/studio/bookings/{booking_id}")
def update_booking(booking_id: str, payload: BookingUpdateIn, request: Request,
                   session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id)
    client = db.scalar(select(Client).where(Client.id == booking.client_id, Client.tenant_id == tenant.id))
    old_date = booking.event_date; old_venue = booking.venue
    booking.title = payload.title.strip(); booking.event_date = payload.event_date; booking.venue = payload.venue.strip()
    if payload.venue_details is not None:
        booking.venue_details = venue_details_dict(payload.venue_details)
    elif old_venue != booking.venue:
        booking.venue_details = {}
    client.first_name = payload.first_name.strip(); client.last_name = payload.last_name.strip()
    client.partner_name = payload.partner_name.strip() or None
    client.email = normalise_email(str(payload.email)); client.phone = payload.phone.strip() or None
    if old_date != booking.event_date or old_venue != booking.venue:
        journey = booking_journey(db, booking)
        journey.calendar_state = {**(journey.calendar_state or {}), "status": "pending", "last_error": "Wedding details changed — sync required."}
        sync_booking_calendar_safely(db, tenant, booking, journey)
    audit(db, "booking_details_updated", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"old_date": old_date.isoformat() if old_date else None,
                  "new_date": booking.event_date.isoformat() if booking.event_date else None,
                  "old_venue": old_venue, "new_venue": booking.venue})
    result = journey_json(db, booking, booking_journey(db, booking))
    db.commit()
    return result


@app.post("/api/studio/bookings/{booking_id}/reschedule")
def reschedule_booking(booking_id: str, payload: BookingRescheduleIn, request: Request,
                       session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id); journey = booking_journey(db, booking)
    if not booking.event_date:
        raise HTTPException(409, "Set the existing wedding date before using the move-date tool")
    clash = db.scalar(select(Booking.id).where(
        Booking.tenant_id == tenant.id, Booking.id != booking.id,
        Booking.event_date == payload.event_date,
        Booking.status.notin_(["cancelled", "archived"])).limit(1))
    blocked = db.scalar(select(TenantDateBlock.id).where(
        TenantDateBlock.tenant_id == tenant.id, TenantDateBlock.archived_at.is_(None),
        TenantDateBlock.start_date <= payload.event_date,
        TenantDateBlock.end_date >= payload.event_date).limit(1))
    if clash or blocked:
        raise HTTPException(409, "That date is already booked or blocked")
    old_date = booking.event_date; delta = payload.event_date - old_date
    booking.event_date = payload.event_date
    if payload.move_financial_dates:
        if journey.balance_due_date:
            journey.balance_due_date += delta
        invoices = db.scalars(select(BookingInvoice).where(
            BookingInvoice.tenant_id == tenant.id, BookingInvoice.booking_id == booking.id)).all()
        for invoice in invoices:
            if invoice.due_date:
                invoice.due_date += delta
            if invoice.booking_fee_due_date:
                invoice.booking_fee_due_date += delta
    actions = db.scalars(select(WorkflowAction).where(
        WorkflowAction.tenant_id == tenant.id, WorkflowAction.booking_id == booking.id,
        WorkflowAction.trigger_key == "wedding_date", WorkflowAction.completed_at.is_(None))).all()
    for action in actions:
        action.due_at += timedelta(days=delta.days)
    sync_booking_calendar_safely(db, tenant, booking, journey)
    audit(db, "booking_rescheduled", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"old_date": old_date.isoformat(), "new_date": payload.event_date.isoformat(),
                  "reason": payload.reason, "financial_dates_moved": payload.move_financial_dates})
    result = journey_json(db, booking, journey)
    db.commit()
    return result


@app.post("/api/studio/bookings/{booking_id}/archive")
def archive_booking(booking_id: str, request: Request,
                    session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id); journey = booking_journey(db, booking)
    quote_state = dict(journey.quote_state or {})
    quote_state["archived_from_status"] = booking.status
    journey.quote_state = quote_state; booking.status = "archived"
    audit(db, "booking_archived", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit(); return {"ok": True}


@app.post("/api/studio/bookings/{booking_id}/restore")
def restore_booking(booking_id: str, request: Request,
                    session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    booking = studio_booking(db, tenant.id, booking_id); journey = booking_journey(db, booking)
    if booking.status != "archived":
        raise HTTPException(409, "This wedding is not archived")
    booking.status = (journey.quote_state or {}).get("archived_from_status") or "confirmed"
    audit(db, "booking_restored", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit(); return {"ok": True, "status": booking.status}


@app.post("/api/studio/enquiries/{enquiry_id}/close")
def close_enquiry(enquiry_id: str, payload: EnquiryCloseIn, request: Request,
                  session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(Enquiry).where(Enquiry.id == enquiry_id, Enquiry.tenant_id == tenant.id))
    if not row:
        raise HTTPException(404, "Enquiry not found")
    if row.status == "booked":
        raise HTTPException(409, "This enquiry is already a wedding journey")
    row.status = "closed"
    journey = db.scalar(select(BookingJourney).where(
        BookingJourney.tenant_id == tenant.id,
        BookingJourney.enquiry_id == row.id))
    cancel_open_enquiry_actions(db, tenant.id, row.id,
                                journey.booking_id if journey else None)
    audit(db, "enquiry_closed", "enquiry", row.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"outcome": payload.outcome, "note": payload.note})
    db.commit(); return {"ok": True, "status": row.status}


@app.post("/api/studio/enquiries/{enquiry_id}/reopen")
def reopen_enquiry(enquiry_id: str, request: Request,
                   session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(Enquiry).where(Enquiry.id == enquiry_id, Enquiry.tenant_id == tenant.id))
    if not row:
        raise HTTPException(404, "Enquiry not found")
    if row.status != "closed":
        raise HTTPException(409, "Only a closed enquiry can be reopened")
    journey = db.scalar(select(BookingJourney).where(
        BookingJourney.tenant_id == tenant.id,
        BookingJourney.enquiry_id == row.id))
    quote = dict(journey.quote_state or {}) if journey else {}
    expired = False
    if quote.get("expires_on"):
        try:
            expired = date.fromisoformat(quote["expires_on"]) < date.today()
        except (TypeError, ValueError):
            pass
    row.status = ("quote_expired" if expired else
                  "quote_viewed" if quote.get("viewed_at") else
                  "quote_sent" if quote.get("status") == "sent" else
                  "quote_draft" if quote.get("packages") else "new")
    audit(db, "enquiry_reopened", "enquiry", row.id, actor=session.user,
          tenant_id=tenant.id, request=request)
    db.commit(); return {"ok": True, "status": row.status}


@app.post("/api/studio/bookings/{booking_id}/notes", status_code=201)
def add_booking_note(booking_id: str, payload: NoteIn, request: Request,
                     session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db); booking = studio_booking(db, tenant.id, booking_id)
    row = BookingNote(tenant_id=tenant.id, booking_id=booking.id, body=payload.body.strip(),
                      created_by_user_id=session.user.id)
    db.add(row); db.flush()
    audit(db, "booking_note_added", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"note_id": row.id})
    db.commit(); return note_json(row)


@app.delete("/api/studio/bookings/{booking_id}/notes/{note_id}")
def delete_booking_note(booking_id: str, note_id: str, request: Request,
                        session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db); studio_booking(db, tenant.id, booking_id)
    row = db.scalar(select(BookingNote).where(BookingNote.id == note_id,
                    BookingNote.booking_id == booking_id, BookingNote.tenant_id == tenant.id))
    if not row: raise HTTPException(404, "Note not found")
    db.delete(row); audit(db, "booking_note_deleted", "booking", booking_id,
                          actor=session.user, tenant_id=tenant.id, request=request,
                          detail={"note_id": note_id})
    db.commit(); return {"ok": True}


@app.post("/api/studio/tasks", status_code=201)
def create_task(payload: TaskIn, request: Request, booking_id: str | None = None,
                session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    if booking_id: studio_booking(db, tenant.id, booking_id)
    row = StudioTask(tenant_id=tenant.id, booking_id=booking_id,
                     created_by_user_id=session.user.id, **payload.model_dump())
    db.add(row); db.flush(); audit(db, "task_created", "booking" if booking_id else "task",
                                  booking_id or row.id, actor=session.user,
                                  tenant_id=tenant.id, request=request, detail={"task_id": row.id})
    db.commit(); return task_json(row)


@app.patch("/api/studio/tasks/{task_id}")
def update_task(task_id: str, payload: TaskUpdateIn, request: Request,
                session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(StudioTask).where(StudioTask.id == task_id, StudioTask.tenant_id == tenant.id))
    if not row: raise HTTPException(404, "Task not found")
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items(): setattr(row, key, value)
    row.completed_at = utcnow() if row.status == "completed" else None
    audit(db, "task_updated", "booking" if row.booking_id else "task", row.booking_id or row.id,
          actor=session.user, tenant_id=tenant.id, request=request,
          detail={"task_id": row.id, "status": row.status})
    db.commit(); return task_json(row)


@app.delete("/api/studio/tasks/{task_id}")
def delete_task(task_id: str, request: Request, session: UserSession = Depends(require_csrf),
                db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(StudioTask).where(StudioTask.id == task_id, StudioTask.tenant_id == tenant.id))
    if not row: raise HTTPException(404, "Task not found")
    subject_id = row.booking_id or row.id; db.delete(row)
    audit(db, "task_deleted", "booking" if row.booking_id else "task", subject_id,
          actor=session.user, tenant_id=tenant.id, request=request, detail={"task_id": task_id})
    db.commit(); return {"ok": True}


def safe_upload_name(value: str) -> str:
    name = Path(value or "file").name
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name)[:180] or "file"


@app.post("/api/studio/bookings/{booking_id}/documents", status_code=201)
async def upload_booking_document(booking_id: str, request: Request,
                                  file: UploadFile = File(...), description: str = Form(""),
                                  session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db); booking = studio_booking(db, tenant.id, booking_id)
    original = safe_upload_name(file.filename or "document")
    allowed = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".txt", ".doc", ".docx", ".xls", ".xlsx"}
    suffix = Path(original).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(422, "Upload a PDF, image, text, Word or Excel document")
    raw = await file.read(20 * 1024 * 1024 + 1)
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(413, "Documents must be 20 MB or smaller")
    folder = settings.tenant_storage_root / tenant.storage_key / "documents" / booking.id
    folder.mkdir(parents=True, exist_ok=True)
    storage_name = f"{secrets.token_hex(20)}{suffix}"
    target = folder / storage_name; target.write_bytes(raw)
    row = BookingDocument(tenant_id=tenant.id, booking_id=booking.id,
                          original_name=original, storage_name=storage_name,
                          content_type=file.content_type or "application/octet-stream",
                          size_bytes=len(raw), description=description.strip()[:500],
                          uploaded_by_user_id=session.user.id)
    db.add(row); db.flush(); audit(db, "document_uploaded", "booking", booking.id,
                                  actor=session.user, tenant_id=tenant.id, request=request,
                                  detail={"document_id": row.id, "name": original, "size_bytes": len(raw)})
    db.commit(); return document_json(row)


def booking_document_path(tenant: Tenant, row: BookingDocument) -> Path:
    return settings.tenant_storage_root / tenant.storage_key / "documents" / row.booking_id / row.storage_name


@app.get("/api/studio/documents/{document_id}")
def download_booking_document(document_id: str, context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    row = db.scalar(select(BookingDocument).where(
        BookingDocument.id == document_id, BookingDocument.tenant_id == tenant.id))
    if not row: raise HTTPException(404, "Document not found")
    path = booking_document_path(tenant, row)
    if not path.is_file(): raise HTTPException(404, "The stored document is missing")
    return FileResponse(path, media_type=row.content_type, filename=row.original_name)


@app.delete("/api/studio/documents/{document_id}")
def delete_booking_document(document_id: str, request: Request,
                            session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(BookingDocument).where(
        BookingDocument.id == document_id, BookingDocument.tenant_id == tenant.id))
    if not row: raise HTTPException(404, "Document not found")
    path = booking_document_path(tenant, row)
    if path.is_file(): path.unlink()
    booking_id = row.booking_id; name = row.original_name; db.delete(row)
    audit(db, "document_deleted", "booking", booking_id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"name": name})
    db.commit(); return {"ok": True}


def email_template_json(row: EmailTemplate) -> dict:
    return {"id": row.id, "name": row.name, "subject": row.subject,
            "body": row.body, "category": row.category, "is_active": row.is_active,
            "updated_at": row.updated_at.isoformat()}


@app.get("/api/studio/email-templates")
def list_email_templates(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    ensure_starter_email_templates(db, tenant)
    rows = db.scalars(select(EmailTemplate).where(
        EmailTemplate.tenant_id == tenant.id).order_by(EmailTemplate.category, EmailTemplate.name)).all()
    result = [email_template_json(row) for row in rows]
    db.commit()
    return result


@app.post("/api/studio/email-templates", status_code=201)
def create_email_template(payload: EmailTemplateIn, request: Request,
                          session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = EmailTemplate(tenant_id=tenant.id, **payload.model_dump())
    db.add(row); db.flush(); audit(db, "email_template_created", "email_template", row.id,
                                  actor=session.user, tenant_id=tenant.id, request=request)
    db.commit(); return email_template_json(row)


@app.put("/api/studio/email-templates/{template_id}")
def update_email_template(template_id: str, payload: EmailTemplateIn, request: Request,
                          session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(EmailTemplate).where(
        EmailTemplate.id == template_id, EmailTemplate.tenant_id == tenant.id))
    if not row: raise HTTPException(404, "Email template not found")
    for key, value in payload.model_dump().items(): setattr(row, key, value)
    audit(db, "email_template_updated", "email_template", row.id,
          actor=session.user, tenant_id=tenant.id, request=request)
    db.commit(); return email_template_json(row)


@app.delete("/api/studio/email-templates/{template_id}")
def delete_email_template(template_id: str, request: Request,
                          session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(EmailTemplate).where(
        EmailTemplate.id == template_id, EmailTemplate.tenant_id == tenant.id))
    if not row: raise HTTPException(404, "Email template not found")
    db.delete(row); audit(db, "email_template_deleted", "email_template", row.id,
                          actor=session.user, tenant_id=tenant.id, request=request)
    db.commit(); return {"ok": True}


def email_branding_json(row: TenantEmailBranding, tenant: Tenant) -> dict:
    return {"signoff": row.signoff, "signature_name": row.signature_name,
            "signature_role": row.signature_role, "telephone": row.telephone,
            "website": row.website, "show_logo": row.show_logo,
            "show_badge": row.show_badge,
            "owner_notifications_enabled": row.owner_notifications_enabled,
            "has_logo": bool(row.logo_path and Path(row.logo_path).is_file()),
            "has_badge": bool(row.badge_path and Path(row.badge_path).is_file()),
            "logo_url": "/api/studio/email-branding/assets/logo",
            "badge_url": "/api/studio/email-branding/assets/badge",
            "business_name": tenant.display_name}


def ensure_email_branding(db: Session, tenant: Tenant) -> TenantEmailBranding:
    row = db.get(TenantEmailBranding, tenant.id)
    if not row:
        row = TenantEmailBranding(tenant_id=tenant.id, signature_name=tenant.display_name)
        db.add(row); db.flush()
    return row


@app.get("/api/studio/email-branding")
def get_email_branding(context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    row = ensure_email_branding(db, tenant); result = email_branding_json(row, tenant)
    db.commit(); return result


@app.put("/api/studio/email-branding")
def update_email_branding(payload: EmailBrandingIn, request: Request,
                          session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db); row = ensure_email_branding(db, tenant)
    for key, value in payload.model_dump().items(): setattr(row, key, value)
    audit(db, "email_branding_updated", "tenant", tenant.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"owner_notifications_enabled": row.owner_notifications_enabled})
    db.commit(); return email_branding_json(row, tenant)


@app.post("/api/studio/email-branding/assets/{kind}")
async def upload_email_branding_asset(kind: str, request: Request, file: UploadFile = File(...),
                                      session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    if kind not in {"logo", "badge"}: raise HTTPException(404, "Email image type not found")
    suffix = Path(safe_upload_name(file.filename or "image")).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(422, "Upload a PNG, JPG or WebP image")
    raw = await file.read(3 * 1024 * 1024 + 1)
    if len(raw) > 3 * 1024 * 1024: raise HTTPException(413, "Email images must be 3 MB or smaller")
    folder = settings.tenant_storage_root / tenant.storage_key / "email-branding"
    folder.mkdir(parents=True, exist_ok=True); target = folder / f"{kind}{suffix}"
    for old in folder.glob(f"{kind}.*"):
        if old != target and old.is_file(): old.unlink()
    target.write_bytes(raw); row = ensure_email_branding(db, tenant)
    setattr(row, f"{kind}_path", str(target))
    audit(db, "email_branding_asset_uploaded", "tenant", tenant.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"kind": kind, "size_bytes": len(raw)})
    db.commit(); return email_branding_json(row, tenant)


@app.get("/api/studio/email-branding/assets/{kind}")
def get_email_branding_asset(kind: str, context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context; row = db.get(TenantEmailBranding, tenant.id)
    if not row or kind not in {"logo", "badge"}: raise HTTPException(404, "Email image not found")
    path = Path(getattr(row, f"{kind}_path", ""))
    if not path.is_file(): raise HTTPException(404, "Email image not found")
    return FileResponse(path)


@app.delete("/api/studio/email-branding/assets/{kind}")
def delete_email_branding_asset(kind: str, request: Request,
                                session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db); row = ensure_email_branding(db, tenant)
    if kind not in {"logo", "badge"}: raise HTTPException(404, "Email image type not found")
    path = Path(getattr(row, f"{kind}_path", ""))
    if path.is_file(): path.unlink()
    setattr(row, f"{kind}_path", "")
    audit(db, "email_branding_asset_deleted", "tenant", tenant.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"kind": kind})
    db.commit(); return {"ok": True}


@app.post("/api/studio/bookings/{booking_id}/emails/send", status_code=201)
def send_manual_booking_email(booking_id: str, payload: ManualEmailIn, request: Request,
                              session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db); booking = studio_booking(db, tenant.id, booking_id)
    client = db.scalar(select(Client).where(Client.id == booking.client_id, Client.tenant_id == tenant.id))
    mailbox = db.get(MailboxSetting, tenant.id)
    if not mailbox or not mailbox.smtp_verified_at or not mailbox.smtp_password_encrypted:
        raise HTTPException(409, "Verify the Studio outgoing email connection first")
    try:
        row = send_tenant_email(db, tenant, mailbox, str(payload.recipient), payload.subject,
                                payload.body, booking=booking, client=client,
                                template_id=payload.template_id,
                                extra={"client_portal_link": portal_url(booking_journey(db, booking))})
    except Exception as exc:
        audit(db, "manual_email_failed", "booking", booking.id, actor=session.user,
              tenant_id=tenant.id, request=request, detail={"error": str(exc)[:500]})
        db.commit(); raise HTTPException(422, "The email could not be sent. Check Email connection and try again") from exc
    audit(db, "manual_email_sent", "booking", booking.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"recipient": str(payload.recipient), "subject": row.subject, "email_message_id": row.id})
    db.commit(); return email_json(row)


@app.get("/api/studio/emails")
def list_email_messages(booking_id: str | None = None, unread_only: bool = False,
                        context=Depends(studio_context), db: Session = Depends(get_db)):
    _, _, tenant = context
    stmt = select(EmailMessage).where(EmailMessage.tenant_id == tenant.id)
    if booking_id: stmt = stmt.where(EmailMessage.booking_id == booking_id)
    if unread_only: stmt = stmt.where(EmailMessage.is_read.is_(False))
    rows = db.scalars(stmt.order_by(EmailMessage.sent_at.desc()).limit(250)).all()
    return [email_json(row) for row in rows]


@app.post("/api/studio/emails/{message_id}/read")
def mark_email_read(message_id: str, request: Request,
                    session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    row = db.scalar(select(EmailMessage).where(
        EmailMessage.id == message_id, EmailMessage.tenant_id == tenant.id))
    if not row: raise HTTPException(404, "Email not found")
    row.is_read = True; db.commit(); return {"ok": True}


def decode_mail_header(value: str | None) -> str:
    parts = []
    for raw, charset in decode_header(value or ""):
        if isinstance(raw, bytes):
            try: parts.append(raw.decode(charset or "utf-8", errors="replace"))
            except LookupError: parts.append(raw.decode("utf-8", errors="replace"))
        else: parts.append(raw)
    return "".join(parts).strip()


def extract_mail_bodies(message) -> tuple[str, str, list[tuple[str, bytes, str]]]:
    text_body = ""; html_body = ""; attachments = []
    for part in message.walk() if message.is_multipart() else [message]:
        content_type = part.get_content_type(); disposition = str(part.get("Content-Disposition") or "")
        filename = decode_mail_header(part.get_filename()) if part.get_filename() else ""
        raw = part.get_payload(decode=True) or b""
        if filename or "attachment" in disposition.lower():
            if raw: attachments.append((safe_upload_name(filename or "attachment"), raw, content_type))
            continue
        charset = part.get_content_charset() or "utf-8"
        if content_type in {"text/plain", "text/html"}:
            try: value = raw.decode(charset, errors="replace")
            except LookupError: value = raw.decode("utf-8", errors="replace")
            if content_type == "text/plain" and not text_body: text_body = value
            if content_type == "text/html" and not html_body: html_body = value
    if not text_body and html_body:
        text_body = re.sub(r"<[^>]+>", " ", html_body)
    return text_body[:100_000], html_body[:200_000], attachments


def match_email_booking(db: Session, tenant_id: str, address: str) -> Booking | None:
    client = db.scalar(select(Client).where(
        Client.tenant_id == tenant_id, func.lower(Client.email) == normalise_email(address)))
    if not client: return None
    return db.scalar(select(Booking).where(
        Booking.tenant_id == tenant_id, Booking.client_id == client.id
    ).order_by(Booking.event_date.desc(), Booking.created_at.desc()).limit(1))


@app.post("/api/studio/mailbox/sync")
def sync_mailbox(request: Request, session: UserSession = Depends(require_csrf),
                 db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db); mailbox = db.get(MailboxSetting, tenant.id)
    if not mailbox or not mailbox.imap_verified_at or not mailbox.imap_password_encrypted:
        raise HTTPException(409, "Verify the Studio incoming email connection first")
    try:
        if mailbox.imap_security == "ssl":
            connection = imaplib.IMAP4_SSL(mailbox.imap_host, mailbox.imap_port, timeout=25)
        else:
            connection = imaplib.IMAP4(mailbox.imap_host, mailbox.imap_port, timeout=25)
            if mailbox.imap_security == "starttls": connection.starttls(ssl_context=ssl.create_default_context())
        connection.login(mailbox.imap_username, decrypt_secret(mailbox.imap_password_encrypted))
        connection.select("INBOX", readonly=True)
        status_value, data = connection.uid("search", None, "ALL")
        if status_value != "OK": raise RuntimeError("Inbox search failed")
        uids = (data[0] or b"").split()[-100:]
        imported = 0; replies_paused = 0
        for uid_raw in uids:
            uid = uid_raw.decode()
            if db.scalar(select(EmailMessage.id).where(
                    EmailMessage.tenant_id == tenant.id, EmailMessage.folder == "inbox",
                    EmailMessage.external_uid == uid)):
                continue
            fetch_status, payload = connection.uid("fetch", uid, "(RFC822 FLAGS)")
            if fetch_status != "OK" or not payload or not isinstance(payload[0], tuple): continue
            message = email.message_from_bytes(payload[0][1])
            sender_header = decode_mail_header(message.get("From")); sender_pairs = getaddresses([sender_header])
            sender_address = normalise_email(sender_pairs[0][1]) if sender_pairs and sender_pairs[0][1] else ""
            booking = match_email_booking(db, tenant.id, sender_address) if sender_address else None
            text_body, html_body, attachment_parts = extract_mail_bodies(message)
            received = utcnow()
            try:
                parsed = parsedate_to_datetime(message.get("Date"))
                if parsed: received = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except Exception: pass
            row = EmailMessage(tenant_id=tenant.id, booking_id=booking.id if booking else None,
                               direction="inbound", folder="inbox", external_uid=uid,
                               message_id_header=str(message.get("Message-ID") or "")[:500],
                               in_reply_to=str(message.get("In-Reply-To") or "")[:500],
                               sender=sender_header[:500], recipient=decode_mail_header(message.get("To"))[:500],
                               subject=decode_mail_header(message.get("Subject"))[:500],
                               body_text=text_body, body_html=html_body, status="received",
                               is_read=False, sent_at=received)
            db.add(row); db.flush(); saved_attachments = []
            if attachment_parts:
                folder = settings.tenant_storage_root / tenant.storage_key / "mail" / row.id
                folder.mkdir(parents=True, exist_ok=True)
                for index, (name, raw, content_type) in enumerate(attachment_parts[:10]):
                    if len(raw) > 15 * 1024 * 1024: continue
                    stored = f"{index}-{secrets.token_hex(8)}-{name}"; (folder / stored).write_bytes(raw)
                    saved_attachments.append({"name": name, "stored": stored,
                                              "content_type": content_type, "size_bytes": len(raw)})
                row.attachments = saved_attachments
            if booking:
                actions = db.scalars(select(WorkflowAction).where(
                    WorkflowAction.tenant_id == tenant.id, WorkflowAction.booking_id == booking.id,
                    WorkflowAction.trigger_key == "quote_sent",
                    WorkflowAction.status.in_(["queued", "review", "pending"]))).all()
                for action in actions:
                    details = dict(action.payload or {}); details["reply_received_at"] = received.isoformat()
                    details["resume_status"] = action.status; action.payload = details; action.status = "paused"
                    replies_paused += 1
                audit(db, "client_reply_received", "booking", booking.id, tenant_id=tenant.id,
                      detail={"email_message_id": row.id, "quote_followups_paused": len(actions)})
            imported += 1
        connection.logout()
    except Exception as exc:
        audit(db, "imap_sync_failed", "mailbox", tenant.id, actor=session.user,
              tenant_id=tenant.id, request=request, detail={"error": str(exc)[:500]})
        db.commit(); raise HTTPException(422, "The inbox could not be refreshed. Check Email connection and try again") from exc
    audit(db, "imap_sync_completed", "mailbox", tenant.id, actor=session.user,
          tenant_id=tenant.id, request=request,
          detail={"imported": imported, "quote_followups_paused": replies_paused})
    db.commit(); return {"ok": True, "imported": imported, "quote_followups_paused": replies_paused}


@app.get("/api/studio/emails/{message_id}/attachments/{index}")
def download_email_attachment(message_id: str, index: int, context=Depends(studio_context),
                              db: Session = Depends(get_db)):
    _, _, tenant = context
    row = db.scalar(select(EmailMessage).where(
        EmailMessage.id == message_id, EmailMessage.tenant_id == tenant.id))
    if not row or index < 0 or index >= len(row.attachments or []):
        raise HTTPException(404, "Attachment not found")
    item = row.attachments[index]
    path = settings.tenant_storage_root / tenant.storage_key / "mail" / row.id / item["stored"]
    if not path.is_file(): raise HTTPException(404, "Attachment not found")
    return FileResponse(path, media_type=item.get("content_type"), filename=item.get("name") or "attachment")


@app.post("/api/studio/emails/{message_id}/reply", status_code=201)
def reply_to_email(message_id: str, payload: ManualEmailIn, request: Request,
                   session: UserSession = Depends(require_csrf), db: Session = Depends(get_db)):
    membership, tenant = studio_write_context(session, db)
    original = db.scalar(select(EmailMessage).where(
        EmailMessage.id == message_id, EmailMessage.tenant_id == tenant.id))
    if not original: raise HTTPException(404, "Email not found")
    mailbox = db.get(MailboxSetting, tenant.id)
    if not mailbox or not mailbox.smtp_verified_at: raise HTTPException(409, "Verify outgoing email first")
    booking = studio_booking(db, tenant.id, original.booking_id) if original.booking_id else None
    client = db.get(Client, booking.client_id) if booking else None
    try:
        row = send_tenant_email(db, tenant, mailbox, str(payload.recipient), payload.subject,
                                payload.body, booking=booking, client=client,
                                template_id=payload.template_id,
                                in_reply_to=original.message_id_header)
    except Exception as exc:
        db.commit(); raise HTTPException(422, "The reply could not be sent") from exc
    original.is_read = True
    audit(db, "email_reply_sent", "booking" if booking else "email_message",
          booking.id if booking else original.id, actor=session.user,
          tenant_id=tenant.id, request=request, detail={"email_message_id": row.id})
    db.commit(); return email_json(row)


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
            item["venue_search"] = {"provider": "google_places",
                                    "configured": bool(settings.google_maps_browser_api_key),
                                    "manual_entry_available": True}
        question_data.append(item)
    result = enquiry_form_json(row, tenant)
    result.update({"display_name": branding.get("display_name") or tenant.display_name,
                   "accent_colour": branding.get("accent_colour") or "#a9782e",
                   "packages": [{"id": item.id, "name": item.name,
                                  "price_pence": item.price_pence} for item in packages],
                   "questions": question_data,
                   "google_places": google_places_config()})
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
    row = Enquiry(tenant_id=tenant.id,
                  **payload.model_dump(exclude={"website", "answers", "venue_details"}),
                  venue_details=venue_details_dict(payload.venue_details))
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
    create_studio_notification(
        db, tenant, None, "new_enquiry",
        f"New enquiry from {' & '.join(filter(None, [row.first_name, row.partner_name]))}",
        "Their date, venue and answers are waiting in Enquiries.",
    )
    audit(db, "enquiry_received", "enquiry", row.id, tenant_id=tenant.id,
          request=request, detail={"workflow_trigger_recorded": True,
                                   "automatic_sending_paused": tenant.automations_paused})
    db.commit()
    return {"ok": True, "enquiry_id": row.id, "message": config.success_message,
            "automatic_reply": "paused" if tenant.automations_paused else "not_enabled"}
