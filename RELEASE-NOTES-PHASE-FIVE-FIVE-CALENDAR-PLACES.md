# Ivory Digital Booking SaaS — Phase 5.5

## Per-tenant Google Calendar

- Every studio connects its own Google account through a short-lived,
  single-use OAuth state tied to the authenticated tenant and user.
- Refresh tokens remain encrypted at rest.
- The studio chooses from calendars where Google reports owner or writer access.
- Weddings and date blocks use deterministic event IDs, preventing duplicate
  events during ordinary retries.
- New events are created when absent and updated in place afterwards.
- Changing the selected calendar attempts to remove known Ivory Digital events
  from the former calendar before rebuilding them in the new calendar.
- The Calendar screen shows the account, chosen calendar, last successful sync,
  waiting records, errors and manual retry controls.
- A calendar failure never reverses a booking, payment, wedding-date change or
  availability block.
- Couples are not added as guests and Google is always called with client
  notifications disabled.

## Google Places and directions

- Published enquiry forms use Google's current Place Autocomplete element.
- Manual venue entry stays available when Google is unavailable or the venue is
  not listed.
- The selected venue's name, formatted address, Place ID and coordinates are
  stored on the tenant-owned enquiry and carried into its wedding booking.
- The photographer can open Google Maps directions directly from the wedding.
- Existing manually entered venues automatically receive a directions link too.
- The Google browser key is supplied by Ivory Digital and must be restricted to
  `https://client.ivorydigital.uk/*` in Google Cloud.

## Deployment safety

The compatibility step only adds nullable/defaulted venue and calendar columns.
It does not replace or delete any tenant, enquiry, wedding, invoice, payment,
workflow, email, contract, form, document or calendar record.

Expected health build:

`2026.09.20-phase-five-five-calendar-places`
