from urllib.parse import parse_qs, urlparse
from datetime import datetime, timezone

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.main import app, ensure_compatibility_columns, ensure_public_mail_host
from app.worker import process_billing_statuses


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


def test_phase_five_three_additive_column_upgrade():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE service_packages (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE package_add_ons (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE enquiries (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE bookings (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE tenant_calendar_connections (tenant_id VARCHAR(36) PRIMARY KEY)"))
    with Session(engine) as session:
        ensure_compatibility_columns(session)
        package_columns = {row[1] for row in session.execute(text("PRAGMA table_info(service_packages)"))}
        add_on_columns = {row[1] for row in session.execute(text("PRAGMA table_info(package_add_ons)"))}
        enquiry_columns = {row[1] for row in session.execute(text("PRAGMA table_info(enquiries)"))}
        booking_columns = {row[1] for row in session.execute(text("PRAGMA table_info(bookings)"))}
        calendar_columns = {row[1] for row in session.execute(text("PRAGMA table_info(tenant_calendar_connections)"))}
    assert "information_url" in package_columns
    assert "information_url" in add_on_columns
    assert "venue_details" in enquiry_columns
    assert "venue_details" in booking_columns
    assert "last_synced_at" in calendar_columns


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

        billing_centre = manager.get("/api/manager/billing")
        assert billing_centre.status_code == 200
        assert {row["tenant"]["slug"] for row in billing_centre.json()["accounts"]} == {
            "alpha-weddings", "beta-films"
        }
        extended = manager.post(f"/api/manager/tenants/{alpha['id']}/trial", headers=csrf(manager), json={
            "days": 120, "note": "Extended for a full platform trial"
        })
        assert extended.status_code == 200, extended.text
        assert extended.json()["subscription"]["trial_days_granted"] == 120
        assert extended.json()["tenant"]["automations_paused"] is True
        plan = manager.put(f"/api/manager/tenants/{alpha['id']}/billing", headers=csrf(manager), json={
            "plan_name": "Ivory Pro", "price_pence": 4900, "billing_cycle": "monthly",
            "next_payment_due": "2026-10-18", "grace_days": 7, "auto_suspend": True,
        })
        assert plan.status_code == 200, plan.text
        assert plan.json()["price_pence"] == 4900
        paid = manager.post(f"/api/manager/tenants/{alpha['id']}/billing/payments", headers=csrf(manager), json={
            "amount_pence": 4900, "paid_date": "2026-09-18", "payment_method": "bank_transfer",
            "reference": "TEST-ALPHA-001", "notes": "Test subscription payment",
            "covers_until": "2026-10-18", "reactivate": True,
        })
        assert paid.status_code == 201, paid.text
        alpha_billing = manager.get(f"/api/manager/tenants/{alpha['id']}/billing").json()
        assert alpha_billing["tenant"]["status"] == "active"
        assert alpha_billing["tenant"]["automations_paused"] is True
        assert alpha_billing["payments"][0]["reference"] == "TEST-ALPHA-001"

        suspended = manager.post(f"/api/manager/tenants/{beta['id']}/billing/suspend", headers=csrf(manager), json={
            "reason": "Subscription payment overdue", "next_payment_due": "2026-09-10"
        })
        assert suspended.status_code == 200, suspended.text
        assert beta_client.get("/api/studio/dashboard").status_code == 403
        reactivated = manager.post(f"/api/manager/tenants/{beta['id']}/billing/reactivate", headers=csrf(manager), json={
            "reason": "Payment arrangement agreed", "next_payment_due": "2026-10-18"
        })
        assert reactivated.status_code == 200, reactivated.text
        assert reactivated.json()["tenant"]["automations_paused"] is True
        assert beta_client.get("/api/studio/dashboard").status_code == 200

        help_library = manager.get("/api/manager/help/articles")
        assert help_library.status_code == 200
        assert len(help_library.json()) >= 15
        help_home = alpha_client.get("/api/studio/help/articles?context=workflow")
        assert help_home.status_code == 200
        assert help_home.json()["suggestions"]
        assert "outside AI" in help_home.json()["privacy"]
        no_csrf_help = alpha_client.post("/api/studio/help/ask", json={
            "question": "How do I pause one follow-up?", "context": "weddings",
        })
        assert no_csrf_help.status_code == 403
        help_answer = alpha_client.post("/api/studio/help/ask", headers=csrf(alpha_client), json={
            "question": "How do I pause only the first follow-up for one couple?",
            "context": "weddings",
        })
        assert help_answer.status_code == 200, help_answer.text
        assert help_answer.json()["matched"] is True
        assert help_answer.json()["article"]["action_route"] == "weddings"
        draft_help = manager.post("/api/manager/help/articles", headers=csrf(manager), json={
            "slug": "test-private-answer", "title": "A private draft answer",
            "category": "Testing", "summary": "Manager-only until published.",
            "body": "This answer is deliberately long enough to pass validation.",
            "keywords": ["private draft"], "contexts": ["home"],
            "is_published": False, "sort_order": 9000,
        })
        assert draft_help.status_code == 201, draft_help.text
        public_help_ids = {row["id"] for row in alpha_client.get(
            "/api/studio/help/articles?context=home").json()["articles"]}
        assert draft_help.json()["id"] not in public_help_ids

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
            "information_url": "https://alpha.example/story-collection",
            "price_pence": 149500, "booking_fee_pence": 10000,
            "balance_due_days": 45, "inclusions": ["Photography", "Online gallery"],
            "is_featured": True, "is_active": True, "sort_order": 0,
        })
        assert package.status_code == 201, package.text
        assert package.json()["information_url"] == "https://alpha.example/story-collection"
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
            "information_url": "https://alpha.example/wedding-albums",
        })
        assert optional.status_code == 201
        assert optional.json()["information_url"] == "https://alpha.example/wedding-albums"
        assert beta_client.get("/api/studio/add-ons").json() == []

        forms = alpha_client.get("/api/studio/questionnaire-templates")
        assert forms.status_code == 200, forms.text
        forms_by_type = {row["form_type"]: row for row in forms.json()}
        assert set(forms_by_type) == {"booking", "final_timings"}
        assert len(forms_by_type["booking"]["questions"]) >= 20
        assert len(forms_by_type["final_timings"]["questions"]) >= 25
        assert {row["section_title"] for row in forms_by_type["final_timings"]["questions"]} >= {
            "Ceremony and reception", "Preparations and travel", "Your running order",
        }

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
        enquiry_qr = alpha_client.get("/api/studio/enquiry-form/qr")
        assert enquiry_qr.status_code == 200
        assert enquiry_qr.headers["content-type"] == "image/svg+xml"
        assert b"<svg" in enquiry_qr.content
        assert beta_client.get("/api/studio/enquiry-form/qr").status_code == 409
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
        assert public_form.json()["google_places"]["manual_entry_available"] is True
        submitted = manager.post("/api/public/business/alpha-weddings/enquiries", json={
            "first_name": "Taylor", "partner_name": "Jordan", "email": "taylor@example.com",
            "phone": "07000111222", "event_date": "2027-08-14", "venue": "Test Hall",
            "venue_details": {"place_id": "test-place-alpha", "name": "Test Hall",
                              "formatted_address": "Test Hall, Alpha Road, Manchester",
                              "latitude": 53.4808, "longitude": -2.2426},
            "package_interest": "Story Collection", "message": "We love relaxed photographs.",
            "website": "", "answers": {custom_question_id: "Google"},
        })
        assert submitted.status_code == 201, submitted.text
        assert submitted.json()["automatic_reply"] == "paused"
        enquiries = alpha_client.get("/api/studio/enquiries").json()
        assert len(enquiries) == 1
        assert enquiries[0]["venue_details"]["place_id"] == "test-place-alpha"
        assert "destination_place_id=test-place-alpha" in enquiries[0]["venue_maps_url"]
        assert enquiries[0]["answers"] == [{"label": "How did you hear about us?", "answer": "Google"}]
        assert beta_client.get("/api/studio/enquiries").json() == []

        converted = alpha_client.post(
            f"/api/studio/enquiries/{enquiries[0]['id']}/convert",
            headers=csrf(alpha_client), json={"title": "Taylor & Jordan"},
        )
        assert converted.status_code == 201, converted.text
        booking_id = converted.json()["id"]
        assert converted.json()["status"] == "quote_preparation"
        assert converted.json()["venue_details"]["formatted_address"].startswith("Test Hall")
        assert "destination_place_id=test-place-alpha" in converted.json()["venue_maps_url"]
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
        assert portal.json()["quote"]["packages"][0]["information_url"] == "https://alpha.example/story-collection"
        assert portal.json()["quote"]["add_ons"][0]["information_url"] == "https://alpha.example/wedding-albums"
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
        restored_form = alpha_client.post(
            "/api/studio/questionnaire-templates/booking/starter", headers=csrf(alpha_client),
        )
        assert restored_form.status_code == 200, restored_form.text
        assert len(restored_form.json()["questions"]) >= 20
        restored_portal = manager.get(f"/api/public/portal/{portal_token}").json()
        booking_form = next(row for row in restored_portal["available_questionnaires"]
                            if row["form_type"] == "booking")
        assert booking_form["submitted"] is True
        assert booking_form["submission"]["answers"]["ceremony_time"] == "13:30"

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

        updated_booking = alpha_client.patch(
            f"/api/studio/bookings/{booking_id}", headers=csrf(alpha_client), json={
                "title": "Taylor & Jordan", "first_name": "Taylor", "last_name": "Client",
                "partner_name": "Jordan", "email": "taylor@example.com",
                "phone": "07000111222", "event_date": "2027-08-14", "venue": "New Test Hall",
            },
        )
        assert updated_booking.status_code == 200, updated_booking.text
        assert updated_booking.json()["venue"] == "New Test Hall"
        moved = alpha_client.post(
            f"/api/studio/bookings/{booking_id}/reschedule", headers=csrf(alpha_client), json={
                "event_date": "2027-08-15", "reason": "The venue moved the available date",
                "move_financial_dates": True,
            },
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["event_date"] == "2027-08-15"
        date_search = alpha_client.get("/api/studio/search?q=15%2F08%2F2027")
        assert date_search.status_code == 200, date_search.text
        assert any(row["id"] == booking_id for row in date_search.json()["results"])

        note = alpha_client.post(
            f"/api/studio/bookings/{booking_id}/notes", headers=csrf(alpha_client),
            json={"body": "Remember the quiet room for family photographs."},
        )
        assert note.status_code == 201, note.text
        assert beta_client.delete(
            f"/api/studio/bookings/{booking_id}/notes/{note.json()['id']}",
            headers=csrf(beta_client),
        ).status_code == 404
        task = alpha_client.post(
            f"/api/studio/tasks?booking_id={booking_id}", headers=csrf(alpha_client), json={
                "title": "Confirm the group photograph list", "notes": "Ask on Monday",
                "due_date": "2027-08-09",
            },
        )
        assert task.status_code == 201, task.text
        completed_task = alpha_client.patch(
            f"/api/studio/tasks/{task.json()['id']}", headers=csrf(alpha_client),
            json={"status": "completed"},
        )
        assert completed_task.status_code == 200
        assert completed_task.json()["completed_at"]
        document = alpha_client.post(
            f"/api/studio/bookings/{booking_id}/documents", headers=csrf(alpha_client),
            data={"description": "Venue running order"},
            files={"file": ("running-order.pdf", b"%PDF-test-document", "application/pdf")},
        )
        assert document.status_code == 201, document.text
        assert alpha_client.get(document.json()["download_url"]).content == b"%PDF-test-document"
        assert beta_client.get(document.json()["download_url"]).status_code == 404

        today = alpha_client.get("/api/studio/today")
        assert today.status_code == 200, today.text
        assert set(today.json()["counts"]) == {
            "new_enquiries", "review", "updates", "payments", "failed", "tasks"
        }
        search = alpha_client.get("/api/studio/search?q=Taylor")
        assert search.status_code == 200
        assert any(row["id"] == booking_id for row in search.json()["results"])
        assert beta_client.get("/api/studio/search?q=Taylor").json()["results"] == []

        copied_workflow = alpha_client.post(
            f"/api/studio/workflows/{workflow['id']}/duplicate", headers=csrf(alpha_client),
        )
        assert copied_workflow.status_code == 201, copied_workflow.text
        assert copied_workflow.json()["is_active"] is False
        assert copied_workflow.json()["steps"]
        assert all(row["mode"] == "off" for row in copied_workflow.json()["steps"])
        paused_workflow = alpha_client.post(
            "/api/studio/workflows", headers=csrf(alpha_client), json={
                "name": "Small weddings", "description": "A separate safe draft",
                "is_active": False, "sort_order": 4,
            },
        )
        assert paused_workflow.status_code == 201, paused_workflow.text
        paused_step = alpha_client.post(
            f"/api/studio/workflows/{paused_workflow.json()['id']}/steps",
            headers=csrf(alpha_client), json={
                "name": "Personal check-in", "trigger_event": "enquiry_received",
                "timing_direction": "after", "offset_value": 1, "offset_unit": "days",
                "action_type": "manual_task", "task_title": "Call the couple",
                "subject": "", "message_body": "", "is_paused": True, "sort_order": 0,
            },
        )
        assert paused_step.status_code == 201, paused_step.text
        paused_after_step = next(
            row for row in alpha_client.get("/api/studio/workflows").json()
            if row["id"] == paused_workflow.json()["id"]
        )
        assert paused_after_step["is_active"] is False
        assert paused_after_step["steps"][0]["mode"] == "off"

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

        template = alpha_client.post("/api/studio/email-templates", headers=csrf(alpha_client), json={
            "name": "Venue information", "subject": "A little venue information",
            "body": "Hi {{couple_first_name}}, here are the details.",
            "category": "Planning", "is_active": True,
        })
        assert template.status_code == 201, template.text
        assert len(alpha_client.get("/api/studio/email-templates").json()) == 1
        assert beta_client.get("/api/studio/email-templates").json() == []
        branding = alpha_client.put("/api/studio/email-branding", headers=csrf(alpha_client), json={
            "signoff": "All the best", "signature_name": "Alex Alpha",
            "signature_role": "Wedding photographer", "telephone": "07000000001",
            "website": "https://alpha.example", "show_logo": True, "show_badge": True,
            "owner_notifications_enabled": True,
        })
        assert branding.status_code == 200, branding.text
        assert branding.json()["owner_notifications_enabled"] is True
        assert beta_client.get("/api/studio/email-branding").json()["signature_name"] == "Beta Films"

        second_enquiry = manager.post("/api/public/business/alpha-weddings/enquiries", json={
            "first_name": "Morgan", "email": "morgan@example.com", "event_date": "2028-02-12",
            "venue": "Another Hall", "website": "", "answers": {custom_question_id: "Friend"},
        })
        assert second_enquiry.status_code == 201, second_enquiry.text
        second_id = next(row["id"] for row in alpha_client.get("/api/studio/enquiries").json()
                         if row["email"] == "morgan@example.com")
        assert alpha_client.post(
            f"/api/studio/enquiries/{second_id}/close", headers=csrf(alpha_client),
            json={"outcome": "no_reply", "note": "Closed during test"},
        ).json()["status"] == "closed"
        assert alpha_client.post(
            f"/api/studio/enquiries/{second_id}/reopen", headers=csrf(alpha_client),
        ).json()["status"] == "new"

        public = manager.get("/api/public/business/alpha-weddings")
        assert public.status_code == 200
        assert set(public.json()) == {
            "slug", "display_name", "accent_colour", "welcome_message", "portal_status"
        }
        assert "owner_email" not in public.text
        assert "storage_key" not in public.text

        overdue = manager.put(f"/api/manager/tenants/{beta['id']}/billing", headers=csrf(manager), json={
            "plan_name": "Ivory Studio", "price_pence": 3900, "billing_cycle": "monthly",
            "next_payment_due": "2026-09-01", "grace_days": 3, "auto_suspend": True,
        })
        assert overdue.status_code == 200, overdue.text
        process_billing_statuses(datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc))
        beta_billing = manager.get(f"/api/manager/tenants/{beta['id']}/billing").json()
        assert beta_billing["tenant"]["status"] == "suspended"
        assert beta_billing["subscription"]["billing_status"] == "suspended"
        assert beta_client.get("/api/studio/dashboard").status_code == 403


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
    client_nginx = (root / "client" / "nginx.conf").read_text()
    assert 'X-Frame-Options "DENY"' in client_nginx
    assert "frame-ancestors 'none'" in client_nginx
    assert "frame-ancestors https:" in client_nginx
    client_dockerfile = (root / "client" / "Dockerfile").read_text()
    assert "portal.css" in client_dockerfile
    assert "embed.js" in client_dockerfile


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
