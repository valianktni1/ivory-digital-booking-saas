# Ivory Digital Booking SaaS — Phase 5.4

## Complete Studio Workspace & Communications

This release deliberately combines the planned Phase 5.4 and Phase 5.5 work. It turns the strong booking core from Phase 5.3 into a practical daily workspace for photographers and videographers.

## What has changed

- The final private-beta polish pass improves visual hierarchy, readability, keyboard focus and touch targets throughout Studio.
- Setup now reaches a genuine 100% and folds into a compact review panel once complete, leaving Today at the top of everyday work.
- Mobile Studio has a persistent five-button working dock for Home, Enquiries, Weddings, Inbox and More.
- Home now includes a Today workspace for enquiries, review items, unread client updates, tasks, payments and upcoming weddings.
- The top search finds couples, venues, wedding dates—including UK-formatted dates—and invoice numbers.
- Each wedding now opens into clear tabs: Overview, Client journey, Emails, Payments, Files and Notes & activity.
- Photographers can edit couple details, safely move a wedding date, complete, cancel, archive or restore a wedding.
- Safe date moves check bookings and blocked dates, optionally move financial dates, move future wedding-date workflow items and resync Google Calendar.
- Private notes, tasks and tenant-isolated document storage are built into each wedding.
- Communications now includes the inbox, review queue, reusable templates and branded signature settings.
- Manual emails use the photographer's verified SMTP mailbox and keep a copy in the wedding history.
- IMAP refresh stores replies and attachments, matches known couples and pauses outstanding quote follow-ups when a reply is received.
- Unmatched incoming emails can be opened and replied to safely instead of becoming a dead end.
- Review-first emails can be edited before approval. Failed items can be retried and any open item can be deliberately skipped.
- Email signatures can include the photographer's own wording, telephone, website, logo and award badge.
- Optional owner notifications can be enabled for important client activity.
- Studios can create, duplicate, pause, activate and safely retire multiple workflows.
- New workflows and duplicated workflows start paused. Activating one affects future matching events only.
- Enquiries can be closed with an outcome and reopened later without deleting their history.
- Interactive help and guided tours cover the new daily workspace.
- Branded action dialogs replace abrupt browser prompts for cancellations and special payment arrangements.
- Action buttons now say exactly what will happen, such as Move wedding, Upload securely and Send reply.

## Safety and compatibility

- Existing Phase 5.3 data is preserved. New database tables are additive and are created at startup.
- No existing couple is retroactively added to a new or changed workflow.
- The global automation pause remains unchanged.
- SMTP/IMAP setup, templates and signature changes never start automatic sending.
- Incoming email HTML is stored for the record but the Studio interface displays safe escaped text.
- Every new tenant-owned table is included in PostgreSQL row-level security.
- Weddings By Mark production data, uploads and credentials remain completely separate.

## Verification completed

- Python application compilation.
- Studio JavaScript syntax validation.
- Full automated test suite.
- Cross-tenant isolation for notes, documents, templates, search and workflow copies.
- Booking edit and safe reschedule journey.
- Private task and file lifecycle.
- Enquiry close and reopen.
- Email branding and template isolation.
- UK wedding-date search, including `15/08/2027` style searches.
- Studio JavaScript, Client JavaScript and Manager JavaScript syntax checks.

## TrueNAS deployment

Run these commands only after the Phase 5.4 repository has been committed and pushed to the `main` branch.

```bash
cd /mnt/apps/dockge/data/tenantsbookingsystem2026

SAAS_BACKUP="/mnt/apps/dockge/data/tenantsbookingsystem2026/ivory-saas-before-phase54-$(date +%Y%m%d-%H%M%S).dump"

sudo sh -c "docker exec tenantsbookingsystem2026-db pg_dump -U ivory_booking -d ivory_booking -Fc > '$SAAS_BACKUP'"

sudo ls -lh "$SAAS_BACKUP"

sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/ivory_digital_saas -o IdentitiesOnly=yes" git pull --ff-only origin main

sudo docker compose -f /mnt/apps/dockge/data/tenantsbookingsystem2026/compose.yaml build --no-cache backend worker studio

sudo docker compose -f /mnt/apps/dockge/data/tenantsbookingsystem2026/compose.yaml up -d backend worker studio

sudo docker compose -f /mnt/apps/dockge/data/tenantsbookingsystem2026/compose.yaml ps

curl -fsS http://127.0.0.1:30061/api/health && echo
```

The expected health build is:

`2026.09.19-phase-five-four-polished-private-beta`

After deployment, refresh `studio.ivorydigital.uk` once. Test with the existing trial studio before inviting another photographer.
