# Accounting pilot — QuickBooks Online and Xero

This release contains outbound connectors for QuickBooks Online and Xero. Sage Accounting is planned and has no working connector in this release. Provider calls are covered by simulated tests, not by live provider approval or live-account testing. Keep accounting off for real tenants until the staging checks below pass.

## 1. Ivory Digital platform setup

Register one OAuth web application with each provider you intend to offer. Photographers authorise their own accounts through that app; they do not enter API secrets in Studio. Obtain any required production access/approval from the provider. Check its current terms, connection limits and charges before launch.

Add these settings to the server's existing `.env`. Never commit credentials or replace existing encryption keys:

```dotenv
ACCOUNTING_ENABLED=false
XERO_CLIENT_ID=
XERO_CLIENT_SECRET=
QUICKBOOKS_CLIENT_ID=
QUICKBOOKS_CLIENT_SECRET=
QUICKBOOKS_SANDBOX=true
```

Register exact HTTPS callbacks (adjust the domain if STUDIO_URL differs):

- Xero: `https://studio.ivorydigital.uk/api/accounting/xero/callback`
- QuickBooks: `https://studio.ivorydigital.uk/api/accounting/quickbooks/callback`

For staging, use the staging Studio URL and the corresponding registered callbacks. Keep test data separate from live bookings and accounts.

Xero uses authorization code OAuth with `offline_access accounting.invoices accounting.payments accounting.contacts accounting.settings.read`. Ensure the app permits these granular scopes. Start with a UK demo/test organisation using GBP; Xero has no separate sandbox API hostname in this connector. See [Xero OAuth](https://developer.xero.com/documentation/guides/oauth2/auth-flow/) and [granular scopes](https://developer.xero.com/faq/granular-scopes).

QuickBooks uses `com.intuit.quickbooks.accounting`. Begin with development credentials, a UK sandbox company and `QUICKBOOKS_SANDBOX=true`. After successful validation and Intuit production approval, use production credentials and set `QUICKBOOKS_SANDBOX=false` in the production environment. Do not switch an existing sandbox connection to live: use separate staging and production databases. See [Intuit OAuth](https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/oauth-2.0).

Enable `ACCOUNTING_ENABLED=true` only in the environment ready for testing/use, then recreate both backend and worker with the existing Compose file so they receive the new settings. A provider without its client ID and secret remains unavailable. Platform enablement alone does not export anything: each business must connect and save mappings; automatic syncing is off by default.

## 2. Photographer setup

1. Select the correct **Current business** in Studio and open **Accounting**. Only its owner can manage this.
2. Connect QuickBooks Online or Xero and authorise directly with the provider.
3. Choose the correct company. The company is bound to this business and its export history. A company cannot be assigned to two Ivory businesses; each business has one provider.
4. Choose the sales account/service item, sales tax code, bank account and Stripe clearing account. Create a suitable clearing account in the provider if needed. The current form requires both payment mappings even when Stripe is unused.
5. Confirm the tax and account choices with the business's accountant. Save with automatic syncing unchecked.
6. Choose one test invoice and **Sync invoice & payments**. Check its contact, gross total, tax, due date and payment allocation in the provider before enabling automatic sync.

One VAT-inclusive service line holds the full gross total; its description includes the original breakdown, including discounts. One selected tax code applies to the whole invoice. Mixed-tax invoices require manual accounting. GBP only. For a business that is not VAT registered, choose its appropriate provider tax code rather than assuming a rate. QuickBooks needs an active Service item; Xero needs an active sales/revenue account. Option lists currently read at most 1,000 QuickBooks records per category.

## 3. What sync does

- Exports issued positive invoices, contacts and recorded positive bank-transfer/Stripe payments. No invoice emails are sent. The provider assigns its own invoice number; Ivory's number appears in the description.
- No automatic matching/import of accounting history. Check for existing entries before manually exporting older invoices.
- Auto sync is opt-in. Its cut-off is when accounting settings were first saved: invoices since that time become eligible when auto sync is enabled. It also sends later payments on invoices already exported manually or automatically. One invoice per business is processed per worker cycle.
- Stripe receipts are gross, directed to the selected clearing account. Fees, payouts, bank reconciliation and incoming accounting payments are outside this release.
- Refunds, voids and changes after export are flagged for review. No automatic credit notes or remote invoice edits. Other payment methods also require review.
- Two local bank details can still be offered on quotes. This pilot maps all bank-transfer receipts to one accounting ledger account per business. If receipts go to two real bank accounts, use a suitable clearing account and reconcile them in the provider, or leave automatic sync off.
- Connection secrets are encrypted; owner-only controls and tenant-scoped export history are separate from the couple portal. Date-clash warnings remain photographer-only.

## 4. Failures and review

A confirmed rejected request can be retried after correction. An uncertain write is saved as sending/review and is not sent again automatically. Inspect the provider first. If it exists, use **Match existing accounting record** with the provider's record ID. The server checks the original identity/reference and amounts before linking. If absent, ask support to investigate; there is deliberately no blind reset button. Never delete export rows to force retries.

Invoice changes or refunds need an accountant to make the appropriate adjustments; matching a record does not correct its tax or create a credit. Disconnect removes local tokens and disables automatic sync but preserves company binding and export history. Revoke the grant in the accounting provider as well if you want provider-side revocation. Reconnect the original company.

## 5. Required staging acceptance before real exports

Use separate test companies and PostgreSQL with production-style tenant isolation. Check both providers:

- Owner OAuth connect/return, wrong-owner rejection, company selection and cross-business isolation.
- Token expiry and refresh rotation; reconnect and disconnect.
- Non-VAT and VAT-inclusive invoices, discounts, exact gross/tax totals and due dates.
- Partial/final bank payments and Stripe payments, including later payments after initial export.
- Repeat sync causes no duplicate invoice/payment; a simulated lost response stops for review and can be matched.
- Provider rejection is visible, mapping correction can be retried, and refunds/voids/edits stop for review.
- Automatic sync starts only when selected and does not unexpectedly export pre-cut-off history.
- Client portal still presents the saved bank details and optional Stripe, without accounting or date-warning details.

Keep accounting disabled if a provider returns an unexpected total or tax calculation. Automated local tests cannot replace this provider acceptance step.
