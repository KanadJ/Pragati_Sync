import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

import main


class DocumentComplianceTests(unittest.TestCase):
    def setUp(self):
        self.database_directory = tempfile.TemporaryDirectory()
        self.original_database_path = main.DATABASE_PATH
        self.original_settings_path = main.COMPLIANCE_SETTINGS_PATH
        main.DATABASE_PATH = Path(self.database_directory.name) / "test.db"
        main.COMPLIANCE_SETTINGS_PATH = Path(self.database_directory.name) / "compliance.json"
        main.COMPLIANCE_SETTINGS_PATH.write_text(json.dumps({
            "notice": "Test-only settings",
            "expiry_warning_days": 30,
            "required_document_types": [],
            "escalation_thresholds_days": [3, 7],
        }), encoding="utf-8")
        with main.SESSION_LOCK:
            main.SESSIONS.clear()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.add_application()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        with main.SESSION_LOCK:
            main.SESSIONS.clear()
        main.DATABASE_PATH = self.original_database_path
        main.COMPLIANCE_SETTINGS_PATH = self.original_settings_path
        self.database_directory.cleanup()

    def login(self, username):
        passwords = {
            "admin": "Admin@26262",
            "officer": "Officer@26262",
            "viewer": "Viewer@26262",
        }
        response = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": passwords[username]},
        )
        self.assertEqual(response.status_code, 200)
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    def add_application(self, application_id="COMPLIANCE-1", **attributes):
        application = {
            "id": application_id,
            "institution": "Compliance Test Institute",
            "region": "North",
            "officer": "Test Officer",
            "date": date.today().isoformat(),
            "stage": "Application Review",
            "status": "Pending",
            "workflow_route": None,
            "history": [],
            **attributes,
        }
        with main.connect_db() as connection:
            connection.execute(
                "INSERT INTO applications (id, data) VALUES (?, ?)",
                (application_id, json.dumps(application)),
            )
        return application

    def create_document(self, headers, **overrides):
        payload = {
            "document_type": "Lease",
            "name": "Registered lease",
            "status": "Pending",
            "expiry_date": None,
            "reviewer_remarks": None,
            **overrides,
        }
        return self.client.post(
            "/api/applications/COMPLIANCE-1/documents",
            headers=headers,
            json=payload,
        )

    def test_document_read_requires_authentication_and_viewer_cannot_mutate(self):
        self.assertEqual(
            self.client.get("/api/applications/COMPLIANCE-1/documents").status_code,
            401,
        )
        self.assertEqual(self.client.get("/api/notifications").status_code, 401)
        self.assertEqual(
            self.client.get("/api/applications/COMPLIANCE-1/compliance-summary").status_code,
            401,
        )
        viewer = self.login("viewer")
        self.assertEqual(
            self.client.get(
                "/api/applications/COMPLIANCE-1/documents",
                headers=viewer,
            ).status_code,
            200,
        )
        self.assertEqual(self.create_document(viewer).status_code, 403)

    def test_officer_can_create_and_update_document_with_validation(self):
        officer = self.login("officer")
        invalid_rejection = self.create_document(
            officer,
            status="Rejected",
            reviewer_remarks=" ",
        )
        self.assertEqual(invalid_rejection.status_code, 400)

        created = self.create_document(officer, status="Submitted")
        self.assertEqual(created.status_code, 201)
        document = created.json()["document"]
        self.assertEqual(document["status"], "Submitted")
        summary = self.client.get(
            "/api/applications/COMPLIANCE-1/compliance-summary",
            headers=officer,
        ).json()
        self.assertEqual(summary["state"], "Pending Verification")

        updated = self.client.patch(
            f"/api/applications/COMPLIANCE-1/documents/{document['id']}",
            headers=officer,
            json={
                "status": "Verified",
                "expiry_date": (date.today() + timedelta(days=10)).isoformat(),
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(
            self.client.get(
                "/api/applications/COMPLIANCE-1/compliance-summary",
                headers=officer,
            ).json()["state"],
            "Expiring Soon",
        )

    def test_compliance_states_are_not_treated_as_compliant_without_verification(self):
        settings = main.get_compliance_settings()
        today = date.today()

        def state(status, expiry=None):
            return main.calculate_compliance_summary(
                [{"document_type": "Lease", "status": status, "expiry_date": expiry}],
                settings,
                today,
            )["state"]

        self.assertEqual(main.calculate_compliance_summary([], settings, today)["state"], "Missing")
        settings["required_document_types"] = ["Lease", "Floor plan"]
        self.assertEqual(
            main.calculate_compliance_summary(
                [{"document_type": "Lease", "status": "Verified", "expiry_date": None}],
                settings,
                today,
            )["state"],
            "Missing",
        )
        settings["required_document_types"] = []
        self.assertEqual(state("Pending"), "Pending Verification")
        self.assertEqual(state("Submitted"), "Pending Verification")
        self.assertEqual(state("Rejected"), "Rejected")
        self.assertEqual(state("Verified", (today - timedelta(days=1)).isoformat()), "Expired")
        self.assertEqual(state("Verified", (today + timedelta(days=10)).isoformat()), "Expiring Soon")
        self.assertEqual(state("Verified"), "Compliant")

    def test_rejection_notification_is_deduplicated_audited_and_readable_by_officer(self):
        officer = self.login("officer")
        created = self.create_document(
            officer,
            status="Rejected",
            reviewer_remarks="Lease signature is missing.",
        )
        self.assertEqual(created.status_code, 201)
        notifications = self.client.get(
            "/api/notifications",
            headers=officer,
        ).json()["notifications"]
        self.assertEqual(len(notifications), 1)
        notification = notifications[0]
        self.assertEqual(notification["category"], "document_rejected")
        self.assertFalse(notification["is_read"])

        admin = self.login("admin")
        self.assertEqual(self.client.post("/api/compliance/check", headers=admin).status_code, 200)
        self.assertEqual(
            len(self.client.get("/api/notifications", headers=officer).json()["notifications"]),
            1,
        )
        self.assertEqual(
            self.client.post(
                f"/api/notifications/{notification['id']}/read",
                headers=self.login("viewer"),
            ).status_code,
            403,
        )
        marked = self.client.post(
            f"/api/notifications/{notification['id']}/read",
            headers=officer,
        )
        self.assertEqual(marked.status_code, 200)
        self.assertEqual(marked.json()["notification"]["is_read"], 1)

        with main.connect_db() as connection:
            actions = [
                row["action"] for row in connection.execute(
                    "SELECT action FROM audit_events WHERE application_id = 'COMPLIANCE-1'"
                ).fetchall()
            ]
        self.assertIn("Document created", actions)
        self.assertIn("Notification created", actions)
        self.assertIn("Notification marked read", actions)

    def test_marking_notification_viewed_hides_it_for_that_user_after_refetch(self):
        officer = self.login("officer")
        created = self.create_document(
            officer,
            status="Rejected",
            reviewer_remarks="Lease signature is missing.",
        )
        self.assertEqual(created.status_code, 201)
        notification = self.client.get(
            "/api/notifications",
            headers=officer,
        ).json()["notifications"][0]

        marked = self.client.post(
            f"/api/notifications/{notification['id']}/read",
            headers=officer,
        )
        self.assertEqual(marked.status_code, 200)
        self.assertEqual(marked.json()["notification"]["is_read"], 1)
        self.assertEqual(
            self.client.get("/api/notifications", headers=officer).json()["notifications"],
            [],
        )

        with main.connect_db() as connection:
            stored = connection.execute(
                "SELECT id FROM notifications WHERE id = ?",
                (notification["id"],),
            ).fetchone()
        self.assertIsNotNone(stored)

        viewer = self.login("viewer")
        other_user_notifications = self.client.get(
            "/api/notifications",
            headers=viewer,
        ).json()["notifications"]
        self.assertEqual([item["id"] for item in other_user_notifications], [notification["id"]])

        with main.connect_db() as connection:
            connection.execute(
                "UPDATE notifications SET resolved_at = ? WHERE id = ?",
                (main.now_iso(), notification["id"]),
            )
            dedupe_key = connection.execute(
                "SELECT dedupe_key FROM notifications WHERE id = ?",
                (notification["id"],),
            ).fetchone()["dedupe_key"]
        self.assertTrue(main.notification_event(
            "COMPLIANCE-1",
            notification["category"],
            notification["message"],
            dedupe_key,
            "officer",
        ))
        self.assertEqual(
            len(self.client.get("/api/notifications", headers=officer).json()["notifications"]),
            1,
        )

    def test_expiry_notifications_escalate_at_configured_threshold_once(self):
        officer = self.login("officer")
        expiry = (date.today() - timedelta(days=1)).isoformat()
        self.assertEqual(
            self.create_document(
                officer,
                status="Verified",
                expiry_date=expiry,
            ).status_code,
            201,
        )
        notification = self.client.get(
            "/api/notifications",
            headers=officer,
        ).json()["notifications"][0]
        self.assertEqual(notification["category"], "document_expired")

        created_at = (date.today() - timedelta(days=4)).isoformat() + "T12:00:00+00:00"
        with main.connect_db() as connection:
            connection.execute(
                "UPDATE notifications SET created_at = ? WHERE id = ?",
                (created_at, notification["id"]),
            )

        first_check = main.run_compliance_check(as_of=date.today())
        self.assertEqual(first_check["escalated"], 1)
        second_check = main.run_compliance_check(as_of=date.today())
        self.assertEqual(second_check["escalated"], 0)
        with main.connect_db() as connection:
            level = connection.execute(
                "SELECT escalation_level FROM notifications WHERE id = ?",
                (notification["id"],),
            ).fetchone()["escalation_level"]
        self.assertEqual(level, 1)

    def test_admin_only_triggers_compliance_check_and_overdue_notice_is_deduplicated(self):
        application = self.add_application(
            "OVERDUE-1",
            due_date=(date.today() - timedelta(days=1)).isoformat(),
        )
        viewer = self.login("viewer")
        self.assertEqual(self.client.post("/api/compliance/check", headers=viewer).status_code, 403)
        admin = self.login("admin")
        first = self.client.post("/api/compliance/check", headers=admin)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["created"], 1)
        second = self.client.post("/api/compliance/check", headers=admin)
        self.assertEqual(second.json()["created"], 0)
        notifications = self.client.get("/api/notifications", headers=admin).json()["notifications"]
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["application_id"], application["id"])
        self.assertEqual(notifications[0]["category"], "application_overdue")


if __name__ == "__main__":
    unittest.main()
