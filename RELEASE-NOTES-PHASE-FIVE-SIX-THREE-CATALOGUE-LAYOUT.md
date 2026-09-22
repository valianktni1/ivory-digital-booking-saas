# Ivory Digital Booking Studio — Phase 5.6.3

Release build: `2026.09.21-phase-five-six-three-catalogue-layout`

This is a complete replacement release. It contains every feature from Phases
5.6, 5.6.1 and 5.6.2 as well as the catalogue and layout work below.

## Package catalogue

- Packages now have a full long-form description as well as a short summary.
- The photographer controls the full price, booking fee/deposit, balance timing,
  display position, visibility and featured state.
- Full package wording is shown in the couple's quote.
- Package cards are larger and display their useful commercial details instead
  of reducing the catalogue to a small name-and-price row.
- Packages can be edited or deleted. Historical quote snapshots are not altered.

## Add-ons, compulsory items and discounts

- Add-ons have a description, price, display position, visibility and optional
  information link.
- An add-on can apply to all packages or only selected packages.
- A photographer can make an add-on compulsory by default and must record the
  reason for doing so.
- A photographer can still override an included add-on as optional or compulsory
  for a particular quote.
- Reusable private discounts can be saved in the catalogue. They are not shown
  as public add-ons; the photographer deliberately includes them while preparing
  a quote.
- The server resolves the real discount name and value, so a browser cannot
  tamper with a saved discount amount.
- Add-ons and discounts can be edited or deleted without changing accepted quote
  snapshots.

## Layout and deployment reliability

- The enquiry quote workspace uses the available desktop width and keeps the
  side information column readable.
- Package and add-on editors are substantially larger and use clear labels.
- Quote preparation is divided into packages, extras, adjustments and message.
- A missing Dockerfile copy rule for `v55.css` and `v55.js` is fixed. This was a
  real cause of a page appearing not to execute all of its client code.
- Every Studio and Client asset has a new release query string.
- The deployment guide verifies the Git checkout, the newly built images, the
  running containers and the public URLs separately. It stops the old build from
  being mistaken for the new one.

## Database compatibility

Startup adds these columns safely when upgrading an existing database:

- `service_packages.full_description`
- `package_add_ons.is_discount`

No existing package, quote, enquiry, wedding or client record is deleted.

## Verification completed

- 9 automated backend, tenant-isolation and journey tests passed.
- Python compilation passed.
- Studio and Client JavaScript syntax checks passed.
- The tests cover package wording, private discounts, compulsory rules, catalogue
  deletion, accepted quote preservation and cross-tenant access denial.

