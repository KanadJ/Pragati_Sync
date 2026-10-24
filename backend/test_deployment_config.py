import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
