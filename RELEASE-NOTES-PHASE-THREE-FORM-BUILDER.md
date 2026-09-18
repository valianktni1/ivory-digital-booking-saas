# Ivory Digital Booking SaaS - Phase Three Form Builder

## A flexible enquiry form for every studio

This update expands Phase Three without enabling automatic sending.

### Studio form builder

- Adds a proper question list alongside the page wording.
- Seeds protected core questions for names, email, phone, date, venue, package interest and plans.
- Photographers can add, edit and remove their own questions.
- Supported answers: short text, long text, email, telephone, date, choose one, choose several, yes/no and venue search.
- Questions can be required, optional, visible or hidden.
- Choice questions require at least two valid options.
- Protected core questions cannot be accidentally deleted.

### Couple-facing form

- Renders each tenant's question order and wording.
- Package choices come from that studio's active packages and begin unselected.
- Venue questions use a search-style field and retain manual entry.
- The field is marked ready for Google Places; a platform Google key is not included in this release.
- Custom answers are stored with a historical copy of the question wording.
- Custom answers appear with the enquiry in Studio.

### Safety

- New question and answer tables use PostgreSQL tenant row-level security.
- Cross-tenant question editing is tested and blocked.
- Required questions are validated on the server, not only in the browser.
- Workflow emails remain paused.

### Deployment

```sh
cd /mnt/apps/dockge/data/tenantsbookingsystem2026

SAAS_BACKUP="/mnt/tenant-weddingbookingsystem2026/ivory-saas-before-phase-three-form-builder-$(date +%Y%m%d-%H%M%S).dump"

sudo sh -c "docker exec tenantsbookingsystem2026-db pg_dump -U ivory_booking -d ivory_booking -Fc > '$SAAS_BACKUP'"

sudo ls -lh "$SAAS_BACKUP"

sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/ivory_digital_saas -o IdentitiesOnly=yes" git pull --ff-only origin main

sudo docker compose -f compose.yaml build --no-cache backend studio client

sudo docker compose -f compose.yaml up -d backend studio client

sudo docker compose -f compose.yaml ps

curl -fsS http://127.0.0.1:30061/api/health && echo
```

Expected build: `2026.09.18-phase-three-form-builder`.
