import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import main


class WorkflowAutomationTests(unittest.TestCase):
    def setUp(self):
        self.database_directory = tempfile.TemporaryDirectory()
        self.original_database_path = main.DATABASE_PATH
        main.DATABASE_PATH = Path(self.database_directory.name) / "test.db"
        with main.SESSION_LOCK:
            main.SESSIONS.clear()

        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        with main.SESSION_LOCK:
            main.SESSIONS.clear()
        main.DATABASE_PATH = self.original_database_path
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
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def add_application(self, application_id, **attributes):
        application = {
            "id": application_id,
            "institution": "Test Institute",
            "region": "North",
            "officer": "Test Officer",
            "date": "2026-10-24",
            "stage": "Application Review",
            "status": "Pending",
            "reason": None,
            "history": [],
            **attributes,
        }
        with main.connect_db() as connection:
            connection.execute(
                "INSERT INTO applications (id, data) VALUES (?, ?)",
                (application_id, json.dumps(application)),
            )
        return application

    def audit_actions(self, application_id):
        with main.connect_db() as connection:
            return [
                row["action"]
                for row in connection.execute(
                    "SELECT action FROM audit_events WHERE application_id = ? ORDER BY id",
                    (application_id,),
                )
            ]

    def create_payload(self, application_id="TEST-CREATED", **attributes):
        return {
            "id": application_id,
            "institution": "New Test Institute",
            "region": "South",
            "officer": "New Officer",
            "date": "2026-10-24",
            "requires_scrutiny": False,
            "requires_evc": True,
            "requires_sac_shc": True,
            **attributes,
        }

    def test_admin_can_create_pending_initial_application_then_auto_route(self):
        headers = self.login("admin")
        response = self.client.post(
            "/api/applications",
            headers=headers,
            json=self.create_payload(),
        )

        self.assertEqual(response.status_code, 201)
        application = response.json()["application"]
        self.assertEqual(application["stage"], "Application Review")
        self.assertEqual(application["status"], "Pending")
        self.assertIsNone(application["workflow_route"])
        self.assertNotIn("routing_decision", application)
        self.assertEqual(
            [event["action"] for event in application["history"]],
            ["Application created"],
        )
        self.assertEqual(self.audit_actions("TEST-CREATED"), ["Application created"])

        routed = self.client.post(
            "/api/applications/TEST-CREATED/auto-route",
            headers=headers,
        )
        self.assertEqual(routed.status_code, 200)
        routed_application = routed.json()["application"]
        self.assertEqual(routed_application["workflow_route"], "EVC_SAC_SHC")
        self.assertEqual(routed_application["stage"], "EVC")
        self.assertEqual(
            routed_application["routing_decision"]["rule_id"],
            "demo-evc-sac-shc",
        )
        self.assertEqual(
            self.audit_actions("TEST-CREATED"),
            ["Application created", "Workflow automatically assigned"],
        )

    def test_only_admin_can_create_applications(self):
        payload = self.create_payload("TEST-OFFICER-CREATE")
        self.assertEqual(
            self.client.post(
                "/api/applications",
                json=payload,
            ).status_code,
            401,
        )
        for username in ("officer", "viewer"):
            with self.subTest(username=username):
                response = self.client.post(
                    "/api/applications",
                    headers=self.login(username),
                    json=payload,
                )
                self.assertEqual(response.status_code, 403)

    def test_application_creation_validates_required_fields_and_values(self):
        headers = self.login("admin")
        missing_field = self.create_payload()
        missing_field.pop("institution")
        invalid_date = self.create_payload("TEST-BAD-DATE", date="24-10-2026")
        invalid_attribute = self.create_payload(
            "TEST-BAD-ATTRIBUTE",
            requires_evc="yes",
        )
        unknown_field = self.create_payload(
            "TEST-UNKNOWN",
            workflow_route="STANDARD",
        )

        for payload in (
            missing_field,
            invalid_date,
            invalid_attribute,
            unknown_field,
        ):
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/api/applications",
                    headers=headers,
                    json=payload,
                )
                self.assertEqual(response.status_code, 400)

    def test_duplicate_application_id_is_rejected_without_overwriting(self):
        headers = self.login("admin")
        original = self.client.post(
            "/api/applications",
            headers=headers,
            json=self.create_payload("TEST-DUPLICATE", institution="Original"),
        )
        duplicate = self.client.post(
            "/api/applications",
            headers=headers,
            json=self.create_payload("TEST-DUPLICATE", institution="Replacement"),
        )

        self.assertEqual(original.status_code, 201)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(
            main.find_application("TEST-DUPLICATE")["institution"],
            "Original",
        )

    def test_auto_route_uses_configured_demo_attributes_and_records_decision(self):
        self.add_application(
            "TEST-ROUTE",
            requires_scrutiny=True,
            requires_evc=True,
            requires_sac_shc=True,
        )

        response = self.client.post(
            "/api/applications/TEST-ROUTE/auto-route",
            headers=self.login("admin"),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["application"]["workflow_route"], "SCRUTINY_EVC_SAC_SHC")
        self.assertEqual(body["application"]["stage"], "Scrutiny")
        self.assertEqual(
            body["routing_decision"]["rule_id"],
            "demo-scrutiny-evc-sac-shc",
        )
        self.assertEqual(body["routing_decision"]["actor"], "admin")
        self.assertIn(
            "Workflow automatically assigned",
            self.audit_actions("TEST-ROUTE"),
        )

    def test_auto_route_uses_default_when_no_demo_rule_matches(self):
        self.add_application("TEST-DEFAULT")

        response = self.client.post(
            "/api/applications/TEST-DEFAULT/auto-route",
            headers=self.login("admin"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["application"]["workflow_route"],
            "STANDARD",
        )
        self.assertEqual(
            response.json()["routing_decision"]["rule_id"],
            "demo-standard-default",
        )

    def test_only_admin_can_assign_automatic_route(self):
        self.add_application("TEST-ROLE")

        response = self.client.post(
            "/api/applications/TEST-ROLE/auto-route",
            headers=self.login("officer"),
        )

        self.assertEqual(response.status_code, 403)

    def test_admin_can_manually_assign_route_before_processing(self):
        self.add_application("TEST-MANUAL")

        response = self.client.post(
            "/api/applications/TEST-MANUAL/workflow",
            headers=self.login("admin"),
            json={"workflow_route": "SCRUTINY_ONLY"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["application"]["workflow_route"],
            "SCRUTINY_ONLY",
        )
        self.assertEqual(response.json()["application"]["stage"], "Scrutiny")
        self.assertIn(
            "Workflow changed",
            self.audit_actions("TEST-MANUAL"),
        )

    def test_workflow_advances_in_order_and_ignores_skip_attempts(self):
        self.add_application("TEST-ORDER")
        headers = self.login("admin")
        routed = self.client.post(
            "/api/applications/TEST-ORDER/auto-route",
            headers=headers,
        )
        self.assertEqual(routed.status_code, 200)

        skip_attempt = self.client.post(
            "/api/applications/TEST-ORDER/advance",
            headers=headers,
            json={"stage": "Final Approval"},
        )
        self.assertEqual(skip_attempt.status_code, 200)
        self.assertEqual(
            skip_attempt.json()["application"]["stage"],
            "Document Verification",
        )
        next_transition = self.client.post(
            "/api/applications/TEST-ORDER/advance",
            headers=headers,
        )
        self.assertEqual(
            next_transition.json()["application"]["stage"],
            "Final Approval",
        )

    def test_return_requires_reason_resubmission_then_workflow_continues(self):
        self.add_application("TEST-RETURN")
        headers = self.login("officer")
        returned = self.client.post(
            "/api/applications/TEST-RETURN/return",
            headers=headers,
            json={"reason": "  Provide the signed lease.  "},
        )
        self.assertEqual(returned.status_code, 200)
        self.assertEqual(returned.json()["application"]["reason"], "Provide the signed lease.")

        missing_reason = self.client.post(
            "/api/applications/TEST-RETURN/return",
            headers=headers,
            json={"reason": " "},
        )
        self.assertEqual(missing_reason.status_code, 400)

        premature_advance = self.client.post(
            "/api/applications/TEST-RETURN/advance",
            headers=headers,
        )
        self.assertEqual(premature_advance.status_code, 409)

        resubmitted = self.client.post(
            "/api/applications/TEST-RETURN/resubmit",
            headers=headers,
        )
        self.assertEqual(resubmitted.status_code, 200)
        self.assertEqual(
            resubmitted.json()["application"]["status"],
            "Under Review",
        )
        advanced = self.client.post(
            "/api/applications/TEST-RETURN/advance",
            headers=headers,
        )
        self.assertEqual(advanced.status_code, 200)
        self.assertEqual(
            advanced.json()["application"]["stage"],
            "Document Verification",
        )

        history = self.client.get(
            "/api/applications/TEST-RETURN/history",
            headers=headers,
        ).json()["history"]
        actions = [event["action"] for event in history]
        self.assertEqual(
            actions,
            ["Application returned", "Application resubmitted", "Stage advanced"],
        )
        self.assertIn("Provide the signed lease.", history[0]["details"])
        self.assertIn("Provide the signed lease.", history[1]["details"])
        self.assertTrue(all(event["actor"] == "officer" for event in history))
        self.assertTrue(all(event["timestamp"] for event in history))

    def test_route_cannot_be_changed_after_processing_or_audit_history(self):
        self.add_application("TEST-LOCK")
        headers = self.login("admin")
        self.assertEqual(
            self.client.post(
                "/api/applications/TEST-LOCK/auto-route",
                headers=headers,
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/applications/TEST-LOCK/workflow",
                headers=headers,
                json={"workflow_route": "SCRUTINY_ONLY"},
            ).status_code,
            409,
        )

    def test_auto_route_rejects_applications_with_existing_audit_history(self):
        application = self.add_application("TEST-AUDIT-LOCK")
        main.record_event(
            application,
            "Existing processing event",
            application["stage"],
            "Application processing already began.",
            "officer",
        )
        application["status"] = "Pending"
        main.save_application(application)

        response = self.client.post(
            "/api/applications/TEST-AUDIT-LOCK/auto-route",
            headers=self.login("admin"),
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("before processing", response.json()["detail"])

    def test_viewer_cannot_resubmit(self):
        self.add_application(
            "TEST-VIEWER",
            status="Returned",
            reason="Supply a corrected document.",
        )
        response = self.client.post(
            "/api/applications/TEST-VIEWER/resubmit",
            headers=self.login("viewer"),
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
