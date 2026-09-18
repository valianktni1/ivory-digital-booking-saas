# Ivory Digital Booking SaaS

A secure, multi-tenant booking platform built for wedding photographers and videographers.

The product is split into three deliberately separate experiences:

- **Manager** — `manager.ivorydigital.uk` for Ivory Digital platform administration.
- **Studio** — `studio.ivorydigital.uk/<business-name>` for each photographer or videographer.
- **Client** — `client.ivorydigital.uk/<business-name>` for enquiry forms and secure couple portals.

The platform never connects to or reuses the Weddings By Mark production database, uploads, credentials, mailbox or client records.

## Current release

Phase Five Guided Help includes the complete booking journey plus an interactive support layer:

- Tenant-isolated enquiry forms and custom questions.
- Enquiry conversion into a wedding journey.
- Package and optional-extra quote builder.
- Client quote choices start unselected; mandatory extras remain locked.
- Accepted quote snapshots, itemised invoices and tenant-specific sequential invoice numbers.
- Audited quote amendments before full payment and a full-payment lock afterwards.
- Manual bank, cash and card payment recording, booking-fee handling and pay-later arrangements.
- Editable contract templates, couple signature, studio countersignature and PDF downloads.
- Booking and final-timings questionnaires with downloadable PDFs.
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

Google Calendar connection requires an Ivory Digital Google OAuth web application with this redirect URI:

`https://studio.ivorydigital.uk/api/integrations/google-calendar/callback`

Card processing is not enabled in this release. Payments are deliberately recorded after they have been received by bank transfer, cash or another external method.

## Local development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --reload
```

For the latest release contents and TrueNAS deployment procedure, see `RELEASE-NOTES-PHASE-FIVE-GUIDED-HELP.md`.
