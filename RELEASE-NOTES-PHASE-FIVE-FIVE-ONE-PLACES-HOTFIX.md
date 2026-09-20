# Phase 5.5.1 — Google Places client hotfix

## Fixed

- Includes `client/places.js` in the client Docker image so Google venue autocomplete can initialise on published enquiry forms.
- Missing JavaScript or stylesheet files now return `404` instead of the single-page HTML fallback.
- If Google venue search cannot load, couples see a clear manual-entry message instead of a silent failure.

## Deployment scope

Only the `client` service requires a rebuild and recreation. No database migration is required.

