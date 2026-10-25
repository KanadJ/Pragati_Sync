import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


class WorkflowHeatmapTests(unittest.TestCase):
    def setUp(self):
        self.database_directory = tempfile.TemporaryDirectory()
        self.original_database_path = main.DATABASE_PATH
        main.DATABASE_PATH = Path(self.database_directory.name) / "heatmap.db"
        with main.SESSION_LOCK:
            main.SESSIONS.clear()

        self.client = TestClient(main.app)
        self.client.__enter__()
        with main.connect_db() as connection:
            connection.execute("DELETE FROM applications")
            connection.execute("DELETE FROM audit_events")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        with main.SESSION_LOCK:
            main.SESSIONS.clear()
        main.DATABASE_PATH = self.original_database_path
        self.database_directory.cleanup()

    def login(self, username="viewer"):
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

    def add_application(self, application_id, **overrides):
        application = {
            "id": application_id,
            "institution": f"Institution {application_id}",
            "region": "North",
            "stage": "Application Review",
            "status": "Pending",
            "workflow_route": "STANDARD",
            "date": date.today().isoformat(),
            "history": [],
            **overrides,
        }
        main.save_application(application)
        return application

    def get_heatmap(self, headers, metric="applications"):
        return self.client.get(
            f"/api/monitoring/heatmap?metric={metric}",
            headers=headers,
        ).json()

    @staticmethod
    def cell_count(heatmap, region_key, stage):
        region = next(row for row in heatmap["regions"] if row["key"] == region_key)
        return next(cell["count"] for cell in region["cells"] if cell["stage"] == stage)

    def test_heatmap_aggregates_real_regions_stages_zeroes_and_unknowns(self):
        self.add_application("HEAT-1", region="North")
        self.add_application("HEAT-2", region="north", stage="application review")
        self.add_application("HEAT-3", region="South", stage="Completed")
        self.add_application(
            "HEAT-4",
            region=" ",
            stage="Stage not in workflow configuration",
        )

        response = self.client.get(
            "/api/monitoring/heatmap",
            headers=self.login(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        rows = {row["key"]: row for row in body["regions"]}
        north = rows["region:north"]
        north_cells = {cell["stage"]: cell for cell in north["cells"]}
        self.assertEqual(north["cells"][0]["count"], 2)
        self.assertEqual(north_cells["Application Review"]["count"], 2)
        self.assertEqual(len(north_cells["Application Review"]["applications"]), 2)
        self.assertEqual(north_cells["Completed"]["count"], 0)
        self.assertIn("Completed", body["stages"])
        self.assertIn("Unknown stage", body["stages"])
        self.assertEqual(rows["unknown"]["label"], "Unknown / missing region")
        self.assertTrue(rows["unknown"]["is_unknown"])
        unknown_cells = {cell["stage"]: cell for cell in rows["unknown"]["cells"]}
        self.assertEqual(unknown_cells["Unknown stage"]["count"], 1)
        self.assertEqual(body["total_applications"], 4)
        self.assertEqual(body["matching_applications"], 4)
        application_ids = [
            item["id"]
            for row in body["regions"]
            for cell in row["cells"]
            for item in cell["applications"]
        ]
        self.assertEqual(len(application_ids), 4)
        self.assertEqual(len(set(application_ids)), 4)
        self.assertEqual(
            sum(cell["count"] for row in body["regions"] for cell in row["cells"]),
            body["matching_applications"],
        )
        self.assertEqual(body["max_count"], 2)

    def test_overdue_and_high_risk_metrics_filter_applications(self):
        today = date(2026, 10, 25)
        self.add_application(
            "OVERDUE-HIGH",
            due_date=(today - timedelta(days=20)).isoformat(),
            document_expiry_date=(today - timedelta(days=1)).isoformat(),
        )
        self.add_application(
            "DUE-TODAY",
            due_date=today.isoformat(),
            region="South",
        )
        self.add_application(
            "OVERDUE-MODERATE",
            due_date=(today - timedelta(days=1)).isoformat(),
            region="South",
        )
        self.add_application(
            "COMPLETED-LATE",
            due_date=(today - timedelta(days=20)).isoformat(),
            status="Approved",
            stage="Completed",
        )
        self.add_application(
            "RISK-UNKNOWN",
            due_date="invalid-date",
            region="East",
        )
        headers = self.login()

        with patch("main.local_date", return_value=today):
            overdue = self.client.get(
                "/api/monitoring/heatmap?metric=overdue",
                headers=headers,
            )
            high_risk = self.client.get(
                "/api/monitoring/heatmap?metric=high_risk",
                headers=headers,
            )

        self.assertEqual(overdue.status_code, 200)
        overdue_cells = [
            cell
            for row in overdue.json()["regions"]
            for cell in row["cells"]
            if cell["count"]
        ]
        self.assertEqual(sum(cell["count"] for cell in overdue_cells), 2)
        overdue_ids = [
            application["id"]
            for cell in overdue_cells
            for application in cell["applications"]
        ]
        self.assertCountEqual(overdue_ids, ["OVERDUE-HIGH", "OVERDUE-MODERATE"])

        self.assertEqual(high_risk.status_code, 200)
        high_risk_body = high_risk.json()
        risk_cells = [
            cell
            for row in high_risk_body["regions"]
            for cell in row["cells"]
            if cell["count"]
        ]
        self.assertEqual(sum(cell["count"] for cell in risk_cells), 1)
        self.assertEqual(risk_cells[0]["applications"][0]["id"], "OVERDUE-HIGH")
        high_risk_ids = [
            application["id"]
            for row in high_risk_body["regions"]
            for cell in row["cells"]
            for application in cell["applications"]
        ]
        self.assertEqual(high_risk_body["matching_applications"], 1)
        self.assertEqual(high_risk_body["total_applications"], 5)
        self.assertEqual(
            sum(cell["count"] for row in high_risk_body["regions"] for cell in row["cells"]),
            high_risk_body["matching_applications"],
        )
        self.assertTrue(all(
            cell["count"] == len(cell["applications"])
            for row in high_risk_body["regions"]
            for cell in row["cells"]
        ))
        self.assertEqual(high_risk_ids, ["OVERDUE-HIGH"])
        self.assertNotIn("OVERDUE-MODERATE", high_risk_ids)
        self.assertEqual(len(high_risk_ids), len(set(high_risk_ids)))

    def test_empty_dataset_returns_configured_stages_and_no_rows(self):
        response = self.client.get(
            "/api/monitoring/heatmap",
            headers=self.login(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["regions"], [])
        self.assertEqual(body["total_applications"], 0)
        self.assertEqual(body["matching_applications"], 0)
        self.assertEqual(body["max_count"], 0)
        self.assertEqual(
            body["stages"],
            list(dict.fromkeys(
                stage
                for route in main.WORKFLOW_ROUTES.values()
                for stage in route
            )),
        )

    def test_invalid_metric_is_rejected(self):
        response = self.client.get(
            "/api/monitoring/heatmap?metric=processing_time",
            headers=self.login(),
        )

        self.assertEqual(response.status_code, 400)

    def test_heatmap_requires_authentication(self):
        self.assertEqual(
            self.client.get("/api/monitoring/heatmap").status_code,
            401,
        )

    def test_created_application_appears_in_heatmap(self):
        headers = self.login("admin")
        created = self.client.post(
            "/api/applications",
            headers=headers,
            json={
                "id": "HEAT-CREATED",
                "institution": "Created Institute",
                "region": "West",
                "officer": "Test Officer",
                "date": date.today().isoformat(),
            },
        )
        self.assertEqual(created.status_code, 201)

        heatmap = self.get_heatmap(headers)
        self.assertEqual(
            self.cell_count(heatmap, "region:west", "Application Review"),
            1,
        )
        route_change = self.client.post(
            "/api/applications/HEAT-CREATED/workflow",
            headers=headers,
            json={"workflow_route": "SCRUTINY_EVC"},
        )
        self.assertEqual(route_change.status_code, 200)
        heatmap = self.get_heatmap(headers)
        self.assertEqual(
            self.cell_count(heatmap, "region:west", "Application Review"),
            0,
        )
        self.assertEqual(
            self.cell_count(heatmap, "region:west", "Scrutiny"),
            1,
        )

    def test_stage_changes_and_return_keep_applications_in_their_current_stage(self):
        application = self.add_application("HEAT-STAGES")
        headers = self.login("officer")

        self.assertEqual(
            self.cell_count(self.get_heatmap(headers), "region:north", "Application Review"),
            1,
        )
        advanced = self.client.post(
            f"/api/applications/{application['id']}/advance",
            headers=headers,
        )
        self.assertEqual(advanced.status_code, 200)
        self.assertEqual(
            self.cell_count(self.get_heatmap(headers), "region:north", "Application Review"),
            0,
        )
        self.assertEqual(
            self.cell_count(self.get_heatmap(headers), "region:north", "Document Verification"),
            1,
        )

        returned = self.client.post(
            f"/api/applications/{application['id']}/return",
            headers=headers,
            json={"reason": "Please correct the application"},
        )
        self.assertEqual(returned.status_code, 200)
        self.assertEqual(
            self.cell_count(self.get_heatmap(headers), "region:north", "Document Verification"),
            1,
        )
        resubmitted = self.client.post(
            f"/api/applications/{application['id']}/resubmit",
            headers=headers,
        )
        self.assertEqual(resubmitted.status_code, 200)

        advanced = self.client.post(
            f"/api/applications/{application['id']}/advance",
            headers=headers,
        )
        self.assertEqual(advanced.status_code, 200)
        self.assertEqual(
            self.cell_count(self.get_heatmap(headers), "region:north", "Document Verification"),
            0,
        )
        self.assertEqual(
            self.cell_count(self.get_heatmap(headers), "region:north", "Final Approval"),
            1,
        )

    def test_saved_region_change_moves_application_to_new_region_row(self):
        application = self.add_application("HEAT-REGION", region="North")
        headers = self.login()
        application["region"] = "South"
        main.save_application(application)

        heatmap = self.get_heatmap(headers)
        self.assertNotIn("region:north", {row["key"] for row in heatmap["regions"]})
        self.assertEqual(
            self.cell_count(heatmap, "region:south", "Application Review"),
            1,
        )

    def test_compliance_date_changes_update_overdue_and_high_risk_metrics(self):
        today = date(2026, 10, 25)
        application = self.add_application("HEAT-RISK")
        headers = self.login("officer")

        with patch("main.local_date", return_value=today):
            before_overdue = self.get_heatmap(headers, "overdue")
            before_risk = self.get_heatmap(headers, "high_risk")
            self.assertEqual(before_overdue["max_count"], 0)
            self.assertEqual(before_risk["max_count"], 0)

            updated = self.client.patch(
                f"/api/applications/{application['id']}/compliance",
                headers=headers,
                json={
                    "due_date": (today - timedelta(days=20)).isoformat(),
                    "document_expiry_date": (today - timedelta(days=1)).isoformat(),
                },
            )
            self.assertEqual(updated.status_code, 200)

            after_overdue = self.get_heatmap(headers, "overdue")
            after_risk = self.get_heatmap(headers, "high_risk")

        self.assertEqual(
            self.cell_count(after_overdue, "region:north", "Application Review"),
            1,
        )
        self.assertEqual(
            self.cell_count(after_risk, "region:north", "Application Review"),
            1,
        )

    def test_failed_mutation_leaves_heatmap_unchanged(self):
        application = self.add_application("HEAT-FAILED")
        headers = self.login("officer")
        before = self.get_heatmap(headers)

        failed = self.client.post(
            f"/api/applications/{application['id']}/return",
            headers=headers,
            json={"reason": " "},
        )

        self.assertEqual(failed.status_code, 400)
        after = self.get_heatmap(headers)
        self.assertEqual(after["regions"], before["regions"])


if __name__ == "__main__":
    unittest.main()
