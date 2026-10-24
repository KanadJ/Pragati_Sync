import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

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

    def test_dashboard_aggregates_by_region_officer_and_stage_with_missing_values(self):
        headers = self.login("viewer")
        applications = [
            {
                "region": "North",
                "officer": "Officer A",
                "stage": "Document Verification",
                "status": "Pending",
            },
            {
                "region": "North",
                "officer": "Officer A",
                "stage": "Document Verification",
                "status": "Returned",
            },
            {
                "region": "South",
                "officer": "Officer B",
                "stage": "Completed",
                "status": "Approved",
            },
            {
                "region": None,
                "officer": "  ",
                "stage": None,
                "status": "Pending",
            },
        ]

        with patch("main.get_all_applications", return_value=applications):
            response = self.client.get("/api/dashboard", headers=headers)

        self.assertEqual(response.status_code, 200)
        dashboard = response.json()
        self.assertEqual(dashboard["total_applications"], 4)
        self.assertEqual(
            dashboard["by_region"],
            {"North": 2, "South": 1, "Unassigned": 1},
        )
        self.assertEqual(
            dashboard["by_officer"],
            {"Officer A": 2, "Officer B": 1, "Unassigned": 1},
        )
        self.assertEqual(
            dashboard["by_stage"],
            {"Document Verification": 2, "Completed": 1, "Unknown": 1},
        )

    def test_dashboard_returns_empty_aggregates_for_empty_application_list(self):
        headers = self.login("viewer")
        with patch("main.get_all_applications", return_value=[]):
            response = self.client.get("/api/dashboard", headers=headers)

        self.assertEqual(response.status_code, 200)
        dashboard = response.json()
        self.assertEqual(dashboard["total_applications"], 0)
        self.assertEqual(dashboard["pending_requests"], 0)
        self.assertEqual(dashboard["approved"], 0)
        self.assertEqual(dashboard["by_region"], {})
        self.assertEqual(dashboard["by_officer"], {})
        self.assertEqual(dashboard["by_stage"], {})

    def test_workflow_risk_deadline_bands_overdue_cap_and_recommendations(self):
        settings = main.get_compliance_settings()
        today = date(2026, 10, 24)
        base_application = {
            "id": "RISK-DEADLINE",
            "stage": "Application Review",
            "status": "Pending",
        }

        overdue = main.calculate_workflow_risk(
            {**base_application, "due_date": (today - timedelta(days=1)).isoformat()},
            [],
            settings,
            today=today,
        )
        self.assertEqual(overdue["risk_score"], 32)
        self.assertEqual(overdue["risk_level"], "Moderate")
        self.assertEqual(overdue["risk_factors"][0]["points"], 32)
        self.assertTrue(overdue["risk_recommendations"])

        capped_overdue = main.calculate_workflow_risk(
            {**base_application, "due_date": (today - timedelta(days=20)).isoformat()},
            [],
            settings,
            today=today,
        )
        self.assertEqual(capped_overdue["risk_score"], 40)

        upcoming = main.calculate_workflow_risk(
            {**base_application, "due_date": (today + timedelta(days=3)).isoformat()},
            [],
            settings,
            today=today,
        )
        self.assertEqual(upcoming["risk_score"], 25)
        self.assertEqual(upcoming["risk_level"], "Moderate")

        outside_warning = main.calculate_workflow_risk(
            {**base_application, "due_date": (today + timedelta(days=31)).isoformat()},
            [],
            settings,
            today=today,
        )
        self.assertEqual(outside_warning["risk_score"], 0)
        self.assertEqual(outside_warning["risk_level"], "Low")

    def test_workflow_risk_stage_age_and_risk_level_thresholds(self):
        settings = main.get_compliance_settings()
        today = date(2026, 10, 24)
        application = {
            "id": "RISK-STAGE",
            "stage": "Document Verification",
            "status": "Under Review",
            "due_date": (today + timedelta(days=100)).isoformat(),
        }
        risk = main.calculate_workflow_risk(
            application,
            [],
            settings,
            stage_entered_at="2026-09-24T12:00:00+00:00",
            today=today,
        )
        self.assertEqual(risk["risk_score"], 25)
        stage_factor = next(
            factor for factor in risk["risk_factors"]
            if factor["category"] == "stage_age"
        )
        self.assertEqual(stage_factor["points"], 25)
        self.assertIn("blockers", stage_factor["recommendation"])

        thresholds = settings["workflow_risk_assessment"]["risk_level_thresholds"]
        self.assertEqual(main.get_workflow_risk_level(0, thresholds), "Low")
        self.assertEqual(main.get_workflow_risk_level(25, thresholds), "Moderate")
        self.assertEqual(main.get_workflow_risk_level(50, thresholds), "High")
        self.assertEqual(main.get_workflow_risk_level(75, thresholds), "Critical")
        self.assertEqual(main.get_workflow_risk_level(None, thresholds), "Unknown")

    def test_workflow_risk_compliance_caps_and_missing_required_documents(self):
        settings = main.get_compliance_settings()
        settings["required_document_types"] = ["Lease", "Fire Safety"]
        today = date(2026, 10, 24)
        application = {
            "id": "RISK-COMPLIANCE",
            "stage": "Application Review",
            "status": "Under Review",
            "due_date": "invalid-date",
        }
        documents = [
            {
                "id": 1,
                "document_type": "Lease",
                "name": "Signed lease",
                "status": "Verified",
                "expiry_date": (today - timedelta(days=1)).isoformat(),
            },
            {
                "id": 2,
                "document_type": "Electrical safety",
                "name": "Safety certificate",
                "status": "Pending",
                "expiry_date": None,
            },
        ]

        risk = main.calculate_workflow_risk(
            application,
            documents,
            settings,
            today=today,
        )
        compliance_factors = [
            factor for factor in risk["risk_factors"]
            if factor["category"] == "compliance"
        ]
        self.assertEqual(sum(factor["points"] for factor in compliance_factors), 35)
        self.assertEqual(risk["risk_score"], 35)
        self.assertEqual(risk["risk_level"], "Moderate")
        self.assertTrue(any("Missing Fire Safety" == factor["name"] for factor in compliance_factors))
        self.assertLessEqual(risk["risk_score"], 100)
        self.assertTrue(any("invalid" in gap for gap in risk["risk_data_gaps"]))
        self.assertTrue(any("renewal" in text.lower() for text in risk["risk_recommendations"]))
        self.assertTrue(any("Fire Safety" in text for text in risk["risk_recommendations"]))

    def test_workflow_risk_configuration_rejects_invalid_caps_and_thresholds(self):
        settings = main.get_compliance_settings()["workflow_risk_assessment"]
        invalid_caps = {
            **settings,
            "factor_caps": {"deadline": 40, "stage_age": 25, "compliance": 34},
        }
        with self.assertRaises(main.HTTPException):
            main.validate_workflow_risk_assessment(invalid_caps)

        invalid_levels = {
            **settings,
            "risk_level_thresholds": {
                "low_max": 50,
                "moderate_max": 49,
                "high_max": 74,
            },
        }
        with self.assertRaises(main.HTTPException):
            main.validate_workflow_risk_assessment(invalid_levels)

    def test_workflow_risk_missing_data_is_unknown_not_zero(self):
        settings = main.get_compliance_settings()
        risk = main.calculate_workflow_risk(
            {
                "id": "RISK-UNKNOWN",
                "stage": "Unrecognized",
                "status": "Under Review",
                "due_date": "not-a-date",
            },
            [],
            settings,
            stage_entered_at="not-a-timestamp",
            today=date(2026, 10, 24),
        )
        self.assertIsNone(risk["risk_score"])
        self.assertEqual(risk["risk_level"], "Unknown")
        self.assertGreaterEqual(len(risk["risk_data_gaps"]), 3)

    def test_workflow_risk_uses_stage_entry_audit_and_monitoring_endpoint(self):
        headers = self.login("viewer")
        application_id = "RISK-MONITOR"
        today = date.today()
        self.add_application(
            application_id,
            due_date=(today - timedelta(days=1)).isoformat(),
            stage="Document Verification",
            status="Under Review",
        )
        with main.connect_db() as connection:
            connection.execute("""
                INSERT INTO audit_events
                    (application_id, action, stage, details, timestamp, actor)
                VALUES (?, 'Stage advanced', ?, 'Previous stage: Application Review',
                        ?, 'officer')
            """, (
                application_id,
                "Document Verification",
                (today - timedelta(days=30)).isoformat() + "T12:00:00+00:00",
            ))

        response = self.client.get("/api/monitoring", headers=headers)
        self.assertEqual(response.status_code, 200)
        result = next(
            item for item in response.json()["applications"]
            if item["id"] == application_id
        )
        self.assertEqual(result["risk_score"], 57)
        self.assertEqual(result["risk_level"], "High")
        self.assertTrue(result["risk_factors"])
        self.assertTrue(result["risk_recommendations"])
        self.assertIn("High", response.json()["risk_summary"])

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
