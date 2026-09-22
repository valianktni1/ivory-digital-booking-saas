# Ivory Digital Booking Studio — Phase 5.6.4

Build: `2026.09.22-phase-five-six-four-quote-templates`

## What changed

- Keeps the existing Packages & pricing designer unchanged.
- Adds a separate Quote templates setup screen.
- A quote template can include one or more packages, optional add-ons, compulsory add-ons, private discounts, an agreement and the forms used after acceptance.
- Opening an enquiry now offers a saved quote-template chooser above the existing manual quote editor.
- Choosing a quote template prepares the couple's quote and immediately opens the email-review step.
- The quote email screen now lets the photographer choose any active saved email template.
- The selected email is merged with the couple, wedding, venue and business details before it is shown.
- The photographer can add enquiry-specific wording without changing the master email template.
- The final HTML email always includes a branded **View your quote** button linked to that couple's private quote.
- Preparing or sending a quote leaves the record in Enquiries. It moves to Weddings only after quote acceptance or deliberate **Mark as booked**.

## Safety and data behaviour

- Quote templates are isolated by tenant in both application queries and PostgreSQL row-level security.
- Existing packages, add-ons, quotes, enquiries and accepted quote snapshots are preserved.
- Applying a template snapshots the current package wording and prices into the couple's draft quote.
- Later catalogue changes cannot rewrite an accepted quote.
- Saving a one-off change after applying a template retains its chosen agreement and form settings.
- The new database table is created additively during backend startup.

## Verification

- Python compilation passed.
- Studio JavaScript syntax checks passed.
- Full automated suite: `9 passed`.
- Tests cover cross-tenant template isolation, applying a template to an enquiry, compulsory add-ons, chosen email templates, link/button delivery metadata, quote acceptance and the existing enquiry-to-wedding safeguards.
