import csv
import io
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

    def test_pendency_report_deadline_boundaries_completion_and_stage_duration(self):
        today = date(2026, 10, 24)
        self.add_application(
            "PENDENCY-OVERDUE",
            institution="Overdue Institute",
            officer="Officer One",
            stage="Application Review",
            status="Under Review",
            due_date=(today - timedelta(days=2)).isoformat(),
        )
        self.add_application(
            "PENDENCY-TODAY",
            stage="Document Verification",
            status="Under Review",
            due_date=today.isoformat(),
        )
        self.add_application(
            "PENDENCY-UPCOMING",
            due_date=(today + timedelta(days=5)).isoformat(),
        )
        self.add_application(
            "PENDENCY-COMPLETED",
            stage="Completed",
            status="Approved",
            due_date=(today - timedelta(days=10)).isoformat(),
        )
        self.add_application(
            "PENDENCY-INVALID",
            due_date="not-a-date",
        )
        self.add_application(
            "PENDENCY-MISSING",
            due_date=None,
        )
        self.add_application(
            "PENDENCY-BAD-STAGE-TIME",
            due_date=(today + timedelta(days=20)).isoformat(),
        )
        with main.connect_db() as connection:
            connection.executemany("""
                INSERT INTO audit_events
                    (application_id, action, stage, details, timestamp, actor)
                VALUES (?, 'Stage advanced', ?, ?, ?, ?)
            """, [
                (
                    "PENDENCY-OVERDUE",
                    "Application Review",
                    "Entered current stage",
                    "2026-10-20T12:00:00+00:00",
                    "officer",
                ),
                (
                    "PENDENCY-BAD-STAGE-TIME",
                    "Application Review",
                    "Invalid timestamp",
                    "not-a-timestamp",
                    "officer",
                ),
            ])

        headers = self.login("viewer")
        with patch("main.local_date", return_value=today):
            response = self.client.get("/api/reports/pendency.csv", headers=headers)
            monitoring = self.client.get("/api/monitoring", headers=headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/csv; charset=utf-8")
        self.assertIn("attachment; filename=\"pendency-report.csv\"", response.headers["content-disposition"])
        rows = {
            row["Application ID"]: row
            for row in csv.DictReader(io.StringIO(response.text))
        }
        self.assertIn(
            "Prototype Compliance State (Not Official APH Determination)",
            rows["PENDENCY-OVERDUE"],
        )
        overdue = rows["PENDENCY-OVERDUE"]
        self.assertEqual(overdue["Deadline State"], "Overdue")
        self.assertEqual(overdue["Days Overdue"], "2")
        self.assertEqual(overdue["Days Until Deadline"], "")
        self.assertEqual(
            overdue["Current Stage Entered At (Asia/Kolkata)"],
            "2026-10-20T17:30:00+05:30",
        )
        self.assertEqual(overdue["Pending Duration (Days)"], "4")
        self.assertEqual(rows["PENDENCY-TODAY"]["Deadline State"], "Due today")
        self.assertEqual(rows["PENDENCY-TODAY"]["Days Until Deadline"], "0")
        self.assertEqual(rows["PENDENCY-UPCOMING"]["Deadline State"], "Upcoming")
        self.assertEqual(rows["PENDENCY-UPCOMING"]["Days Until Deadline"], "5")
        self.assertEqual(rows["PENDENCY-COMPLETED"]["Deadline State"], "Completed")
        self.assertEqual(rows["PENDENCY-COMPLETED"]["Days Overdue"], "")
        self.assertEqual(rows["PENDENCY-INVALID"]["Deadline State"], "Unknown")
        self.assertEqual(rows["PENDENCY-INVALID"]["Pending Duration (Days)"], "Unknown")
        self.assertEqual(rows["PENDENCY-MISSING"]["Deadline State"], "Not set")
        self.assertEqual(rows["PENDENCY-MISSING"]["Deadline"], "Not set")
        self.assertEqual(rows["PENDENCY-MISSING"]["Current Stage Entered At (Asia/Kolkata)"], "Unknown")
        self.assertEqual(rows["PENDENCY-MISSING"]["Pending Duration (Days)"], "Unknown")
        self.assertEqual(
            rows["PENDENCY-BAD-STAGE-TIME"]["Current Stage Entered At (Asia/Kolkata)"],
            "Unknown",
        )
        self.assertEqual(
            rows["PENDENCY-BAD-STAGE-TIME"]["Pending Duration (Days)"],
            "Unknown",
        )

        monitoring_by_id = {
            item["id"]: item for item in monitoring.json()["applications"]
        }
        self.assertTrue(any(
            alert["type"] == "Overdue"
            for alert in monitoring_by_id["PENDENCY-OVERDUE"]["alerts"]
        ))
        self.assertFalse(any(
            alert["type"] == "Overdue"
            for alert in monitoring_by_id["PENDENCY-COMPLETED"]["alerts"]
        ))
        self.assertEqual(
            overdue["Prototype Risk Score"],
            str(monitoring_by_id["PENDENCY-OVERDUE"]["risk_score"]),
        )
        self.assertEqual(
            overdue["Prototype Risk Level"],
            monitoring_by_id["PENDENCY-OVERDUE"]["risk_level"],
        )

    def test_pendency_report_includes_document_and_active_escalation_data(self):
        today = date(2026, 10, 24)
        application = self.add_application(
            "PENDENCY-DOCUMENT",
            stage="Document Verification",
            status="Under Review",
            due_date=(today - timedelta(days=1)).isoformat(),
        )
        with main.connect_db() as connection:
            connection.execute("""
                INSERT INTO documents
                    (application_id, document_type, name, status, expiry_date,
                     renewal_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                application["id"],
                "Lease",
                "Current lease",
                "Submitted",
                None,
                "Submitted",
                "2026-10-20T00:00:00+00:00",
                "2026-10-20T00:00:00+00:00",
            ))
            connection.execute("""
                INSERT INTO notifications
                    (application_id, category, message, created_at, escalation_level, dedupe_key)
                VALUES (?, 'application_overdue', ?, ?, ?, ?)
            """, (
                application["id"],
                "Deadline passed",
                "2026-10-23T12:00:00+00:00",
                2,
                "test-active-overdue-notification",
            ))
            connection.execute("""
                INSERT INTO notifications
                    (application_id, category, message, created_at, escalation_level,
                     dedupe_key, resolved_at)
                VALUES (?, 'application_overdue', ?, ?, ?, ?, ?)
            """, (
                application["id"],
                "Previously resolved deadline",
                "2026-10-20T12:00:00+00:00",
                5,
                "test-resolved-overdue-notification",
                "2026-10-22T12:00:00+00:00",
            ))

        headers = self.login("viewer")
        with patch("main.local_date", return_value=today):
            response = self.client.get("/api/reports/pendency.csv", headers=headers)
        self.assertEqual(response.status_code, 200)
        row = next(
            item
            for item in csv.DictReader(io.StringIO(response.text))
            if item["Application ID"] == application["id"]
        )
        self.assertEqual(row["Document Count"], "1")
        self.assertIn("Lease: Submitted (renewal: Submitted)", row["Document Statuses"])
        self.assertEqual(row["Active Overdue Notifications"], "1")
        self.assertEqual(row["Active Overdue Escalation Level"], "2")
        self.assertEqual(
            row["Prototype Compliance State (Not Official APH Determination)"],
            "Pending Verification",
        )

    def test_pendency_report_empty_results_and_formula_injection_safety(self):
        headers = self.login("viewer")
        with main.connect_db() as connection:
            connection.execute("DELETE FROM applications")
        empty_response = self.client.get(
            "/api/reports/pendency.csv",
            headers=headers,
        )
        self.assertEqual(empty_response.status_code, 200)
        empty_rows = list(csv.reader(io.StringIO(empty_response.text)))
        self.assertGreater(len(empty_rows[0]), 0)
        self.assertEqual(len(empty_rows), 1)

        self.add_application(
            "=2+2",
            institution=" \t=HYPERLINK(\"https://example.invalid\")",
            officer="\x00+SUM(1,1)",
        )
        self.add_application(
            "SAFE-CSV",
            institution='Ordinary, "quoted" institution',
            officer="Normal officer",
        )
        self.add_application(
            "FORMAT-CSV",
            institution="\ufeff@SUM(1,1)",
        )
        response = self.client.get("/api/reports/pendency.csv", headers=headers)
        self.assertEqual(response.status_code, 200)
        rows = {
            row["Application ID"]: row
            for row in csv.DictReader(io.StringIO(response.text))
        }
        self.assertIn("'=2+2", rows)
        self.assertEqual(
            rows["'=2+2"]["Institution"],
            "' \t=HYPERLINK(\"https://example.invalid\")",
        )
        self.assertEqual(rows["'=2+2"]["Assigned Officer"], "'\x00+SUM(1,1)")
        self.assertEqual(
            rows["SAFE-CSV"]["Institution"],
            'Ordinary, "quoted" institution',
        )
        self.assertEqual(rows["SAFE-CSV"]["Assigned Officer"], "Normal officer")
        self.assertEqual(rows["FORMAT-CSV"]["Institution"], "'\ufeff@SUM(1,1)")

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

    def test_evc_listing_only_includes_routes_that_require_evc(self):
        self.add_application("TEST-EVC-ROUTE", workflow_route="SCRUTINY_EVC")
        self.add_application("TEST-NO-EVC-ROUTE", workflow_route="SCRUTINY_ONLY")
        response = self.client.get("/api/evc", headers=self.login("viewer"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["upcoming_window_days"], 30)
        self.assertEqual(
            [item["application_id"] for item in body["evcs"]],
            ["TEST-EVC-ROUTE"],
        )
        self.assertEqual(body["evcs"][0]["status"], "Pending Scheduling")

    def test_evc_upcoming_window_is_configurable(self):
        application_id = "TEST-EVC-WINDOW"
        self.add_application(application_id, workflow_route="SCRUTINY_EVC")
        headers = self.login("officer")
        scheduled_date = (date.today() + timedelta(days=2)).isoformat()
        self.assertEqual(
            self.client.post(
                f"/api/applications/{application_id}/evc/schedule",
                headers=headers,
                json={"scheduled_date": scheduled_date},
            ).status_code,
            200,
        )

        with patch(
            "main.get_compliance_settings",
            return_value={"evc_tracking": {"upcoming_window_days": 1}},
        ):
            body = self.client.get("/api/evc", headers=headers).json()
        self.assertEqual(body["upcoming_window_days"], 1)
        self.assertEqual(body["upcoming_count"], 0)

    def test_evc_schema_migration_preserves_existing_application_records(self):
        application = self.add_application(
            "TEST-EVC-MIGRATION",
            workflow_route="SCRUTINY_EVC",
            custom_field="preserve this value",
        )
        main.initialize_database()
        with main.connect_db() as connection:
            stored = connection.execute(
                "SELECT data FROM applications WHERE id = ?",
                (application["id"],),
            ).fetchone()
            evc_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'evc_tracking'"
            ).fetchone()
        self.assertEqual(json.loads(stored["data"])["custom_field"], "preserve this value")
        self.assertIsNotNone(evc_table)

    def test_evc_schedule_reschedule_delay_completion_persistence_and_audit(self):
        application_id = "TEST-EVC-LIFECYCLE"
        self.add_application(application_id, workflow_route="EVC_SAC_SHC")
        officer = self.login("officer")
        endpoint = f"/api/applications/{application_id}/evc"
        self.assertEqual(
            self.client.post(
                f"{endpoint}/schedule",
                headers=officer,
                json={"scheduled_date": "invalid", "notes": "Initial planning"},
            ).status_code,
            400,
        )

        future_date = (date.today() + timedelta(days=2)).isoformat()
        scheduled = self.client.post(
            f"{endpoint}/schedule",
            headers=officer,
            json={"scheduled_date": future_date, "notes": "Initial visit plan"},
        )
        self.assertEqual(scheduled.status_code, 200)
        self.assertEqual(scheduled.json()["evc"]["status"], "Scheduled")
        self.assertEqual(scheduled.json()["evc"]["notes"], "Initial visit plan")

        fetched = self.client.get("/api/evc", headers=officer).json()
        listed = next(item for item in fetched["evcs"] if item["application_id"] == application_id)
        self.assertEqual(listed["scheduled_date"], future_date)
        self.assertEqual(listed["status"], "Scheduled")

        past_date = (date.today() - timedelta(days=1)).isoformat()
        rescheduled = self.client.post(
            f"{endpoint}/schedule",
            headers=officer,
            json={"scheduled_date": past_date, "notes": "Visit date elapsed"},
        )
        self.assertEqual(rescheduled.status_code, 200)
        self.assertEqual(rescheduled.json()["evc"]["status"], "Delayed")
        overview = self.client.get("/api/evc", headers=officer).json()
        self.assertEqual(overview["overdue_count"], 1)

        rescheduled_future = self.client.post(
            f"{endpoint}/schedule",
            headers=officer,
            json={"scheduled_date": future_date, "notes": "New visit date"},
        )
        self.assertEqual(rescheduled_future.status_code, 200)
        self.assertEqual(rescheduled_future.json()["evc"]["status"], "Scheduled")
        completed = self.client.post(
            f"{endpoint}/complete",
            headers=self.login("admin"),
            json={"notes": "Visit completed."},
        )
        self.assertEqual(completed.status_code, 200)
        evc = completed.json()["evc"]
        self.assertEqual(evc["status"], "Completed")
        self.assertIsNotNone(evc["completed_at"])
        self.assertIn("Completion: Visit completed.", evc["notes"])

        persistent = self.client.get("/api/evc", headers=officer).json()
        persisted_evc = next(
            item for item in persistent["evcs"]
            if item["application_id"] == application_id
        )
        self.assertEqual(persisted_evc["status"], "Completed")
        self.assertEqual(
            self.client.post(f"{endpoint}/schedule", headers=officer, json={
                "scheduled_date": future_date,
            }).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(f"{endpoint}/complete", headers=officer, json={}).status_code,
            409,
        )

        with main.connect_db() as connection:
            actions = connection.execute("""
                SELECT action, actor, timestamp
                FROM audit_events
                WHERE application_id = ? AND action LIKE 'EVC %'
                ORDER BY id
            """, (application_id,)).fetchall()
            persisted = connection.execute(
                "SELECT status, scheduled_date, completed_at FROM evc_tracking WHERE application_id = ?",
                (application_id,),
            ).fetchone()
        self.assertEqual(
            [row["action"] for row in actions],
            ["EVC scheduled", "EVC rescheduled", "EVC rescheduled", "EVC completed"],
        )
        self.assertEqual([row["actor"] for row in actions], ["officer", "officer", "officer", "admin"])
        self.assertTrue(all(row["timestamp"] for row in actions))
        self.assertEqual(persisted["status"], "Completed")
        self.assertEqual(persisted["scheduled_date"], future_date)
        self.assertIsNotNone(persisted["completed_at"])

    def test_evc_schedule_and_completion_reject_applications_without_evc_route(self):
        application_id = "TEST-NO-EVC"
        self.add_application(application_id, workflow_route="STANDARD")
        officer = self.login("officer")
        endpoint = f"/api/applications/{application_id}/evc"
        self.assertEqual(
            self.client.post(
                f"{endpoint}/schedule",
                headers=officer,
                json={"scheduled_date": date.today().isoformat()},
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(f"{endpoint}/complete", headers=officer, json={}).status_code,
            409,
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
