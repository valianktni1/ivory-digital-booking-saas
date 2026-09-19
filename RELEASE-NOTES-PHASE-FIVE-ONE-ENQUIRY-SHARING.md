# Ivory Digital Booking SaaS — Phase 5.1 Enquiry Sharing

Build: `2026.09.18-phase-five-one-enquiry-sharing`

## Four practical ways to collect enquiries

The Enquiry form screen now includes a professional **Share your enquiry form** panel.

1. **Direct link** — copy the public form address for email, social media, Google Business or an ordinary website link.
2. **Website button** — copy a ready-made branded HTML button that opens the form in a new tab.
3. **Responsive website embed** — copy an iframe and resize helper that places the complete form inside an existing website.
4. **Downloadable QR code** — download an SVG QR code for printed material, wedding fairs or marketing graphics.

The sharing controls clearly show whether the form is still a draft. Links, embeds and QR downloads remain unavailable until the photographer deliberately publishes the form.

## Website embedding

- The embedded form removes the outer Ivory Digital client-area header and footer so it fits naturally into the photographer's website.
- It retains the studio's branding, questions, validation, availability check and private tenant routing.
- The supplied `embed.js` helper safely resizes the iframe when questions, validation or the success message changes its height.
- The generated code includes a useful minimum height, so the form remains usable even if a website blocks the optional resize script.
- WordPress and Elementor users can paste the generated code into an HTML widget.

## Framing security

- Only public enquiry-form paths may be framed by HTTPS websites.
- Secure couple portals continue to send `X-Frame-Options: DENY` and `frame-ancestors 'none'`.
- The resize message contains only the form height and tenant slug; it contains no couple answers.
- Existing honeypot, server validation and tenant isolation remain in force.

## Small reliability improvement

The Client container now explicitly packages `portal.css` as well as the new `embed.js`, ensuring both secure portals and embedded forms receive their intended presentation in a clean Docker build.

## Verification completed

- Python compilation passed.
- Studio, Client, embed helper and Manager JavaScript syntax checks passed.
- Stylesheet structure checks passed.
- Security-header checks confirm that portals remain non-embeddable while enquiry pages allow HTTPS framing.
- The clean test suite passed: `4 passed`.
- Tests include published-form QR generation and rejection for unpublished forms.

## Safe TrueNAS deployment

```sh
cd /mnt/apps/dockge/data/tenantsbookingsystem2026

SAAS_BACKUP="/mnt/tenant-weddingbookingsystem2026/ivory-saas-before-phase-five-one-enquiry-sharing-$(date +%Y%m%d-%H%M%S).dump"

sudo sh -c "docker exec tenantsbookingsystem2026-db pg_dump -U ivory_booking -d ivory_booking -Fc > '$SAAS_BACKUP'"

sudo ls -lh "$SAAS_BACKUP"

sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/ivory_digital_saas -o IdentitiesOnly=yes" git pull --ff-only origin main

sudo docker compose -f compose.yaml build --no-cache backend studio client

sudo docker compose -f compose.yaml up -d backend studio client

sudo docker compose -f compose.yaml ps

curl -fsS http://127.0.0.1:30061/api/health && echo
```

Expected health response:

```json
{"status":"ok","build":"2026.09.18-phase-five-one-enquiry-sharing","service":"ivory-booking-saas"}
```
