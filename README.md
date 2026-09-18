# Ivory Digital Booking System

An independent, multi-tenant booking platform for wedding photographers and videographers.

Phase One establishes the safe foundation and three deliberately separate experiences:

- **Manager** — `manager.ivorydigital.uk` for Ivory Digital platform administration.
- **Studio** — `studio.ivorydigital.uk` for each photographer or videographer.
- **Client** — `client.ivorydigital.uk/<business-name>` for branded couple portals.

This repository does not connect to or reuse the Weddings By Mark production database, uploads, credentials, email configuration or client records.

## Phase One features

- Isolated photographer businesses with PostgreSQL row-level security.
- Separate, opaque file-storage directory for every tenant.
- Secure owner invitations that expire and work once.
- Argon2 passwords, server-side sessions and CSRF protection.
- Mandatory authenticator-app 2FA for the private Manager.
- 30-day trials with automatic messages paused by default.
- Audited support controls that expose health and counts, not couple details.
- Professional responsive Manager, Studio and Client interfaces.
- Guided Studio onboarding and editable business branding.

## Local development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest
uvicorn app.main:app --reload
```

For the TrueNAS installation, follow `DEPLOY-PHASE-ONE-TRUENAS.md` exactly.
