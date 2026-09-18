# Ivory Digital Booking SaaS — Phase Two

## Packages, pricing and photographer-owned workflows

Phase Two adds two connected Studio modules while keeping the platform's safety lock in place.

### Packages & Pricing

- Create and edit packages with a full price, booking fee and balance-due timing.
- Add clear inclusions and optionally highlight a popular package.
- Create optional, complimentary or mandatory add-ons.
- Mandatory add-ons require a written reason so they cannot be enabled casually.
- Couples will begin with no package or ordinary add-on selected.
- Packages and add-ons are archived by making them unavailable, preserving their history.

### Emails & Workflow

- Build a photographer's own enquiry-to-wedding journey.
- Use ten useful triggers from enquiry received through wedding completed.
- Add either a private manual task or an email step.
- Choose immediate, before or after timing in minutes, hours, days or weeks.
- Pause each step independently.
- Every new step is forced to paused, even if a browser attempts to submit it as active.
- Workflow edits create revision snapshots.

### Safety and tenancy

- Global tenant automations remain paused.
- This release configures workflow only; it contains no email or SMS sending route.
- Packages, add-ons, workflows, steps and revision history all have tenant ownership.
- PostgreSQL row-level security covers every new tenant table.
- Cross-tenant API tests verify that one studio cannot view or alter another studio's setup.

### Deployment

The backend creates the new tables safely during startup. Existing tenant, login and branding data are not replaced.

```sh
cd /mnt/apps/dockge/data/tenantsbookingsystem2026

SAAS_BACKUP="/mnt/tenant-weddingbookingsystem2026/ivory-saas-before-phase-two-$(date +%Y%m%d-%H%M%S).dump"

sudo sh -c "docker exec tenantsbookingsystem2026-db pg_dump -U ivory_booking -d ivory_booking -Fc > '$SAAS_BACKUP'"

sudo ls -lh "$SAAS_BACKUP"

sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/ivory_digital_saas -o IdentitiesOnly=yes" git pull --ff-only origin main

sudo docker compose -f compose.yaml build --no-cache backend worker studio

sudo docker compose -f compose.yaml up -d backend worker studio

sudo docker compose -f compose.yaml ps

curl -fsS http://127.0.0.1:30061/api/health && echo
```

Expected health build: `2026.09.18-phase-two`.

