# Phase 5.9 — two businesses and optional Stripe

## Included

- One owner login can switch between two isolated businesses on a seat. Manager alone creates the second business and grants free access or sets its monthly/yearly subscription price.
- Separate business settings, branding, packages, templates, banks, couples and Stripe recipients. Staff are not automatically added to a second business.
- A selected-business header blocks stale browser tabs from writing into a newly selected business.
- Optional Stripe Billing for Ivory subscriptions, including access to checkout while a paid business is locked, subscription management and signed payment confirmation.
- Optional Stripe Connect direct card payments for a couple's booking fee or remaining invoice balance. Bank transfer remains available, with the existing saved bank details.
- Server-calculated amounts, single pending checkout reuse, Stripe idempotency keys, signed event checks and transactional receipt deduplication.
- Couple refunds update their invoice once, with private notifications. Disputes and overpayments generate private review notices.
- Card checkout is unavailable in couple previews. Private date warnings remain excluded from all couple payloads.
- Updated interactive help and an owner payments tour step.
- Additive database upgrade and a backup-first private-repository deployment script.

## Validation and limits

Automated backend tests cover the existing banking, quote, preview, privacy and isolation behaviour, plus second-business access, stale tabs, payment failure, recipient scope, webhook replay, refunds, bank/card overlap, subscription event ordering and OAuth ownership/replay. UI DOM checks cover business switching, free access, the payment gate, fee/balance choices and the Manager form.

These use SQLite, mocked Stripe responses, and jsdom. They are not a live Stripe transaction test, a browser visual audit, a TrueNAS deployment or a PostgreSQL RLS integration test. Complete the staging checks in STRIPE-SETUP-PHASE-5.9.md before enabling real card payments. Stripe ships disabled.

Currency is GBP; recurring plans are monthly or yearly. Stripe account recipients are fixed per business. The release does not implement automatic subscription-refund accounting, dispute-outcome accounting or prorated plan changes; handle those in Stripe and review the records.

Existing businesses, bank snapshots, quotes and invoices are retained. The new nullable fields are safe for existing rows. No database, password or encryption-key replacement is needed.
