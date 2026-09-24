# Stripe setup — Phase 5.9

The application ships with Stripe OFF. Existing bank transfers keep working. Installing this release does not connect Stripe, start subscriptions or charge anybody.

## Accounts and destinations

Ivory's Stripe account collects the subscription for each business. Couples' booking fees and balances use Stripe Connect direct charges on the selected business's connected account. No Ivory application fee is added. Stripe fees still apply. A business's saved bank account choice continues to control bank transfer instructions; it does not select the destination of a Stripe charge.

This integration uses Standard-account OAuth (`read_write`) and Stripe-hosted Checkout. Enable OAuth in Ivory's Connect settings. Each business connects its own Stripe account; the same account cannot be assigned to two businesses in this release. Reconnect can reauthorise the original receiving account, but cannot replace it with a different recipient. Never collect photographers' secret keys.

## Configure a separate test deployment first

Use a staging database and test businesses, not real invoices. Test-mode payments update that deployment's invoice and subscription records, so do not convert test transactions into production records. Keep the existing production deployment's Stripe disabled while validating staging.

Set the following in the deployment's existing server `.env` (the same env file is used by backend and worker):

```
STRIPE_ENABLED=true
STRIPE_LIVE_MODE=false
STRIPE_SECRET_KEY=your_test_secret_key
STRIPE_CONNECT_CLIENT_ID=your_test_Connect_client_ID
STRIPE_WEBHOOK_SECRET=your_platform_endpoint_signing_secret
STRIPE_CONNECT_WEBHOOK_SECRET=your_connected_accounts_endpoint_signing_secret
STRIPE_API_VERSION=2025-02-24.acacia
```

Do not paste keys into chat, put them in GitHub source, or replace the rest of your `.env`. Manager/Studio/Client URLs must point to this deployment. No publishable key is needed because the browser goes to hosted Checkout.

Register this exact OAuth redirect (substitute the staging Studio URL when testing):

`https://studio.ivorydigital.uk/api/stripe/connect/callback`

Create two **snapshot event** webhook destinations, API version `2025-02-24.acacia`:

1. **Your account / platform:** `https://studio.ivorydigital.uk/api/stripe/webhook/platform`
   - `checkout.session.completed`, `checkout.session.expired`
   - `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`
   - `invoice.paid`, `invoice.payment_failed`
2. **Connected accounts:** `https://studio.ivorydigital.uk/api/stripe/webhook/connect`
   - `checkout.session.completed`, `checkout.session.expired`
   - `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`
   - `account.updated`, `account.application.deauthorized`
   - `charge.refunded`
   - `charge.dispute.created`, `charge.dispute.updated`, `charge.dispute.closed`

Use each endpoint's own `whsec_...` secret. The platform endpoint rejects connected-account events and vice versa. Enable the Stripe Customer Portal for subscription payment-method updates and cancellation. Disable customer plan switching and quantity changes: Manager owns per-business prices. Confirm your desired cancellation timing in Stripe.

Restart backend and worker after changing environment values, using the existing private deployment workflow. Then the business owner opens **Payments & subscription → Connect this business’s Stripe**, completes Stripe's setup, returns and enables card payments. Account connection alone does not enable cards.

## Required staging checks before live use

- Create a second business free; switch between businesses and verify separate quotes, bank details and clients.
- Create a paid second business; verify Studio stays locked while the owner can access subscription checkout. Test successful payment, cancellation, declined payment, renewal and failed renewal.
- Check fee and full-balance card payments, card authentication, duplicate webhook delivery and delayed delivery. An ordinary checkout return must not mark an invoice paid.
- Pay by bank while an unpaid card checkout is open: recording the bank payment expires the open checkout. If card payment is already processing, wait for confirmation first.
- Refund a test card payment in Stripe, redeliver the refund event and check that only one adjustment appears. Review the private refund notice. Disputes require action in Stripe and generate a private notification; they do not silently rewrite booking status.
- Verify account readiness changes, OAuth denial/retry and reconnecting the original account. OAuth callbacks require the initiating owner to remain signed in; expired sessions must restart connection.
- Check webhook delivery logs, the correct connected-account recipient, PostgreSQL isolation under your deployed database role, and backup/rollback in your own environment.

For live use, configure production using live keys, the live Connect client ID and live webhook signing secrets, then set `STRIPE_LIVE_MODE=true` and `STRIPE_ENABLED=true`. Connect the actual businesses there. No live Stripe or TrueNAS access was available during development, so the staging checks above have not been performed against Stripe.

## Administration and support

**Manager → open a business → Businesses on this seat** lets you create the second business free or set a monthly/yearly charge. A paid second business stays locked until verified payment, or you record a bank payment with reactivation. Free access does not expire automatically. Automations stay paused until intentionally released.

Before changing a running Stripe subscription's price or granting that business free access, cancel its subscription in Stripe and wait for confirmation. Then save the new grant/price. This prevents granting free access while Stripe continues charging. Changing a paid access grant locks the business until the replacement charge is paid. No prorations or automatic refunds are initiated by Manager.

A deliberate Manager suspension stays in force when Stripe payments arrive; only Manager can clear that hold. Suspending Studio access does not cancel the Stripe subscription. Cancel in Stripe when charges must stop.

Manage the first business's price through the existing Billing & access screen. Use Payments & subscription in Studio to start or manage its card subscription. The bank-transfer subscription workflow remains available through Manager.

Checkouts are saved locally before contacting Stripe and use an idempotency key. An uncertain request is retried using that same saved request. Do not delete pending checkout rows to work around an error. Expire known open sessions in Stripe and allow the webhook to reconcile them. A checkout with no saved Stripe session ID after a prolonged timeout requires support reconciliation against Stripe request logs before further collection. Never blindly start a second charge.

Subscription revenue is recorded when signed events confirm paid Stripe invoices. Subscription refunds/credits are managed and reviewed in Stripe; automatic subscription-refund ledger adjustments are not included. Couple-card refunds are automatically reflected in the booking invoice. Dispute outcomes require a human review; no automatic cancellation or collection follows a dispute.

References: https://docs.stripe.com/connect/oauth-reference · https://docs.stripe.com/connect/direct-charges · https://docs.stripe.com/webhooks/signature · https://docs.stripe.com/billing/subscriptions/webhooks
