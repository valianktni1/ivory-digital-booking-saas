# Phase 5.7.1 — help refresh

Includes all Phase 5.7 improvements below. Adds seven searchable photographer
help answers, updates tours for payment deadlines, scheduled emails, private
warnings, next actions and subscription details, and fixes cross-screen tour
navigation. Existing Manager-edited answers are preserved.

Couples now have an expandable “Need a hand?” guide in their private portal,
with context-aware “Show me where” buttons. It covers packages, payments,
agreements, draft saving and final-timings invitations. It contains no date-clash
information. The original upload did not include a separate interactive client
help panel; this adds one.

Use the same DEPLOY-PHASE-5.7.sh command; it verifies the new 5.7.1 build marker.

# Ivory Digital Phase 5.7

Build: `2026.09.22-phase-five-seven-private-studio`

## Private date warnings

Date conflicts are visible only in authenticated Studio enquiry and wedding records. They include the studio's own overlapping bookings and date blocks; bookings belonging to another tenant are excluded. New enquiries and quote acceptance generate a private Studio notification when a conflict exists. The quote-acceptance flow serializes same-studio acceptances on PostgreSQL before rechecking.

Couples never receive a date-clash warning. The public availability endpoint now returns a neutral acknowledgement with no available/unavailable boolean, and the public enquiry form no longer calls it. Quote acceptance continues normally; the photographer reviews coverage and handles any personal communication. This deliberately does not automatically refuse or cancel a couple's booking.

## Couple experience

- An explicit public response replaces the shared Studio response. Private notes, task records, email history, workflow actions, calendar state, audit details and financial notes are omitted. Public invoices also omit private payment notes and references.
- Live itemised quote total includes the chosen package, compulsory extras, optional extras and custom adjustments. Amounts retain pence.
- A next-step panel guides the couple through their quote, agreement, booking fee and forms.
- Uploaded studio logo and contact details appear in the private portal.
- Booking fee, balance deadline, bank instructions and invoice reference appear together.
- Questionnaires are collapsible and have a separate Save draft & return later action. Drafts are stored on the server under the booking and form, do not trigger submission workflows, and are removed on final submission.
- Unsaved changes are indicated and leaving the page prompts the browser's normal warning. Changes in another form must be saved before submitting one form, preventing their accidental loss on refresh.
- Final timings retain the existing sent-invitation gate. Draft endpoints enforce the same restriction.
- Agreement signing disables repeat clicks and reports when the confirmation email did not send, with the signed PDF still available.
- A suspended business's existing client portals become read-only. Existing invoices and signed agreements remain downloadable; quote acceptance, signatures and form changes are blocked. Cancelled accounts retain their previous unavailable behaviour.

## Photographer experience

- Private date-review banners on enquiry cards, enquiry workspaces and wedding workspaces.
- A next-action panel on the wedding overview.
- A configurable 0–90 day booking-fee deadline in Business & brand, applying to newly accepted quotes. Existing invoice deadlines are unchanged.
- Communications > Scheduled emails shows prepared messages, planned date/time, recipient, status and preview, with review/edit and skip actions where supported. The settings navigation now names Automation settings explicitly.
- My subscription shows plan, renewal/trial date and recorded payment history.
- Forgotten-password request and reset screens. A verified studio mailbox can send recovery email; if it is unavailable, Manager can issue a private reset link. Links expire after 30 minutes, are one-use, and are not saved in the Studio email history. Successful resets revoke existing sessions.
- More readable text and controls across quote, package, form and workspace screens.

## Manager experience

- Needs your attention view for failed workflow emails, calendar errors, incomplete email setup, expiring trials and overdue subscriptions.
- Support view includes last login, email/calendar status and booking-document storage usage. The storage figure is explicitly limited to booking documents; it is not a full disk quota report.
- Owner password recovery is available for existing accounts, separate from invitations.
- Audit entries identify the business and actor by name.
- Database-role diagnostic warns if the application is running as superuser or with BYPASSRLS.
- Last deployment database-backup metadata appears after the deployment script saves and validates the archive. Archive verification is not represented as a full restore test.

## Delivery and data safety

The worker scans eligible studios in separate batches; paused studios and non-email tasks no longer monopolise the first 25 queue entries. PostgreSQL row locks prevent concurrent workers from processing the same selected item. Owner notifications waiting for an unverified mailbox no longer occupy the first batch indefinitely. Main-loop failures are logged, and the heartbeat is refreshed only after a successful cycle.

SMTP delivery is not an exactly-once transport: a process interruption after SMTP acceptance but before database commit can still require manual investigation. Do not scale workers on the assumption that every crash case is automatically deduplicated.

Two additive tables are introduced: password_resets and questionnaire_drafts. No existing booking, invoice, payment, contract, upload or tenant is deleted. No credentials are rotated. Existing compose settings and private GitHub authentication are preserved.

## Validation completed

- 19 Python tests passed, including eight new behavioural tests for privacy, quote acceptance, drafts, password recovery, suspension, tenant operations and queue progression.
- Existing end-to-end application test retained; its public availability and quote-view assertions were updated to enforce the new privacy contract.
- JavaScript syntax checks for all main interfaces and Studio extension scripts.
- DOM interaction checks against actual API fixture responses for client quote totals/acceptance/drafts, Studio private warnings/next actions/subscription/scheduled emails, Manager support controls, and the reset form.
- Deployment shell syntax checked.

The remote browser could not reach the local preview. Pixel-level desktop/mobile appearance has not been visually certified. Docker builds, live SMTP/Google integration, PostgreSQL locking/RLS and TrueNAS deployment were not exercised in this workspace.

## Follow-on work that is not included

- Automatic subscription collection requires a chosen/configured payment provider; billing still records confirmed payments manually.
- Production database-role migration and a real PostgreSQL isolation test remain necessary. The new diagnostic identifies the issue; this release deliberately does not automatically alter live database privileges.
- Full per-tenant export/restore, storage quotas and backup restoration testing are separate work. Keep existing uploaded-file backups.
- Tenant two-factor authentication, full account/team management and audit-history filters remain future improvements.
- Draft saving is explicit, not timed autosave. Payment-deadline settings are studio-wide for new acceptances, not a new per-quote override.

## Deployment

See `DEPLOY-PHASE-5.7-TRUENAS.txt`. Commit and push the full update to the existing private repository, then pull and run `DEPLOY-PHASE-5.7.sh`. The script backs up the database and preserves old application images before rebuilding all five application services.
