import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import main


class RoleBasedAccessTests(unittest.TestCase):
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
        token = response.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def test_sensitive_reads_require_authentication(self):
        endpoints = [
            "/api/applications",
            "/api/workflows",
            "/api/dashboard",
            "/api/applications/AICTE-2026-1001/history",
            "/api/audit",
            "/api/monitoring",
        ]

        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(endpoint).status_code, 401)

    def test_each_role_can_read_sensitive_endpoints(self):
        endpoints = [
            "/api/applications",
            "/api/workflows",
            "/api/dashboard",
            "/api/applications/AICTE-2026-1001/history",
            "/api/audit",
            "/api/monitoring",
        ]

        for username in ("admin", "officer", "viewer"):
            headers = self.login(username)
            for endpoint in endpoints:
                with self.subTest(username=username, endpoint=endpoint):
                    self.assertEqual(
                        self.client.get(endpoint, headers=headers).status_code,
                        200,
                    )

    def test_monitoring_exposes_explainable_risk_to_authenticated_roles(self):
        for username in ("admin", "officer", "viewer"):
            response = self.client.get(
                "/api/monitoring",
                headers=self.login(username),
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(
                set(body["risk_summary"]),
                {"Low", "Moderate", "High", "Critical", "Unknown"},
            )
            self.assertTrue(body["applications"])
            application = body["applications"][0]
            self.assertIn("risk_score", application)
            self.assertIn("risk_level", application)
            self.assertIn("risk_factors", application)
            self.assertIn("risk_recommendations", application)
            self.assertIn("risk_data_gaps", application)

    def test_invalid_bearer_token_is_unauthorized(self):
        response = self.client.get(
            "/api/applications",
            headers={"Authorization": "Bearer invalid"},
        )
        self.assertEqual(response.status_code, 401)

    def test_viewer_cannot_mutate_applications_or_workflows(self):
        headers = self.login("viewer")
        mutations = [
            (
                "post",
                "/api/applications/AICTE-2026-1001/workflow",
                {"workflow_route": "STANDARD"},
            ),
            ("post", "/api/applications/AICTE-2026-1001/advance", None),
            (
                "post",
                "/api/applications/AICTE-2026-1001/return",
                {"reason": "Missing document"},
            ),
            (
                "patch",
                "/api/applications/AICTE-2026-1001/compliance",
                {"due_date": "2026-11-01"},
            ),
        ]

        for method, endpoint, payload in mutations:
            with self.subTest(method=method, endpoint=endpoint):
                response = getattr(self.client, method)(
                    endpoint,
                    headers=headers,
                    json=payload,
                )
                self.assertEqual(response.status_code, 403)

    def test_officer_can_process_but_cannot_manage_workflows(self):
        headers = self.login("officer")

        self.assertEqual(
            self.client.post(
                "/api/applications/AICTE-2026-1001/advance",
                headers=headers,
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/applications/AICTE-2026-1002/return",
                headers=headers,
                json={"reason": "Please correct the submitted details"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.patch(
                "/api/applications/AICTE-2026-1003/compliance",
                headers=headers,
                json={"due_date": "2026-11-01"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/applications/AICTE-2026-1001/workflow",
                headers=headers,
                json={"workflow_route": "STANDARD"},
            ).status_code,
            403,
        )

    def test_admin_cannot_change_workflow_after_processing_starts(self):
        application = main.find_application("AICTE-2026-1001")
        application["stage"] = "Scrutiny"
        application["workflow_route"] = "SCRUTINY_EVC"
        application["status"] = "Under Review"
        main.save_application(application)

        response = self.client.post(
            "/api/applications/AICTE-2026-1001/workflow",
            headers=self.login("admin"),
            json={"workflow_route": "SCRUTINY_EVC_SAC_SHC"},
        )

        self.assertEqual(response.status_code, 409)

    def test_logout_requires_a_valid_session(self):
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 401)
        self.assertEqual(
            self.client.post(
                "/api/auth/logout",
                headers={"Authorization": "Bearer invalid"},
            ).status_code,
            401,
        )

        headers = self.login("viewer")
        self.assertEqual(
            self.client.post("/api/auth/logout", headers=headers).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/applications", headers=headers).status_code,
            401,
        )


if __name__ == "__main__":
    unittest.main()
