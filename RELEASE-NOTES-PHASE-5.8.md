# Phase 5.8 — Bank transition and quote review

Based on the complete Phase 5.7.1 release.

## Photographer controls

- Business settings → Manage two bank accounts. Each studio has its own two slots and private labels. Bank edits require its owner or administrator.
- Select Bank account for this quote in a saved quote template or individual quote.
- Review quote email shows offered packages, compulsory items, optional extras, discounts, per-package totals and the saved bank together before sending.
- Save & preview as the couple uses the actual client layout. Package/extra selections work for checking totals. Sending, acceptance, form submission and public downloads are disabled. It does not mark the quote as viewed or email the couple.
- A saved bank label and payment instructions appear inside the booking and on each invoice panel. Labels and template names stay private.
- Photographer help includes the bank transition and quote review instructions.

## Existing records and bank changes

Leave the original Bank transfer details fields alone during the transition. Account 1 can hold the current bank; account 2 can hold the new bank. Only the selected account's payment details are supplied to a couple. The other account and private labels are never included in their portal.

Quotes and invoices now save their own payment instructions. Keeping the same account selected when editing an existing draft retains its saved details; changing account selects the other bank for that draft. Applying a template prepares a new draft using the template's current bank choice. Accepted quotes remain locked.

At the first upgrade, legacy invoices/quotes that never stored bank details capture the original bank fields currently configured for their own studio. The system cannot reconstruct older bank details if those fields were already changed before this upgrade. Later edits to bank settings do not rewrite saved instructions, including PDF invoices and the couple's payment section.

## Privacy and validation

The couple preview uses the same explicit public-data allowlist as the client portal. Photographer-only date clashes, private notes, calendar blocks, template names, internal bank labels and other tenants' data remain excluded. Preview endpoints and their assets require a studio session.

UK bank sort codes require six digits (with optional hyphens) and account numbers eight digits. These are format checks, not bank ownership verification.

## Deployment

Unzip into the existing local repository, commit and push privately using GitHub Desktop. Do not copy .env files or remove existing live data. Then on TrueNAS:

    cd /mnt/apps/dockge/data/tenantsbookingsystem2026
    sudo git pull --ff-only && sudo bash DEPLOY-PHASE-5.8.sh

The script backs up and checks the database archive, retains rollback images, builds services and checks release health. No repository visibility change is required. The database migration adds nullable columns and captures legacy instructions; it does not delete invoices or bookings. Rollback instructions are printed by the script.

## Verification

26 backend tests passed, covering tenant isolation, quote acceptance, saved-bank PDF and portal output, disabled accounts, legacy invoice preservation, repeatable migration, public-data privacy and authenticated preview access. JavaScript DOM interaction checks passed for template bank persistence, quote payloads, required-item/discount totals and preview submission blocking. JavaScript syntax and deployment shell checks passed. Docker/TrueNAS deployment and visual browser review were not run here.
