# Ivory Digital Booking SaaS — Phase Five Guided Help

Build: `2026.09.18-phase-five-guided-help`

## A genuinely useful Studio guide

Phase Five adds an interactive help experience throughout Studio. It is designed to feel like a friendly product expert rather than a technical manual or a generic chatbot.

- A Help button is always available in the Studio header and navigation.
- Photographers can type questions in everyday language.
- Answers are matched against Ivory Digital's curated knowledge without sending the question or client information to an outside AI provider.
- Suggested questions change with the screen being viewed.
- Answers can take the photographer directly to the correct Studio section.
- Guided tours highlight the real controls and explain them step by step.
- The drawer becomes a polished bottom sheet on mobile.
- Keyboard users can press `?` to open help and Escape to close it.
- A clear Contact Ivory Digital route remains available whenever personal help is needed.

## Manager-controlled knowledge

Manager now includes **Studio help library**.

- Edit the wording, summary, keywords and category of every answer.
- Decide which Studio screens should suggest it.
- Link an answer to the correct section and guided tour.
- Publish or hide an answer without deleting it.
- Add new answers as real photographer questions reveal gaps.
- Every Manager edit is retained in the private audit history.

The release starts with a carefully written guide covering setup, enquiries, quotes, accepted-quote amendments, payments, pay-later arrangements, contracts, questionnaires, workflow modes, per-couple pauses, email, Google Calendar, date blocks, packages and wedding completion.

## Safety retained

- Northlight's master automation pause remains unchanged.
- Help cannot enable an automation, send an email, alter a booking or record a payment.
- Help articles contain product guidance only and do not expose couple data.
- Draft Manager articles remain invisible in Studio.
- Existing tenant, booking and workflow data is preserved.

## Verification completed

- Python compilation passed.
- Manager, Studio and Client JavaScript syntax checks passed.
- HTML-to-JavaScript control checks passed.
- The clean test suite passed: `4 passed`.
- Tests now include help catalogue seeding, Studio question matching, CSRF protection and unpublished-article protection alongside the complete booking and tenant-isolation journey.

## Safe TrueNAS deployment

```sh
cd /mnt/apps/dockge/data/tenantsbookingsystem2026

SAAS_BACKUP="/mnt/tenant-weddingbookingsystem2026/ivory-saas-before-phase-five-guided-help-$(date +%Y%m%d-%H%M%S).dump"

sudo sh -c "docker exec tenantsbookingsystem2026-db pg_dump -U ivory_booking -d ivory_booking -Fc > '$SAAS_BACKUP'"

sudo ls -lh "$SAAS_BACKUP"

sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/ivory_digital_saas -o IdentitiesOnly=yes" git pull --ff-only origin main

sudo docker compose -f compose.yaml build --no-cache backend worker manager studio

sudo docker compose -f compose.yaml up -d backend worker manager studio

sudo docker compose -f compose.yaml ps

curl -fsS http://127.0.0.1:30061/api/health && echo
```

Expected health response:

```json
{"status":"ok","build":"2026.09.18-phase-five-guided-help","service":"ivory-booking-saas"}
```
