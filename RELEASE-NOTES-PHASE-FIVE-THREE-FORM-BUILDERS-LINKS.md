# Ivory Digital Booking SaaS — Phase 5.3 Form Builders & Information Links

Build: `2026.09.19-phase-five-three-form-builders-links`

Phase 5.3 installs directly over the already-deployed Phase 5.2 release. It does not require a separate intermediate deployment and it does not remove or replace existing studio, couple, booking, invoice, contract, form-submission or billing data.

## Complete couple forms

Every studio now receives two substantial, photographer-designed starter forms:

- **Wedding Booking Form** — names and contact details, home address, wedding date and ceremony time, ceremony and reception locations, chosen package, payment preference, wedding-party size, special events, guest uploads, music and additional planning information.
- **Final Wedding Timings** — confirmed ceremony and reception details, preparation locations and travel, start preferences, group photographs, meal, speeches, evening guests, cake cutting, first dance, later events, wedding-day contacts and important family notes.

The starter forms are intentionally editable. Existing studios receive them only where a form type is missing; the upgrade does not overwrite a studio's saved questions.

## Visual form builder

Under **Contracts & forms**, the photographer can now:

- switch between Booking Questionnaire and Final Wedding Timings;
- edit the form name and warm introduction;
- add, edit, remove and reorder questions;
- group questions into clear named sections;
- choose short text, long text, email, phone, number, date, time, yes/no, single-choice or multiple-choice answers;
- add optional guidance and placeholders;
- make individual answers required;
- restore the complete starter with an explicit confirmation.

Restoring or editing a template never changes an already submitted form or its PDF snapshot. Couples can reopen a submitted form, see their existing answers and safely update it when plans change.

## Package and add-on information pages

Packages and add-ons now have an optional **More information webpage** field. A photographer can paste a secure `https://` page containing fuller package, album or service details. The couple sees a clear link beside the item in their quote, and it opens separately without selecting or changing the quote.

The two new database fields are additive and are created automatically on startup. Existing packages and add-ons remain unchanged with an empty link.

## Interactive help updated

The Studio help drawer and Contracts & forms tour now explain the visual builders, sections, answer types, saved submission snapshots and starter restore. A new help answer explains package and add-on information links. The one obsolete built-in “one question per line” answer is updated automatically only when it still contains the original Ivory Digital wording; a Manager-edited version is preserved.

## Verification completed

- Python compilation passed.
- Manager, Studio and Client JavaScript syntax checks passed.
- A clean automated suite passed: `5 passed`.
- Tests cover the additive Phase 5.2 database upgrade, tenant isolation, complete starter forms, form restoration, preserved submitted answers, information-link snapshots, quote acceptance, invoices, contracts and PDF downloads.

## Safe TrueNAS deployment

```sh
cd /mnt/apps/dockge/data/tenantsbookingsystem2026

SAAS_BACKUP="/mnt/tenant-weddingbookingsystem2026/ivory-saas-before-phase-five-three-$(date +%Y%m%d-%H%M%S).dump"

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
{"status":"ok","build":"2026.09.19-phase-five-three-form-builders-links","service":"ivory-booking-saas"}
```

No `.env` changes are required.
