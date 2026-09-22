# Ivory Digital Booking SaaS

A secure, multi-tenant booking platform built for wedding photographers and videographers.

The product is split into three deliberately separate experiences:

- **Manager** — `manager.ivorydigital.uk` for Ivory Digital platform administration.
- **Studio** — `studio.ivorydigital.uk/<business-name>` for each photographer or videographer.
- **Client** — `client.ivorydigital.uk/<business-name>` for enquiry forms and secure couple portals.

The platform never connects to or reuses the Weddings By Mark production database, uploads, credentials, mailbox or client records.

## Current release — Phase 5.7.1 (help refresh)

Phase 5.7 adds Studio-only date warnings, a deliberately restricted client API,
itemised live quote totals, saved questionnaire drafts, account recovery,
Manager health/support controls and clearer next steps.

Read [the Phase 5.7 release notes](RELEASE-NOTES-PHASE-5.7.md) for the completed
changes, validation and remaining production checks. Use
[the private-repository deployment instructions](DEPLOY-PHASE-5.7-TRUENAS.txt).

## Earlier platform foundation

Phase 5.5 adds production-ready tenant Calendar setup and exact venue handling to the complete Phase 5.4 photographer workspace. It includes:

- A separate Google OAuth connection and encrypted refresh token for every tenant.
- A photographer-facing picker containing only calendars they can write to.
- Deterministic booking and date-block events with safe create, update, removal and retry.
- Calendar health counts, last successful sync, errors and a one-wedding retry control.
- No Google guests or couple email invitations.
- Google Places autocomplete on every published tenant enquiry form, with manual entry retained.
- Structured venue name, address, Place ID and coordinates carried from enquiry to wedding.
- One-tap Google Maps directions from the photographer's wedding workspace.

Phase 5.4 supplied the complete photographer workspace and communications release, including:

- A final private-beta polish pass with clearer typography, stronger mobile navigation, accessible focus states and calmer everyday screens.
- Setup that reaches a genuine 100%, then folds away so returning photographers land on the work that matters today.
- A practical Today dashboard for new enquiries, approvals, replies, tasks, payments and upcoming weddings.
- Fast global search across couples, UK-formatted dates, venues and invoice numbers.
- A tabbed couple workspace for Overview, Client journey, Emails, Payments, Files and Notes & activity.
- Safe wedding detail editing, audited date moves, clash checks, financial-date movement and calendar resync.
- Private notes and tasks with due dates and one-tap completion.
- Tenant-isolated wedding document uploads and downloads.
- An organised Communications centre with Inbox, Review queue, Templates and Signature.
- Manual personal email through each photographer's verified SMTP connection.
- IMAP inbox refresh, matched couple replies, attachments and reply-aware quote-follow-up pauses.
- Safe replies to unmatched incoming messages, ready to be linked to a wedding later.
- Reusable email templates that never send by themselves.
- Editable branded signatures with an embedded logo and award badge.
- Optional owner email notifications for quote acceptance, signatures and submitted forms.
- Review-first message editing, approval, retry and skip controls.
- Multiple workflows with safe paused copies and deliberate future-events-only activation.
- Enquiry close/reopen outcomes and wedding archive/restore controls.
- Expanded page-aware help and tours for the complete working area.

The full platform still includes:

- Tenant-isolated enquiry forms and custom questions.
- Enquiry conversion into a wedding journey.
- Package and optional-extra quote builder, including optional secure webpage links for more information.
- Client quote choices start unselected; mandatory extras remain locked.
- Accepted quote snapshots, itemised invoices and tenant-specific sequential invoice numbers.
- Audited quote amendments before full payment and a full-payment lock afterwards.
- Manual bank, cash and card payment recording, booking-fee handling and pay-later arrangements.
- Editable contract templates, couple signature, studio countersignature and PDF downloads.
- Complete, sectioned Booking Questionnaire and Final Wedding Timings starter forms based on a real wedding-photography workflow.
- A visual form builder for adding, editing, moving and removing questions, selecting answer types, adding guidance and setting required answers.
- Couple form updates with previously submitted answers safely pre-filled, plus downloadable PDFs and immutable submission snapshots.
- Secure branded couple portal links.
- Configurable workflows with Automatic, Review first, Task only and Disabled modes.
- Per-couple workflow controls and a global automation safety pause.
- Google Calendar OAuth foundation, deterministic one-way events and date blocking.
- One-click wedding completion and clear cancellation controls.
- Responsive Manager, Studio and Client interfaces.
- A page-aware Studio help drawer that answers normal-language questions without sending client data to an outside AI service.
- Suggested questions tailored to the screen currently being viewed.
- Interactive guided tours that highlight the real controls step by step on desktop and mobile.
- A central Manager help library where Ivory Digital can edit, publish or hide answers for every studio.
- Enquiry-form sharing with a direct link, ready-made website button, responsive website embed and downloadable QR code.
- A clean embedded enquiry layout with automatic height updates for WordPress, Elementor and other HTTPS websites.
- A dedicated Manager billing centre with plan prices, billing cycles and renewal dates.
- One-click trial periods of 30, 60, 90 or 120 days.
- Manual subscription-payment history for bank transfer, card, Stripe, cash or other confirmed payments.
- Manual and automatic non-payment suspension with a configurable grace period.
- Safe reactivation that restores Studio access while leaving client automations paused.
- No suspension or trial action deletes photographer, couple, booking, invoice or document data.

## Safety defaults

- Every tenant starts with automatic messaging globally paused.
- Starter workflow steps are supplied but disabled.
- SMTP/IMAP connection tests do not activate messages.
- A calendar failure never reverses a booking, payment or date block.
- Calendar events do not invite couples or expose their email addresses.
- PostgreSQL row-level security protects tenant-owned records.
- Passwords and provider tokens are encrypted at rest.
- Manager actions and important booking changes are audited.

## External services

Google Calendar connection requires one Ivory Digital Google OAuth web application with this redirect URI:

`https://studio.ivorydigital.uk/api/integrations/google-calendar/callback`

Venue autocomplete requires a browser-restricted Google Maps Platform key with
Maps JavaScript API and Places API (New) enabled. Restrict the key to:

`https://client.ivorydigital.uk/*`

Automated SaaS card collection is not connected in this release. Manager can securely record confirmed subscription payments now; a payment-provider checkout and webhook can be added once Ivory Digital chooses and configures its provider.

Couple payments inside each photography studio are still deliberately recorded only after the photographer has received them by bank transfer, cash, card or another external method.

## Local development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --reload
```

For the latest release contents and TrueNAS deployment procedure, see `RELEASE-NOTES-PHASE-5.7.md` and `DEPLOY-PHASE-5.7-TRUENAS.txt`.
