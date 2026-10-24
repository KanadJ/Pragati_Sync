import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
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

    def test_document_tracking_statuses_use_configured_expiry_window(self):
        settings = main.get_compliance_settings()
        settings["expiry_warning_days"] = 5
        today = date.today()

        def tracking(status, expiry=None, renewal_status="Not Required"):
            return main.get_document_tracking_status(
                {
                    "status": status,
                    "expiry_date": expiry,
                    "renewal_status": renewal_status,
                },
                settings,
                today,
            )

        self.assertEqual(
            tracking("Verified", (today + timedelta(days=5)).isoformat()),
            "Expiring Soon",
        )
        self.assertEqual(
            tracking("Verified", (today + timedelta(days=6)).isoformat()),
            "Valid",
        )
        self.assertEqual(
            tracking("Verified", (today - timedelta(days=1)).isoformat()),
            "Expired",
        )
        self.assertEqual(tracking("Pending"), "Awaiting Verification")
        self.assertEqual(
            tracking("Verified", renewal_status="Submitted"),
            "Submitted for Renewal",
        )
        self.assertEqual(
            tracking("Verified", renewal_status="Rejected"),
            "Awaiting Renewal",
        )
        self.assertEqual(
            tracking("Verified", renewal_status="Awaiting Renewal"),
            "Awaiting Renewal",
        )

    def test_expiry_notifications_use_warning_setting_and_deduplicate(self):
        settings = json.loads(main.COMPLIANCE_SETTINGS_PATH.read_text(encoding="utf-8"))
        settings["expiry_warning_days"] = 5
        main.COMPLIANCE_SETTINGS_PATH.write_text(json.dumps(settings), encoding="utf-8")
        officer = self.login("officer")
        self.assertEqual(
            self.create_document(
                officer,
                status="Verified",
                expiry_date=(date.today() + timedelta(days=5)).isoformat(),
            ).status_code,
            201,
        )
        self.assertEqual(
            self.create_document(
                officer,
                status="Verified",
                expiry_date=(date.today() - timedelta(days=1)).isoformat(),
            ).status_code,
            201,
        )

        admin = self.login("admin")
        checked = self.client.post("/api/compliance/check", headers=admin)
        self.assertEqual(checked.status_code, 200)
        self.assertEqual(checked.json()["created"], 0)
        with main.connect_db() as connection:
            categories = [
                row["category"]
                for row in connection.execute(
                    "SELECT category FROM notifications ORDER BY id"
                ).fetchall()
            ]
        self.assertCountEqual(categories, ["document_expiring", "document_expired"])

    def test_existing_documents_receive_safe_additive_renewal_migration(self):
        legacy_database = Path(self.database_directory.name) / "legacy.db"
        main.DATABASE_PATH = legacy_database
        connection = main.sqlite3.connect(legacy_database)
        try:
            connection.execute("""
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_id TEXT NOT NULL,
                    document_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expiry_date TEXT,
                    reviewer_remarks TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                INSERT INTO documents
                    (application_id, document_type, name, status, created_at, updated_at)
                VALUES ('COMPLIANCE-1', 'Lease', 'Legacy lease', 'Verified', '2025-01-01', '2025-01-01')
            """)
            connection.commit()
        finally:
            connection.close()

        main.initialize_database()
        with main.connect_db() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(documents)").fetchall()
            }
            document = connection.execute(
                "SELECT name, status FROM documents WHERE id = 1"
            ).fetchone()
        self.assertTrue({
            "issue_date", "submission_date", "renewal_status",
            "renewal_issue_date", "renewal_expiry_date", "renewal_remarks",
        }.issubset(columns))
        self.assertEqual(document["name"], "Legacy lease")
        self.assertEqual(document["status"], "Verified")
        main.DATABASE_PATH = Path(self.database_directory.name) / "test.db"

    def test_renewal_submission_approval_and_audit_history(self):
        officer = self.login("officer")
        created = self.create_document(
            officer,
            status="Verified",
            expiry_date=(date.today() - timedelta(days=1)).isoformat(),
        )
        self.assertEqual(created.status_code, 201)
        document_id = created.json()["document"]["id"]
        proposed_expiry = (date.today() + timedelta(days=365)).isoformat()
        submitted = self.client.post(
            f"/api/applications/COMPLIANCE-1/documents/{document_id}/renewal",
            headers=officer,
            json={
                "issue_date": date.today().isoformat(),
                "expiry_date": proposed_expiry,
                "remarks": "Renewal documents submitted.",
            },
        )
        self.assertEqual(submitted.status_code, 200)
        pending = submitted.json()["document"]
        self.assertEqual(pending["tracking_status"], "Submitted for Renewal")
        self.assertEqual(pending["verification_status"], "Awaiting Verification")
        self.assertEqual(pending["renewal_status"], "Submitted")
        self.assertEqual(pending["expiry_date"], (date.today() - timedelta(days=1)).isoformat())
        self.assertIsNotNone(pending["submission_date"])
        with main.connect_db() as connection:
            expiry_notice = connection.execute(
                "SELECT resolved_at FROM notifications WHERE category = 'document_expired'"
            ).fetchone()
        self.assertIsNotNone(expiry_notice["resolved_at"])

        approved = self.client.post(
            f"/api/applications/COMPLIANCE-1/documents/{document_id}/renewal/verify",
            headers=self.login("admin"),
            json={"decision": "Approved", "remarks": "Reviewed."},
        )
        self.assertEqual(approved.status_code, 200)
        updated = approved.json()["document"]
        self.assertEqual(updated["status"], "Verified")
        self.assertEqual(updated["renewal_status"], "Approved")
        self.assertEqual(updated["expiry_date"], proposed_expiry)
        self.assertEqual(updated["tracking_status"], "Valid")

        with main.connect_db() as connection:
            actions = [
                (row["action"], row["actor"], row["timestamp"])
                for row in connection.execute("""
                    SELECT action, actor, timestamp FROM audit_events
                    WHERE application_id = 'COMPLIANCE-1'
                """).fetchall()
            ]
        renewal_events = [
            event for event in actions
            if event[0] in {"Document renewal submitted", "Document renewal approved"}
        ]
        self.assertEqual([event[0] for event in renewal_events], [
            "Document renewal submitted",
            "Document renewal approved",
        ])
        self.assertEqual([event[1] for event in renewal_events], ["officer", "admin"])
        self.assertTrue(all(event[2] for event in renewal_events))

    def test_renewal_rejection_requires_reason_and_viewer_cannot_change_status(self):
        officer = self.login("officer")
        created = self.create_document(
            officer,
            status="Verified",
            expiry_date=(date.today() - timedelta(days=1)).isoformat(),
        )
        document_id = created.json()["document"]["id"]
        renewal_url = f"/api/applications/COMPLIANCE-1/documents/{document_id}/renewal"
        self.assertEqual(
            self.client.post(
                renewal_url,
                headers=self.login("viewer"),
                json={"expiry_date": (date.today() + timedelta(days=365)).isoformat()},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                renewal_url,
                headers=officer,
                json={"expiry_date": (date.today() + timedelta(days=365)).isoformat()},
            ).status_code,
            200,
        )
        verify_url = f"{renewal_url}/verify"
        self.assertEqual(
            self.client.post(
                verify_url,
                headers=self.login("viewer"),
                json={"decision": "Approved"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                verify_url,
                headers=self.login("admin"),
                json={"decision": "Rejected"},
            ).status_code,
            400,
        )
        rejected = self.client.post(
            verify_url,
            headers=self.login("admin"),
            json={"decision": "Rejected", "remarks": "Expiry date evidence is incomplete."},
        )
        self.assertEqual(rejected.status_code, 200)
        document = rejected.json()["document"]
        self.assertEqual(document["renewal_status"], "Awaiting Renewal")
        self.assertEqual(document["tracking_status"], "Expired")
        self.assertEqual(document["reviewer_remarks"], "Expiry date evidence is incomplete.")

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

    def test_deadline_reminder_windows_deduplicate_and_resolve_when_date_changes(self):
        today = date(2026, 10, 24)
        for days_remaining, expected_interval in (
            (8, None),
            (7, 7),
            (4, 7),
            (3, 3),
            (2, 3),
            (1, 1),
            (0, None),
        ):
            with self.subTest(days_remaining=days_remaining):
                self.assertEqual(
                    main.reminder_interval(days_remaining, [1, 3, 7]),
                    expected_interval,
                )
        application = self.add_application(
            "REMINDER-1",
            due_date=(today + timedelta(days=8)).isoformat(),
        )
        check_time = datetime(2026, 10, 24, 6, tzinfo=timezone.utc)

        self.assertEqual(
            main.run_compliance_check(as_of=today, now=check_time)["created"],
            0,
        )
        application["due_date"] = (today + timedelta(days=7)).isoformat()
        with main.connect_db() as connection:
            connection.execute(
                "UPDATE applications SET data = ? WHERE id = ?",
                (json.dumps(application), application["id"]),
            )

        first = main.run_compliance_check(as_of=today, now=check_time)
        self.assertEqual(first["created"], 1)
        self.assertEqual(main.run_compliance_check(as_of=today, now=check_time)["created"], 0)
        with main.connect_db() as connection:
            reminder = connection.execute(
                "SELECT * FROM notifications WHERE application_id = ?",
                (application["id"],),
            ).fetchone()
        self.assertEqual(reminder["category"], "application_deadline_reminder")
        self.assertIn(":7", reminder["dedupe_key"])

        application["due_date"] = (today + timedelta(days=2)).isoformat()
        with main.connect_db() as connection:
            connection.execute(
                "UPDATE applications SET data = ? WHERE id = ?",
                (json.dumps(application), application["id"]),
            )
        changed = main.run_compliance_check(
            as_of=today,
            now=check_time + timedelta(days=5),
        )
        self.assertEqual(changed["created"], 1)
        with main.connect_db() as connection:
            notifications = connection.execute(
                "SELECT category, dedupe_key, resolved_at FROM notifications WHERE application_id = ?",
                (application["id"],),
            ).fetchall()
        self.assertEqual(len(notifications), 2)
        self.assertIsNotNone(notifications[0]["resolved_at"])
        self.assertIsNone(notifications[1]["resolved_at"])
        self.assertIn(":3", notifications[1]["dedupe_key"])

    def test_overdue_escalation_thresholds_and_audit_events_are_idempotent(self):
        application = self.add_application(
            "OVERDUE-ESCALATION",
            due_date="2026-09-30",
        )
        first_time = datetime(2026, 10, 1, 6, tzinfo=timezone.utc)
        created = main.run_compliance_check(
            as_of=date(2026, 10, 1),
            now=first_time,
        )
        self.assertEqual(created["created"], 1)

        at_first_threshold = datetime(2026, 10, 4, 6, tzinfo=timezone.utc)
        first_escalation = main.run_compliance_check(
            as_of=date(2026, 10, 4),
            now=at_first_threshold,
        )
        self.assertEqual(first_escalation["escalated"], 1)
        self.assertEqual(
            main.run_compliance_check(
                as_of=date(2026, 10, 4),
                now=at_first_threshold,
            )["escalated"],
            0,
        )
        second_escalation = main.run_compliance_check(
            as_of=date(2026, 10, 8),
            now=datetime(2026, 10, 8, 6, tzinfo=timezone.utc),
        )
        self.assertEqual(second_escalation["escalated"], 1)
        with main.connect_db() as connection:
            notification = connection.execute(
                "SELECT escalation_level FROM notifications WHERE application_id = ?",
                (application["id"],),
            ).fetchone()
            audit = connection.execute(
                """
                SELECT COUNT(*) FROM audit_events
                WHERE application_id = ? AND action = 'Notification escalated'
                """,
                (application["id"],),
            ).fetchone()[0]
        self.assertEqual(notification["escalation_level"], 2)
        self.assertEqual(audit, 2)

    def test_evc_reminders_and_overdue_events_resolve_after_reschedule(self):
        today = main.local_date()
        application = self.add_application(
            "EVC-REMINDER",
            workflow_route="SCRUTINY_EVC",
            stage="EVC",
        )
        officer = self.login("officer")
        endpoint = f"/api/applications/{application['id']}/evc/schedule"
        first_schedule = self.client.post(
            endpoint,
            headers=officer,
            json={"scheduled_date": (today + timedelta(days=7)).isoformat()},
        )
        self.assertEqual(first_schedule.status_code, 200)
        rescheduled = self.client.post(
            endpoint,
            headers=officer,
            json={"scheduled_date": (today + timedelta(days=2)).isoformat()},
        )
        self.assertEqual(rescheduled.status_code, 200)
        overdue_schedule = self.client.post(
            endpoint,
            headers=officer,
            json={"scheduled_date": (today - timedelta(days=1)).isoformat()},
        )
        self.assertEqual(overdue_schedule.status_code, 200)

        with main.connect_db() as connection:
            notifications = connection.execute(
                "SELECT category, resolved_at FROM notifications WHERE application_id = ? ORDER BY id",
                (application["id"],),
            ).fetchall()
        self.assertEqual(
            [row["category"] for row in notifications],
            [
                "evc_scheduled_reminder",
                "evc_scheduled_reminder",
                "evc_overdue",
            ],
        )
        self.assertIsNotNone(notifications[0]["resolved_at"])
        self.assertIsNotNone(notifications[1]["resolved_at"])
        self.assertIsNone(notifications[2]["resolved_at"])
        completed = self.client.post(
            f"/api/applications/{application['id']}/evc/complete",
            headers=officer,
            json={},
        )
        self.assertEqual(completed.status_code, 200)
        with main.connect_db() as connection:
            resolved_overdue = connection.execute(
                "SELECT resolved_at FROM notifications WHERE category = 'evc_overdue' AND application_id = ?",
                (application["id"],),
            ).fetchone()["resolved_at"]
        self.assertIsNotNone(resolved_overdue)

    def test_timezone_date_boundary_and_restart_catch_up_are_idempotent(self):
        self.assertEqual(
            main.local_date(datetime(2026, 10, 24, 18, 30, tzinfo=timezone.utc)),
            date(2026, 10, 25),
        )
        application = self.add_application(
            "RESTART-CATCHUP",
            due_date=(date.today() - timedelta(days=1)).isoformat(),
        )
        with main.connect_db() as connection:
            connection.execute(
                "UPDATE applications SET data = ? WHERE id = ?",
                (json.dumps(application), application["id"]),
            )

        self.client.__exit__(None, None, None)
        self.client = TestClient(main.app)
        self.client.__enter__()
        with main.connect_db() as connection:
            notification_count = connection.execute(
                "SELECT COUNT(*) FROM notifications WHERE application_id = ?",
                (application["id"],),
            ).fetchone()[0]
            created_audit_count = connection.execute(
                """
                SELECT COUNT(*) FROM audit_events
                WHERE application_id = ? AND action = 'Notification created'
                """,
                (application["id"],),
            ).fetchone()[0]
        self.assertEqual(notification_count, 1)
        self.assertEqual(created_audit_count, 1)

        self.client.__exit__(None, None, None)
        self.client = TestClient(main.app)
        self.client.__enter__()
        with main.connect_db() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM notifications WHERE application_id = ?",
                    (application["id"],),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM audit_events
                    WHERE application_id = ? AND action = 'Notification created'
                    """,
                    (application["id"],),
                ).fetchone()[0],
                1,
            )

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
