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
        beta_steps = beta_client.get("/api/studio/workflows").json()[0]["steps"]
        assert len(beta_steps) == 11
        assert all(item["name"] != "Check they received the quote" for item in beta_steps)
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
        protected_question = enquiry_form.json()["questions"][0]
        protected_delete = alpha_client.delete(
            f"/api/studio/enquiry-form/questions/{protected_question['id']}",
            headers=csrf(alpha_client),
        )
        assert protected_delete.status_code == 409
        custom_question = alpha_client.post(
            "/api/studio/enquiry-form/questions", headers=csrf(alpha_client), json={
                "label": "How did you hear about us?", "help_text": "Choose the closest answer",
                "question_type": "single_choice", "is_required": True, "is_active": True,
                "options": ["Google", "Friend", "Wedding venue"], "sort_order": 20,
            },
        )
        assert custom_question.status_code == 201, custom_question.text
        custom_question_id = custom_question.json()["id"]
        cross_question = beta_client.patch(
            f"/api/studio/enquiry-form/questions/{custom_question_id}",
            headers=csrf(beta_client), json={
                "label": "Should never change", "question_type": "short_text",
            },
        )
        assert cross_question.status_code == 404
        public_form = manager.get("/api/public/business/alpha-weddings/enquiry-form")
        assert public_form.status_code == 200
        assert public_form.json()["packages"][0]["name"] == "Story Collection"
        assert any(item["question_type"] == "venue" for item in public_form.json()["questions"])
        submitted = manager.post("/api/public/business/alpha-weddings/enquiries", json={
            "first_name": "Taylor", "partner_name": "Jordan", "email": "taylor@example.com",
            "phone": "07000111222", "event_date": "2027-08-14", "venue": "Test Hall",
            "package_interest": "Story Collection", "message": "We love relaxed photographs.",
            "website": "", "answers": {custom_question_id: "Google"},
        })
        assert submitted.status_code == 201, submitted.text
        assert submitted.json()["automatic_reply"] == "paused"
        enquiries = alpha_client.get("/api/studio/enquiries").json()
        assert len(enquiries) == 1
        assert enquiries[0]["answers"] == [{"label": "How did you hear about us?", "answer": "Google"}]
        assert beta_client.get("/api/studio/enquiries").json() == []

        converted = alpha_client.post(
            f"/api/studio/enquiries/{enquiries[0]['id']}/convert",
            headers=csrf(alpha_client), json={"title": "Taylor & Jordan"},
        )
        assert converted.status_code == 201, converted.text
        booking_id = converted.json()["id"]
        assert converted.json()["status"] == "quote_preparation"
        assert beta_client.get(f"/api/studio/bookings/{booking_id}/journey").status_code == 404

        mode = alpha_client.put(
            f"/api/studio/workflow-steps/{step.json()['id']}/mode",
            headers=csrf(alpha_client), json={"mode": "review", "apply_to_existing": False},
        )
        assert mode.status_code == 200, mode.text
        quote = alpha_client.put(
            f"/api/studio/bookings/{booking_id}/quote", headers=csrf(alpha_client), json={
                "package_ids": [package.json()["id"]], "add_on_ids": [optional.json()["id"]],
                "custom_items": [{"label": "Travel", "price_pence": 2500}],
                "message": "Choose the collection that feels right.",
            },
        )
        assert quote.status_code == 200, quote.text
        sent_quote = alpha_client.post(
            f"/api/studio/bookings/{booking_id}/quote/send", headers=csrf(alpha_client),
        )
        assert sent_quote.status_code == 200, sent_quote.text
        assert sent_quote.json()["automatic_email_sent"] is False
        portal_token = sent_quote.json()["portal_url"].rsplit("/", 1)[-1]
        portal = manager.get(f"/api/public/portal/{portal_token}")
        assert portal.status_code == 200, portal.text
        assert portal.json()["quote"]["status"] == "sent"
        accepted_quote = manager.post(f"/api/public/portal/{portal_token}/quote/accept", json={
            "package_id": package.json()["id"], "add_on_ids": [optional.json()["id"]],
            "client_name": "Taylor Client",
        })
        assert accepted_quote.status_code == 200, accepted_quote.text
        invoice = accepted_quote.json()["invoice"]
        assert invoice["number"] == "INV-00001"
        assert invoice["total_pence"] == 149500 + 2500
        assert manager.post(f"/api/public/portal/{portal_token}/quote/accept", json={
            "package_id": package.json()["id"], "client_name": "Taylor Client",
        }).status_code == 409

        payment = alpha_client.post(
            f"/api/studio/invoices/{invoice['id']}/payments", headers=csrf(alpha_client), json={
                "amount_pence": 10000, "paid_date": "2026-09-18",
                "payment_type": "bank_transfer", "reference": "TEST-DEP",
            },
        )
        assert payment.status_code == 201, payment.text
        assert payment.json()["status"] == "part_paid"
        journey = alpha_client.get(f"/api/studio/bookings/{booking_id}/journey").json()
        assert journey["status"] == "confirmed"
        assert journey["calendar"]["status"] == "pending"

        amendment = alpha_client.post(
            f"/api/studio/bookings/{booking_id}/quote/amend", headers=csrf(alpha_client), json={
                "label": "Complimentary wedding album", "price_pence": 0,
                "reason": "Promotional album agreed with the couple",
            },
        )
        assert amendment.status_code == 201, amendment.text
        assert amendment.json()["quote_revisions"][0]["reason"].startswith("Promotional")
        remaining = amendment.json()["invoices"][0]["outstanding_pence"]
        paid_in_full = alpha_client.post(
            f"/api/studio/invoices/{invoice['id']}/payments", headers=csrf(alpha_client), json={
                "amount_pence": remaining, "paid_date": "2026-09-19",
                "payment_type": "bank_transfer", "reference": "TEST-BAL",
            },
        )
        assert paid_in_full.status_code == 201, paid_in_full.text
        assert paid_in_full.json()["status"] == "paid"
        invoice_pdf = alpha_client.get(f"/api/studio/invoices/{invoice['id']}/pdf")
        assert invoice_pdf.status_code == 200
        assert invoice_pdf.headers["content-type"] == "application/pdf"
        assert invoice_pdf.content.startswith(b"%PDF")
        locked_amendment = alpha_client.post(
            f"/api/studio/bookings/{booking_id}/quote/amend", headers=csrf(alpha_client), json={
                "label": "Must not be added", "price_pence": 0, "reason": "Testing the lock",
            },
        )
        assert locked_amendment.status_code == 409

        contract_template = alpha_client.post(
            "/api/studio/contract-templates", headers=csrf(alpha_client), json={
                "name": "Wedding photography agreement",
                "body": "This agreement records the service, payment terms and responsibilities for the wedding.",
                "is_active": True,
            },
        )
        assert contract_template.status_code == 201, contract_template.text
        issued = alpha_client.post(
            f"/api/studio/bookings/{booking_id}/contract", headers=csrf(alpha_client),
            json={"template_id": contract_template.json()["id"]},
        )
        assert issued.status_code == 200, issued.text
        signed = manager.post(f"/api/public/portal/{portal_token}/contract/sign", json={
            "full_name": "Taylor Client", "agreed": True,
        })
        assert signed.status_code == 200, signed.text
        assert signed.json()["client_signed_at"]
        countersigned = alpha_client.post(
            f"/api/studio/bookings/{booking_id}/contract/countersign",
            headers=csrf(alpha_client), json={"full_name": "Alex Alpha", "agreed": True},
        )
        assert countersigned.status_code == 200, countersigned.text
        contract_pdf = manager.get(f"/api/public/portal/{portal_token}/contract/pdf")
        assert contract_pdf.status_code == 200
        assert contract_pdf.content.startswith(b"%PDF")

        form_template = alpha_client.put(
            "/api/studio/questionnaire-templates/booking", headers=csrf(alpha_client), json={
                "form_type": "booking", "name": "Booking details",
                "introduction": "Tell us the essentials.", "is_active": True,
                "questions": [{"id": "ceremony_time", "label": "Ceremony time", "type": "time", "required": True}],
            },
        )
        assert form_template.status_code == 200, form_template.text
        submitted_form = manager.post(
            f"/api/public/portal/{portal_token}/questionnaires/booking",
            json={"answers": {"ceremony_time": "13:30"}},
        )
        assert submitted_form.status_code == 200, submitted_form.text
        questionnaire_pdf = alpha_client.get(
            f"/api/studio/bookings/{booking_id}/questionnaires/booking/pdf"
        )
        assert questionnaire_pdf.status_code == 200
        assert questionnaire_pdf.content.startswith(b"%PDF")

        blocked = alpha_client.post("/api/studio/date-blocks", headers=csrf(alpha_client), json={
            "start_date": "2027-01-02", "end_date": "2027-01-04",
            "label": "Family holiday", "notes": "Not taking bookings",
        })
        assert blocked.status_code == 201, blocked.text
        assert blocked.json()["calendar"]["status"] == "pending"
        assert manager.get(
            "/api/public/business/alpha-weddings/availability/2027-01-03"
        ).json()["available"] is False
        assert manager.get(
            "/api/public/business/alpha-weddings/availability/2027-01-10"
        ).json()["available"] is True
        assert beta_client.delete(
            f"/api/studio/date-blocks/{blocked.json()['id']}", headers=csrf(beta_client),
        ).status_code == 404
        assert alpha_client.post(
            f"/api/studio/bookings/{booking_id}/complete", headers=csrf(alpha_client),
            json={"completed": True},
        ).json()["status"] == "completed"

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
