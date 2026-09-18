# Ivory Digital Booking SaaS — Phase Four Journey RC1

Build: `2026.09.18-phase-four-journey-rc1`

## What this release proves

This release joins the previously separate setup, enquiry and workflow foundations into a complete tenant-safe booking journey. Northlight can now be used to proof the experience from a public enquiry through quote acceptance, invoice and payments, contract, questionnaires, calendar status and completion.

## Booking journey

- Convert an enquiry into a wedding without retyping the couple's details.
- Prepare a package quote with optional and mandatory extras.
- Send a private client-portal link for the couple to choose and accept.
- Keep quote choices unselected until the couple deliberately chooses one.
- Preserve the accepted quote as an audit snapshot.
- Amend an accepted quote before full payment, while preserving its previous revision.
- Lock commercial changes once the invoice has been paid in full.
- Create a human-readable sequential invoice number for each individual studio.
- Download invoice, contract and questionnaire PDFs with meaningful filenames.
- Record booking fees, balances and other manual bank/cash/card payments.
- Secure a date after payment or an explicit pay-later agreement.
- Complete a wedding with one deliberate action instead of a blocking checklist.

## Contracts and forms

- Create an editable studio agreement template.
- Issue it to a couple, collect the couple's signature, then countersign as the photographer.
- Build booking and final-timings questionnaires.
- View completed answers inside the wedding journey and download them as PDFs.

## Workflows

- Each step can be Disabled, Task only, Review first or Automatic.
- Timings belong to the studio rather than being imposed by Ivory Digital.
- Individual workflow steps can be paused for one couple without stopping the rest.
- Enquiry, quote, booking form, agreement, booking fee, balance and wedding-date triggers are supported.
- A safe starter workflow is created for new studios, with every step disabled.
- Automatic sending remains globally paused for proofing. Nothing will send automatically until Ivory Digital deliberately enables that tenant in Manager.

## Calendar

- Each studio connects its own Google account through Ivory Digital's OAuth application.
- Confirmed bookings, pay-later bookings and manual date blocks use one-way calendar events.
- Events are created without sending invitations to couples.
- Calendar sync failures are recorded for retry and never undo a booking action.
- Public availability checks include both secured weddings and date blocks.

Google Calendar live connection requires these private environment values:

```text
GOOGLE_CALENDAR_CLIENT_ID=
GOOGLE_CALENDAR_CLIENT_SECRET=
GOOGLE_CALENDAR_REDIRECT_URI=https://studio.ivorydigital.uk/api/integrations/google-calendar/callback
```

Leaving them blank is safe: the rest of the booking system works and Studio explains that calendar connection is not configured.

## Deliberate RC limitations

- Payments are manually confirmed records; Stripe or another online card processor is not yet connected.
- The form editor currently creates straightforward one-question-per-line fields. The backend model supports richer question types for a later editor.
- Venue entry is a polished manual field; Google Places search is not yet connected.
- Automatic messages must remain paused until Northlight proofing is explicitly signed off.

## Verification completed

- Python application compilation passed.
- Studio and Client JavaScript syntax checks passed.
- The full clean test suite passed: `4 passed`.
- Tests cover cross-tenant isolation, enquiry submission and conversion, quotes, invoice numbering, payments, amendment locking, contracts, questionnaires, date blocks, public availability and wedding completion.

## Safe TrueNAS deployment

```sh
cd /mnt/apps/dockge/data/tenantsbookingsystem2026

SAAS_BACKUP="/mnt/tenant-weddingbookingsystem2026/ivory-saas-before-phase-four-journey-rc1-$(date +%Y%m%d-%H%M%S).dump"

sudo sh -c "docker exec tenantsbookingsystem2026-db pg_dump -U ivory_booking -d ivory_booking -Fc > '$SAAS_BACKUP'"

sudo ls -lh "$SAAS_BACKUP"

sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/ivory_digital_saas -o IdentitiesOnly=yes" git pull --ff-only origin main

sudo docker compose -f compose.yaml build --no-cache backend worker studio client

sudo docker compose -f compose.yaml up -d backend worker studio client

sudo docker compose -f compose.yaml ps

curl -fsS http://127.0.0.1:30061/api/health && echo
```

Expected health response:

```json
{"status":"ok","build":"2026.09.18-phase-four-journey-rc1","service":"ivory-booking-saas"}
```

The database backup is taken before the application containers are rebuilt. Existing tenant data is preserved; this release adds new tables without modifying the structure of previously deployed tenant tables.
