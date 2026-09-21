from datetime import date, timedelta
from pathlib import Path

import httpx

from app import main
from app.models import Booking, BookingJourney, Tenant, TenantCalendarConnection, TenantStatus
from app.schemas import PublicEnquiryIn


ROOT = Path(__file__).resolve().parents[1]


class CalendarDB:
    def __init__(self, connection):
        self.connection = connection

    def get(self, model, key):
        return self.connection if model is TenantCalendarConnection else None

    def scalar(self, statement):
        return "payment-present"


def response(status, body=None):
    request = httpx.Request("PUT", "https://www.googleapis.com/calendar/v3/test")
    return httpx.Response(status, json=body, request=request) if body is not None else httpx.Response(status, request=request)


def test_calendar_first_sync_creates_then_reuses_deterministic_event(monkeypatch):
    tenant = Tenant(id="tenant-alpha", slug="alpha", display_name="Alpha", owner_email="alpha@example.com",
                    status=TenantStatus.ACTIVE, trial_ends_at=main.utcnow() + timedelta(days=30))
    booking = Booking(id="booking-alpha", tenant_id=tenant.id, client_id="client-alpha",
                      title="Taylor & Jordan", event_date=date(2027, 8, 14), venue="Test Hall",
                      venue_details={"formatted_address": "Test Hall, Manchester", "place_id": "place-alpha"},
                      status="confirmed")
    journey = BookingJourney(booking_id=booking.id, tenant_id=tenant.id,
                             portal_token_hash="hash", portal_token_encrypted="token",
                             calendar_state={})
    connection = TenantCalendarConnection(tenant_id=tenant.id, calendar_id="alpha-calendar",
                                          refresh_token_encrypted="encrypted")
    calls = []

    monkeypatch.setattr(main, "google_configured", lambda: True)
    monkeypatch.setattr(main, "google_access_token", lambda connection: "access")

    def fake_request(method, path, access_token, payload=None):
        calls.append((method, path, payload))
        return response(404) if method == "PUT" else response(200, {"htmlLink": "https://calendar.google.com/event"})

    monkeypatch.setattr(main, "google_request", fake_request)
    state = main.sync_booking_calendar_safely(CalendarDB(connection), tenant, booking, journey)

    assert [item[0] for item in calls] == ["PUT", "POST"]
    assert calls[0][2]["id"] == calls[1][2]["id"] == state["event_id"]
    assert calls[1][2]["location"] == "Test Hall, Manchester"
    assert "destination_place_id=place-alpha" in calls[1][2]["description"]
    assert state["status"] == "synced"
    assert connection.last_synced_at is not None


def test_google_calendar_read_does_not_send_write_only_parameter(monkeypatch):
    captured = {}

    def fake_httpx(method, url, **kwargs):
        captured.update(kwargs)
        return response(200, {"items": []})

    monkeypatch.setattr(main.httpx, "request", fake_httpx)
    main.google_request("GET", "/users/me/calendarList", "access")
    assert captured["params"] is None


def test_venue_details_validation_and_directions_url():
    payload = PublicEnquiryIn(first_name="Taylor", email="taylor@example.com", venue="Test Hall",
                              venue_details={"place_id": "place-alpha", "name": "Test Hall",
                                             "formatted_address": "Test Hall, Manchester",
                                             "latitude": 53.4808, "longitude": -2.2426})
    details = main.venue_details_dict(payload.venue_details)
    url = main.venue_maps_url(payload.venue, details)
    assert details["place_id"] == "place-alpha"
    assert url.startswith("https://www.google.com/maps/dir/?api=1")
    assert "destination_place_id=place-alpha" in url


def test_phase55_frontends_and_security_policy_are_wired():
    client = (ROOT / "client/app.js").read_text(encoding="utf-8")
    places = (ROOT / "client/places.js").read_text(encoding="utf-8")
    studio = (ROOT / "studio/v55.js").read_text(encoding="utf-8")
    studio_v54 = (ROOT / "studio/v54.js").read_text(encoding="utf-8")
    client_html = (ROOT / "client/index.html").read_text(encoding="utf-8")
    studio_html = (ROOT / "studio/index.html").read_text(encoding="utf-8")
    nginx = (ROOT / "client/nginx.conf").read_text(encoding="utf-8")

    assert "venue_details" in client and "IvoryPlaces" in client
    assert "PlaceAutocompleteElement" in places and "includedRegionCodes" in places
    assert "Use this calendar" in studio and "Get directions" in studio
    assert "/places.js?v=phase-five-six-two-scheduled-emails" in client_html
    assert "/v55.js?v=phase-five-six-two-scheduled-emails" in studio_html
    assert "#final-timings" in client
    assert "questionnaire-final_timings" in client
    assert "＋ New email" in studio_v54
    assert "const allowedDays=[120,90,60,30]" in studio_v54
    assert "https://places.googleapis.com" in nginx
    assert "frame-ancestors https:" in nginx
