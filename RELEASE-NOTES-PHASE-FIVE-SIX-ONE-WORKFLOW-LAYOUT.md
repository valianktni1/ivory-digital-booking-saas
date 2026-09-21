# Ivory Digital Booking SaaS — Phase 5.6.1

## The intended enquiry-to-booking workflow

- A submitted enquiry stays inside **Enquiries** while the photographer opens it,
  prepares the quote, reviews the email and waits for the couple's decision.
- The quote builder now has four clear, readable sections: packages, extras,
  one-off items or discounts, and the personal message/expiry.
- Any offered extra can be changed from **Optional** to **Compulsory** for that
  individual quote. Catalogue-level compulsory extras remain locked on.
- The couple chooses one offered package and any optional extras in their secure
  private link. Compulsory extras are added by the server even if browser data is
  altered.
- Only acceptance promotes the provisional record into **Weddings**, preserves
  the accepted package/add-ons snapshot and creates the itemised invoice.
- The accepted quote remains visible in the couple's private area with a full
  itemised package, extras, adjustments and total.
- Booking and final-timings questionnaires unlock after acceptance.

## Contract completion

- When a quote is accepted, the latest active agreement is issued automatically
  as a fixed snapshot. Ivory Digital never invents legal wording.
- If no active agreement exists, acceptance still succeeds safely and Studio
  creates a clear action telling the photographer to add one.
- The couple signs once in their private area.
- Studio immediately countersigns with the saved business signature name (or the
  owner name when no signature has been saved).
- A completed PDF showing both signatures is generated, attached to the couple's
  confirmation email and stored in the Studio email history for later download.
- Signature data is committed even if SMTP is unavailable. Studio records the
  failed delivery and keeps the completed PDF accessible rather than losing the
  signed agreement.

## Email templates, button and branding

- New studios receive editable examples for the wedding quote, enquiry reply,
  quote follow-up, booking confirmation, signed contract and balance reminder.
- Existing Phase 5.6 studios receive the new signed-contract template once
  without restoring other starter templates they deliberately deleted.
- **Email templates** now has a direct Setup navigation item. Every template can
  be created, edited, hidden or deleted.
- The editor explains the available couple, wedding, business and secure-link
  variables.
- Quote and signed-agreement emails include a large branded secure-link button.
  The quote review dialog shows that button before the photographer sends.
- Business logo upload is now available directly under **Business & brand** as
  well as the email signature area. The stored image is embedded in outgoing
  Studio email.

## Layout and readability

- The enquiry quote workspace is wider and uses larger headings, labels, helper
  text, controls and cards.
- Package, add-on and email-template editors have been enlarged.
- The couple portal typography is larger and the accepted quote has a clear
  itemised breakdown.
- Desktop, tablet and mobile rules keep the quote sections usable without
  squeezing the wording into the narrow column seen in the Phase 5.6 screenshot.
- Asset versions were bumped to `phase-five-six-one-workflow-layout` so the new
  interface cannot be hidden by an older browser asset cache.

## Verification completed

- Python compilation passed.
- Studio and client JavaScript syntax checks passed.
- Automated suite: **9 passed**.
- The suite covers tenant isolation, compulsory per-quote extras, provisional
  enquiry lifecycle, quote delivery failure safety, acceptance/promotion,
  automatic contract issue, questionnaire unlocking, automatic countersigning,
  a real MIME PDF attachment, stored attachment download, editable starter
  templates and tenant-isolated logo upload.

Expected health build:

`2026.09.21-phase-five-six-one-workflow-layout`
