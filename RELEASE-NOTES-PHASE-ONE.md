# Phase One — the safe SaaS foundation

Phase One is the first independent Ivory Digital release. It is intentionally separate from the live Weddings By Mark booking app.

## Included

- Private Manager at port `30061`, protected by mandatory 2FA.
- Photographer Studio at port `30060`, with invitation setup and guided onboarding.
- Branded couple portal entry at port `30062` using tenant paths.
- PostgreSQL and Redis services with persistent TrueNAS datasets.
- Multi-tenant data guards at the API and PostgreSQL policy layers.
- 30-day trials, status controls, safe automation pause and audit history.
- Mobile layouts with generous edge spacing and clear, human wording.

## Deliberately not active yet

- Couple emails or SMS.
- Workflow automations.
- Payments.
- Google Calendar connections.
- Migration or copying of Weddings By Mark data.

Those arrive in controlled phases after the foundation is proved. No client contact can be triggered by this release.
