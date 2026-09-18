from urllib.parse import parse_qs, urlparse

import pyotp
from fastapi.testclient import TestClient

from app.main import app


def csrf(client: TestClient) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ivory_booking_csrf")}


def make_tenant(manager: TestClient, name: str, slug: str, email: str) -> dict:
    response = manager.post("/api/manager/tenants", headers=csrf(manager), json={
        "display_name": name,
        "slug": slug,
        "owner_name": f"{name} Owner",
        "owner_email": email,
        "timezone": "Europe/London",
    })
    assert response.status_code == 201, response.text
    return response.json()


def accept(tenant: dict, full_name: str) -> TestClient:
    token = parse_qs(urlparse(tenant["invitation"]["setup_url"]).query)["invite"][0]
    client = TestClient(app)
    response = client.post(f"/api/invitations/{token}/accept", json={
        "full_name": full_name,
        "password": "A-very-strong-Test-Password-2026!",
    })
    assert response.status_code == 200, response.text
    return client


def test_manager_mfa_and_cross_tenant_isolation():
    with TestClient(app) as manager:
        login = manager.post("/api/auth/login", json={
            "email": "manager@example.com", "password": "TestPlatformPassword!2026"
        })
        assert login.status_code == 200
        assert login.json()["requires_two_factor_setup"] is True

        setup = manager.post("/api/auth/2fa/setup", headers=csrf(manager))
        assert setup.status_code == 200
        secret = setup.json()["secret"]
        enabled = manager.post("/api/auth/2fa/enable", headers=csrf(manager), json={
            "code": pyotp.TOTP(secret).now()
        })
        assert enabled.status_code == 200
        assert len(enabled.json()["recovery_codes"]) == 8
        recovery_code = enabled.json()["recovery_codes"][0]
        with TestClient(app) as recovery_login:
            recovered = recovery_login.post("/api/auth/login", json={
                "email": "manager@example.com",
                "password": "TestPlatformPassword!2026",
                "code": recovery_code,
            })
            assert recovered.status_code == 200
            reused = recovery_login.post("/api/auth/login", json={
                "email": "manager@example.com",
                "password": "TestPlatformPassword!2026",
                "code": recovery_code,
            })
            assert reused.status_code == 401

        alpha = make_tenant(manager, "Alpha Weddings", "alpha-weddings", "alpha@example.com")
        beta = make_tenant(manager, "Beta Films", "beta-films", "beta@example.com")
        assert alpha["automations_paused"] is True
        assert beta["automations_paused"] is True
        assert alpha["client_url"].endswith("/alpha-weddings")

        alpha_client = accept(alpha, "Alex Alpha")
        beta_client = accept(beta, "Ben Beta")

        created = alpha_client.post("/api/studio/clients", headers=csrf(alpha_client), json={
            "first_name": "Chris", "last_name": "Client", "partner_name": "Sam",
            "email": "couple@example.com", "phone": "07000000000"
        })
        assert created.status_code == 201, created.text
        alpha_client_id = created.json()["id"]

        own = alpha_client.get(f"/api/studio/clients/{alpha_client_id}")
        assert own.status_code == 200
        hidden = beta_client.get(f"/api/studio/clients/{alpha_client_id}")
        assert hidden.status_code == 404

        cross_booking = beta_client.post("/api/studio/bookings", headers=csrf(beta_client), json={
            "client_id": alpha_client_id, "title": "Must never cross", "event_date": "2027-06-12"
        })
        assert cross_booking.status_code == 404

        alpha_list = alpha_client.get("/api/studio/clients").json()
        beta_list = beta_client.get("/api/studio/clients").json()
        assert len(alpha_list) == 1
        assert beta_list == []

        public = manager.get("/api/public/business/alpha-weddings")
        assert public.status_code == 200
        assert set(public.json()) == {
            "slug", "display_name", "accent_colour", "welcome_message", "portal_status"
        }
        assert "owner_email" not in public.text
        assert "storage_key" not in public.text


def test_frontend_assets_do_not_reference_live_wbm():
    from pathlib import Path

    root = Path(__file__).parents[1]
    text = "\n".join(
        path.read_text(errors="ignore")
        for folder in (root / "manager", root / "studio", root / "client")
        for path in folder.iterdir() if path.is_file()
    ).lower()
    assert "weddings by mark" not in text
    assert "booking.weddingsbymark" not in text


def test_login_throttle_locks_repeated_bad_attempts():
    with TestClient(app) as client:
        for _ in range(5):
            response = client.post("/api/auth/login", json={
                "email": "unknown@example.com", "password": "DefinitelyWrong!123"
            })
            assert response.status_code == 401
        locked = client.post("/api/auth/login", json={
            "email": "unknown@example.com", "password": "DefinitelyWrong!123"
        })
        assert locked.status_code == 429
