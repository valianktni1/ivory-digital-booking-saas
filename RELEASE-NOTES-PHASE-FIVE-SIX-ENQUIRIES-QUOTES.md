# Ivory Digital Booking SaaS — Phase 5.6

## Enquiries stay as enquiries

- Opening an enquiry now creates a private provisional workspace without moving
  the couple into Weddings.
- The Enquiries screen keeps the original answers, message, contact details,
  date and venue available while the quote is prepared.
- Enquiry states now show New, Quote draft, Quote sent, Quote viewed, Quote
  expired, Closed and Booked.
- A couple moves into Weddings only when they accept the quote or the studio
  deliberately chooses **Mark as booked**.
- Provisional enquiries are excluded from the Weddings list, wedding counts,
  upcoming-wedding panels, calendar events and booking search results.
- Search results and Today links open the correct enquiry directly.
- The enquiry list refreshes safely while it is open and also checks again when
  the browser regains focus. An open workspace or dialog is never interrupted.

## Complete quote workflow inside Enquiries

- Packages and extras can be selected from the enquiry workspace.
- Extras can be restricted to one or more eligible packages.
- Custom positive items and private discounts can be added to a quote.
- An optional quote expiry date and personal note can be saved.
- Saving creates a draft only and never sends an email.
- **Save & review email** shows the exact recipient, subject, message and secure
  quote link before the studio explicitly chooses **Send quote now**.
- Quote emails are delivered through the tenant's verified SMTP connection and
  are recorded in the enquiry email history.
- A delivery failure leaves the quote as a draft and shows a human-readable
  error instead of falsely marking it as sent.
- The couple's quote page starts with no package selected and only displays
  extras that are valid for the package they choose.
- Booking forms stay hidden and blocked until the quote is accepted.
- Quote acceptance is transaction-locked on PostgreSQL to prevent duplicate
  invoices from near-simultaneous clicks.
- Acceptance promotes the provisional record, creates one itemised invoice,
  marks the enquiry Booked and then makes it visible in Weddings.

## Safety and compatibility

- Existing unaccepted quote-preparation records are restored to Enquiries during
  the additive startup migration. Accepted or invoiced bookings remain Weddings.
- Closing an enquiry cancels only that enquiry's unsent workflow items.
- Accepting or manually booking an enquiry cancels remaining quote-chasing
  messages before starting the booked-client workflow.
- Manual booking safely requests a Google Calendar sync when Calendar is
  connected; a calendar error never reverses the booking.
- API validation messages are rendered as readable text instead of
  `[object Object]`.
- Asset versions were changed to `phase-five-six-enquiries-quotes`, preventing
  an older browser bundle from surviving deployment.

## Database changes

The compatibility migration adds only:

- `bookings.is_provisional`
- `bookings.promoted_at`
- `package_add_ons.eligible_package_ids`
- an index for provisional booking filtering on PostgreSQL

It does not delete or replace tenant, enquiry, booking, invoice, payment,
workflow, email, contract, form, document or calendar data.

## Verification completed

- Python application compilation passed.
- Studio and client JavaScript syntax checks passed.
- Automated test suite: **9 passed**.
- Covered: tenant isolation, provisional lifecycle, quote drafts, package-aware
  extras, email preview, SMTP failure safety, real send state, first-view state,
  quote acceptance, invoice creation, workflow cancellation and final promotion
  into Weddings.

Expected health build:

`2026.09.21-phase-five-six-enquiries-quotes`

Phase 3 (business logo management and re-use across Studio, emails, quotes and
communications) is intentionally not included in this release.
