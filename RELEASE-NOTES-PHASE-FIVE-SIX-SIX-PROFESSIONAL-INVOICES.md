# Ivory Digital Booking Studio - Phase 5.6.6

Build: `2026.09.22-phase-five-six-six-professional-invoices`

## Professional branded invoices

- Rebuilt the invoice PDF using the supplied Weddings By Mark invoice as the structural reference.
- Uses the photographer's uploaded business logo when available.
- Uses the studio's own accent colour throughout.
- Shows business contact details, couple details and all important invoice dates clearly.
- Shows packages, add-ons and discounts in a proper description-and-amount table.
- Package inclusions are split into readable, generously spaced paragraphs and bullets.
- Uses an embedded PDF font so letters render consistently without awkward character gaps.
- Displays subtotal, paid and total outstanding with a clear hierarchy.
- Keeps the payment schedule and bank-transfer panel together on the following page when required.
- Adds a discreet invoice reference and page number footer to every page.
- Uses the uploaded award badge at the end of the invoice when enabled.

## New Business & Brand settings

Photographers can now save:

- business address;
- invoice email, telephone and website;
- bank account name, sort code and account number;
- tax wording;
- an optional additional payment note.

These details are private and only appear on generated invoices.

## Safe invoice data

- Package descriptions come from the accepted quote snapshot, not a later edited package.
- Existing invoices automatically gain the new professional layout when downloaded again.
- No destructive database migration is required; invoice settings remain inside the existing tenant branding record.

## Verification

- Python and JavaScript syntax checks passed.
- The generated A4 invoice was rendered and inspected page by page.
- PDF text extraction confirmed all financial and payment information is present.
- Full automated suite: `11 passed`.

