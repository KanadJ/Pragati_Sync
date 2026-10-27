import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import main


class DeploymentConfigurationTests(unittest.TestCase):
    def test_cors_keeps_development_origins_and_adds_render_origin(self):
        origins = main.parse_cors_origins(
            " https://aicte-demo.onrender.com/ , https://teammate.example "
        )

        self.assertEqual(
            origins,
            [
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://localhost:5174",
                "http://127.0.0.1:5174",
                "https://aicte-approval-frontend.onrender.com",
                "https://aicte-demo.onrender.com",
                "https://teammate.example",
            ],
        )

    def test_cors_rejects_wildcard_and_non_origin_urls(self):
        for value in (
            "*",
            "https://example.com/path",
            "ftp://example.com",
            "https://user:password@example.com",
            "https://example.com:invalid",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                main.parse_cors_origins(value)

    def test_workflow_rules_path_is_relative_to_backend_source(self):
        expected_path = Path(main.__file__).resolve().parent / "routing_rules.json"
        self.assertEqual(main.ROUTING_RULES_PATH, expected_path)

    def test_demo_session_preflight_allows_vite_fallback_frontend_origin(self):
        frontend_origin = "http://127.0.0.1:5174"
        database_directory = tempfile.TemporaryDirectory()
        original_database_path = main.DATABASE_PATH
        main.DATABASE_PATH = Path(database_directory.name) / "preflight-test.db"
        try:
            with TestClient(main.app) as client:
                response = client.options(
                    "/api/auth/demo-session",
                    headers={
                        "Origin": frontend_origin,
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "authorization,content-type",
                    },
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.headers["access-control-allow-origin"],
                frontend_origin,
            )
            self.assertEqual(
                response.headers["access-control-allow-credentials"],
                "true",
            )
            self.assertIn("POST", response.headers["access-control-allow-methods"])
        finally:
            main.DATABASE_PATH = original_database_path
            database_directory.cleanup()


if __name__ == "__main__":
    unittest.main()
