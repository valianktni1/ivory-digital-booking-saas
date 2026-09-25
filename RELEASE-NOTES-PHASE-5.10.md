# Phase 5.10 — accounting pilot

Adds optional QuickBooks Online and Xero connections for tenant business owners. Sage Accounting remains planned; it is not implemented. Integrations ship disabled pending platform credentials and provider sandbox/demo acceptance.

## Changes

- Owner OAuth, encrypted refresh tokens and separate company binding per business.
- Sales/tax/payment-account mapping with an explicit confirmation and opt-in automatic syncing.
- Outbound contacts, invoices and recorded bank/Stripe payments; exact gross totals checked against provider responses.
- Durable export history and idempotent requests; uncertain writes stop for review rather than repeated creation. Owners can match a verified existing provider record.
- Review states for changed invoices, voids, refunds and unsupported payment types.
- Studio Accounting panel, Home tour step and three searchable help articles.
- Additive accounting tables with tenant isolation, off-by-default settings, setup guide and backup-first private-repository deployment script.

Existing two-business, Stripe, quote-bank and couple features remain included. Accounting controls and date-clash warnings are private to photographers. No accounting data is added to the couple portal.

## Boundaries

Outbound only, UK/GBP, one provider/company per business, one VAT-inclusive service line and tax code per invoice. No automatic historic matching, credit notes, remote edits, accounting-to-Ivory imports, fee/payout reconciliation or invoice emails. Bank receipts currently use one mapped ledger account per business, even when quote templates offer two bank accounts. See the setup guide for clearing-account handling.

## Validation

Validation passed: 46 tests in the full Python suite, followed by the expanded 11-test accounting suite (including two additional token-refresh tests), plus both Studio DOM suites. These cover simulated provider responses, totals, duplicate prevention, permissions, uncertain writes, review states and automatic-sync selection. JavaScript and deployment-script syntax are checked. Actual Xero/QuickBooks credentials, provider production approval, live exports and PostgreSQL staging acceptance remain deployment prerequisites. No server deployment has been performed as part of this package.
