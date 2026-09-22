# Ivory Digital Booking Studio - Phase 5.6.5

Build: `2026.09.22-phase-five-six-five-final-timings-gate`

## Final Timings now appears at the correct stage

- Accepting a quote reveals the Booking Questionnaire, but not Final Wedding Timings.
- Final Timings remains hidden until its scheduled email has been successfully sent.
- Photographers can send that request 60 days or 30 days before the wedding.
- The secure email button opens the correct couple's Final Timings form.
- A direct request to the form is blocked before release; hiding it is not only a visual change.
- If a couple has already submitted timings, the form remains available for later updates.
- Existing Phase 5.6.4 scheduled Final Timings actions are recognised, so no client work is lost.

## Studio wording

- The scheduled-email editor clearly shows that Final Timings is available at 30 or 60 days.
- Quote Templates explains that Final Timings is included for later but hidden until its email is sent.
- Help wording has been updated to describe the same workflow.

## Verification

- Python compilation passed.
- Studio and client JavaScript syntax checks passed.
- Full automated suite: `10 passed`.
- No destructive database migration is required.

