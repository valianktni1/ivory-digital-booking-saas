# Ivory Digital Phase 5.5.2 — Google Places loader hotfix

## Fault confirmed

The live client loaded Google Maps with `loading=async`, but `places.js` treated the script element's ordinary `load` event as proof that `google.maps.importLibrary` was ready. Google completes its bootstrap asynchronously, so the check could run too early. The enquiry form then permanently displayed the manual-entry fallback even though Google continued loading its supporting scripts.

## Changes

- Replaced the timing-sensitive `script.onload` check with Google's supported dynamic-library bootstrap and Promise callback.
- Loads the Places library once and waits for `PlaceAutocompleteElement` before enhancing venue questions.
- Retains manual venue entry if Google is genuinely unavailable.
- Logs the real failure and stores it on the venue note as `data-places-error` for future diagnosis.
- Bumped all public client asset versions to `phase-five-five-two-places-loader` so browsers request the new files.

## Scope

Only these public client files change:

- `client/places.js`
- `client/app.js`
- `client/index.html`

No database, booking, tenant-data, calendar, email, quote, contract, invoice, or personal Weddings By Mark application files are changed.

## Test after deployment

Open:

`https://client.ivorydigital.uk/northlight-wedding-studio/enquire`

The venue question should show a separate Google venue-search box above the manual-entry field. Search for `Higher Trapp Hotel`, choose the suggestion, and confirm the exact address appears beneath it.
