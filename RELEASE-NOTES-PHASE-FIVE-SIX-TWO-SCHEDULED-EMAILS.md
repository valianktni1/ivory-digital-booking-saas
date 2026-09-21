# Ivory Digital Booking SaaS — Phase 5.6.2

This is a complete release. It contains all Phase 5.6.1 enquiry, quote, email,
branding, agreement and layout improvements as well as the scheduled-email work
below. Phase 5.6.1 does not need to be deployed separately.

## Scheduled wedding check-ins

- The Emails & workflow page now says **New email**, not New workflow.
- A photographer can create as many wedding-date emails as required at the four
  clear intervals: **120, 90, 60 or 30 days before the wedding**.
- Each email can start from a saved template and can be disabled, held for
  review, or sent automatically after the platform safety pause is released.
- Two editable starter templates are added: Wedding planning check-in and Final
  timings request. Existing templates and photographer edits are preserved.
- A new Help article explains how to create several check-ins at different
  intervals.

## Final Timings form

- Only the 30-day scheduled email can include the Final Timings form.
- The server also enforces that rule, so a changed browser request cannot attach
  the Final Timings link to a 120, 90 or 60-day email.
- The 30-day email contains a large secure **Complete your final timings** button.
- That button opens the couple's private portal and scrolls directly to their
  actual Final Wedding Timings questionnaire.
- The supplied legacy Final Timings workflow step is upgraded safely when it has
  not been customised. Custom photographer wording is never overwritten.

## Safety and compatibility

- New scheduled emails apply to future matching booking events. They are not
  silently added to existing couples.
- Creating an email does not send it. Review-first is the default unless the
  photographer deliberately enables automatic sending.
- The tenant-wide automation safety pause still takes precedence over every
  individual email setting.
- No database schema migration is required for this release.
- Browser asset versions are now `phase-five-six-two-scheduled-emails`.

## Verification completed

- Python compilation passed.
- Studio and client JavaScript syntax checks passed.
- Automated suite: **9 passed**.
- Coverage includes a real 90-day scheduled check-in, the enforced 30-day Final
  Timings rule, private portal-link generation and direct Final Timings form
  navigation, plus all Phase 5.6.1 journey coverage.

Expected health build:

`2026.09.21-phase-five-six-two-scheduled-emails`
