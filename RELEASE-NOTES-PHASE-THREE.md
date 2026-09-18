# Ivory Digital Booking SaaS - Phase Three

## Enquiries and professional email connection

Phase Three gives the workflow its real starting point and introduces secure mailbox setup.

### Enquiry journey

- Each studio has an editable enquiry form with its own wording and optional fields.
- Forms remain private drafts until the photographer deliberately publishes them.
- The public address follows `/business-name/enquire`.
- Active packages appear as optional interests; none is pre-selected.
- Submitted enquiries are isolated to the correct tenant and appear immediately in Studio.
- Submission records the `New enquiry submitted` workflow event.
- The initial acknowledgement email can now be designed against a genuine trigger.
- Automatic replies remain paused in this release.

### Email connection

- Separate SMTP outgoing and IMAP incoming settings.
- Photographer-specific sender name and email address.
- SSL/TLS, STARTTLS and unencrypted options for compatible providers.
- Passwords are encrypted at rest and are never returned to the browser or written to audit details.
- Separate outgoing and incoming connection tests.
- Mailbox connection does not enable workflow automations.
- Connection tests reject private, local and reserved network addresses and unsupported ports.

### Tenant addresses

- After login, Studio changes to `studio.ivorydigital.uk/business-name`.
- The slug is presentation and navigation; secure membership remains the access control.
- Editing another slug in the address cannot grant access to that tenant.

### Safe deployment

```sh
cd /mnt/apps/dockge/data/tenantsbookingsystem2026

SAAS_BACKUP="/mnt/tenant-weddingbookingsystem2026/ivory-saas-before-phase-three-$(date +%Y%m%d-%H%M%S).dump"

sudo sh -c "docker exec tenantsbookingsystem2026-db pg_dump -U ivory_booking -d ivory_booking -Fc > '$SAAS_BACKUP'"

sudo ls -lh "$SAAS_BACKUP"

sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/ivory_digital_saas -o IdentitiesOnly=yes" git pull --ff-only origin main

sudo docker compose -f compose.yaml build --no-cache backend worker studio client

sudo docker compose -f compose.yaml up -d backend worker studio client

sudo docker compose -f compose.yaml ps

curl -fsS http://127.0.0.1:30061/api/health && echo
```

Expected build: `2026.09.18-phase-three-enquiries-mailbox`.
