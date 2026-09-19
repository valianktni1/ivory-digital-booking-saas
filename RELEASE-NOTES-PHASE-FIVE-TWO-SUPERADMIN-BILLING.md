# Ivory Digital Booking SaaS — Phase 5.2 Superadmin Billing

Build: `2026.09.18-phase-five-two-superadmin-billing`

This is one combined release. It includes the previously prepared enquiry-form sharing and interactive-help work as well as the new Superadmin billing and account controls. Phase 5.1 does not need to be deployed first.

## Manager billing centre

Manager now has a dedicated **Billing & access** workspace showing:

- projected monthly recurring revenue;
- subscription payments recorded this month;
- trial, past-due and suspended account counts;
- every photographer's plan price, billing cycle, renewal date and access status;
- permanent subscription-payment history.

Each business can be opened to:

- set a 30, 60, 90 or 120-day trial from today;
- set its plan name, price and monthly, annual or custom billing cycle;
- set the next payment date and a grace period;
- record a confirmed bank, card, Stripe, cash or other payment;
- suspend access manually when payment has not arrived;
- reactivate access after payment or an arrangement;
- opt into safe automatic suspension after the grace period.

Suspension never deletes any photographer, couple, booking, invoice, contract, form or uploaded data. It blocks Studio access and pauses unfinished workflow actions. Reactivation also leaves client automations paused until they are deliberately released.

## Automatic account protection

The worker now checks subscription state safely:

- expired trials are suspended;
- active subscriptions become past due after their renewal date;
- accounts are suspended only after their configured grace period when automatic suspension is enabled;
- every automatic status change is retained in the audit history;
- billing checks run before queued workflow delivery.

## Enquiry sharing included

The combined release also includes:

- direct public enquiry-form links;
- ready-made website-button HTML;
- responsive iframe embed code for WordPress, Elementor and other HTTPS sites;
- downloadable SVG QR codes;
- a clean embedded layout with automatic height updates;
- strict framing protection that keeps secure couple portals non-embeddable.

## Interactive help included

- page-aware natural-language answers inside Studio;
- screen-specific suggested questions;
- guided tours for the real Studio controls;
- a central Manager help library;
- no couple data sent to an outside AI service.

## Payment-provider boundary

This release provides complete manual subscription records and account enforcement. It does not yet collect SaaS subscription money automatically. Stripe or another provider can be connected later using its hosted checkout and signed webhooks once Ivory Digital has selected and configured the provider.

## Verification completed

- Python compilation passed.
- Manager, Studio, Client and embed-helper JavaScript syntax checks passed.
- New billing APIs were exercised through authenticated Manager tests.
- Tests cover trial extension, plan changes, payment history, manual suspension, safe reactivation and automatic overdue suspension.
- Tenant isolation, quote/invoice journeys, enquiry sharing and frontend security regression tests passed.
- Clean test suite result: `4 passed`.

## Safe TrueNAS deployment

This is the only release that needs to be deployed.

```sh
cd /mnt/apps/dockge/data/tenantsbookingsystem2026

SAAS_BACKUP="/mnt/tenant-weddingbookingsystem2026/ivory-saas-before-phase-five-two-$(date +%Y%m%d-%H%M%S).dump"

sudo sh -c "docker exec tenantsbookingsystem2026-db pg_dump -U ivory_booking -d ivory_booking -Fc > '$SAAS_BACKUP'"

sudo ls -lh "$SAAS_BACKUP"

sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/ivory_digital_saas -o IdentitiesOnly=yes" git pull --ff-only origin main

sudo docker compose -f compose.yaml build --no-cache backend worker manager studio client

sudo docker compose -f compose.yaml up -d backend worker manager studio client

sudo docker compose -f compose.yaml ps

curl -fsS http://127.0.0.1:30061/api/health && echo
```

Expected health response:

```json
{"status":"ok","build":"2026.09.18-phase-five-two-superadmin-billing","service":"ivory-booking-saas"}
```

No `.env` changes are required for this release. The two new additive billing tables are created automatically when the backend starts.
