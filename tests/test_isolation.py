from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.main import app, ensure_public_mail_host


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

        package = alpha_client.post("/api/studio/packages", headers=csrf(alpha_client), json={
            "name": "Story Collection", "short_description": "A full wedding story",
            "price_pence": 149500, "booking_fee_pence": 10000,
            "balance_due_days": 45, "inclusions": ["Photography", "Online gallery"],
            "is_featured": True, "is_active": True, "sort_order": 0,
        })
        assert package.status_code == 201, package.text
        assert len(alpha_client.get("/api/studio/packages").json()) == 1
        assert beta_client.get("/api/studio/packages").json() == []
        cross_package = beta_client.patch(
            f"/api/studio/packages/{package.json()['id']}", headers=csrf(beta_client),
            json={"name": "Changed", "price_pence": 1000, "booking_fee_pence": 1000},
        )
        assert cross_package.status_code == 404
        excessive_fee = alpha_client.post("/api/studio/packages", headers=csrf(alpha_client), json={
            "name": "Invalid package", "price_pence": 5000, "booking_fee_pence": 10000,
        })
        assert excessive_fee.status_code == 422

        mandatory_without_reason = alpha_client.post(
            "/api/studio/add-ons", headers=csrf(alpha_client),
            json={"name": "Travel", "price_pence": 5000, "selection_mode": "mandatory"},
        )
        assert mandatory_without_reason.status_code == 422
        optional = alpha_client.post("/api/studio/add-ons", headers=csrf(alpha_client), json={
            "name": "Complimentary album", "price_pence": 0, "selection_mode": "optional",
        })
        assert optional.status_code == 201
        assert beta_client.get("/api/studio/add-ons").json() == []

        workflow = alpha_client.get("/api/studio/workflows").json()[0]
        step = alpha_client.post(
            f"/api/studio/workflows/{workflow['id']}/steps", headers=csrf(alpha_client), json={
                "name": "Check they received the quote", "trigger_event": "quote_sent",
                "timing_direction": "after", "offset_value": 1, "offset_unit": "days",
                "action_type": "email", "subject": "Just checking in",
                "message_body": "I wanted to make sure your quote arrived.",
                "is_paused": False,
            },
        )
        assert step.status_code == 201, step.text
        assert step.json()["is_paused"] is True
        assert beta_client.get("/api/studio/workflows").json()[0]["steps"] == []
        dashboard = alpha_client.get("/api/studio/dashboard").json()
        assert dashboard["tenant"]["automations_paused"] is True
        assert dashboard["onboarding"]["packages"] is True
        assert dashboard["onboarding"]["templates"] is True

        enquiry_form = alpha_client.put("/api/studio/enquiry-form", headers=csrf(alpha_client), json={
            "heading": "Tell us about your wedding", "introduction": "We would love to hear your plans.",
            "submit_label": "Send my enquiry", "success_message": "Thank you - it arrived safely.",
            "ask_partner_name": True, "ask_phone": True, "ask_venue": True,
            "ask_package_interest": True, "ask_message": True, "is_published": True,
        })
        assert enquiry_form.status_code == 200, enquiry_form.text
        public_form = manager.get("/api/public/business/alpha-weddings/enquiry-form")
        assert public_form.status_code == 200
        assert public_form.json()["packages"][0]["name"] == "Story Collection"
        submitted = manager.post("/api/public/business/alpha-weddings/enquiries", json={
            "first_name": "Taylor", "partner_name": "Jordan", "email": "taylor@example.com",
            "phone": "07000111222", "event_date": "2027-08-14", "venue": "Test Hall",
            "package_interest": "Story Collection", "message": "We love relaxed photographs.",
            "website": "",
        })
        assert submitted.status_code == 201, submitted.text
        assert submitted.json()["automatic_reply"] == "paused"
        assert len(alpha_client.get("/api/studio/enquiries").json()) == 1
        assert beta_client.get("/api/studio/enquiries").json() == []

        mailbox = alpha_client.put("/api/studio/mailbox", headers=csrf(alpha_client), json={
            "from_name": "Alpha Weddings", "email_address": "hello@alpha.example",
            "smtp_host": "smtp.alpha.example", "smtp_port": 465, "smtp_security": "ssl",
            "smtp_username": "hello@alpha.example", "smtp_password": "smtp-secret-password",
            "imap_host": "imap.alpha.example", "imap_port": 993, "imap_security": "ssl",
            "imap_username": "hello@alpha.example", "imap_password": "imap-secret-password",
        })
        assert mailbox.status_code == 200, mailbox.text
        assert mailbox.json()["smtp_has_password"] is True
        assert "smtp-secret-password" not in mailbox.text
        assert "imap-secret-password" not in mailbox.text
        assert beta_client.get("/api/studio/mailbox").json()["configured"] is False

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


def test_mail_connection_rejects_private_hosts():
    with pytest.raises(ValueError):
        ensure_public_mail_host("localhost", 465, {465, 587})
    with pytest.raises(ValueError):
        ensure_public_mail_host("127.0.0.1", 993, {143, 993})
