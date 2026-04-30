from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from app import create_app


class ApiRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": self.database_path,
                "SEED_DEMO_DATA": False,
            }
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_api_create_and_list_application(self) -> None:
        client = self.app.test_client()
        response = client.post(
            "/api/applications",
            json={
                "company": "OpenAI",
                "role": "Developer Advocate",
                "status": "Applied",
                "applied_date": "2026-04-28",
                "source": "Direct",
                "source_type": "manual",
            },
        )
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["company"], "OpenAI")

        list_response = client.get("/api/applications")
        self.assertEqual(list_response.status_code, 200)
        applications = list_response.get_json()
        self.assertEqual(len(applications), 1)
        self.assertEqual(applications[0]["role"], "Developer Advocate")

    def test_api_delete_application(self) -> None:
        client = self.app.test_client()
        created = client.post(
            "/api/applications",
            json={
                "company": "Figma",
                "role": "Designer",
                "status": "Applied",
                "applied_date": "2026-04-28",
            },
        ).get_json()

        delete_response = client.delete(f"/api/applications/{created['id']}")
        self.assertEqual(delete_response.status_code, 200)

        empty_response = client.get("/api/applications")
        self.assertEqual(empty_response.get_json(), [])

    def test_api_list_sorting(self) -> None:
        client = self.app.test_client()
        client.post(
            "/api/applications",
            json={
                "company": "B Company",
                "role": "Role 1",
                "status": "Applied",
                "applied_date": "2026-04-20",
            },
        )
        client.post(
            "/api/applications",
            json={
                "company": "A Company",
                "role": "Role 2",
                "status": "Applied",
                "applied_date": "2026-04-21",
            },
        )

        resp = client.get("/api/applications")
        apps = resp.get_json()
        self.assertEqual(apps[0]["company"], "A Company")
        self.assertEqual(apps[1]["company"], "B Company")

        resp = client.get("/api/applications?sort_by=company&order=asc")
        apps = resp.get_json()
        self.assertEqual(apps[0]["company"], "A Company")
        self.assertEqual(apps[1]["company"], "B Company")

        resp = client.get("/api/applications?sort_by=company&order=desc")
        apps = resp.get_json()
        self.assertEqual(apps[0]["company"], "B Company")
        self.assertEqual(apps[1]["company"], "A Company")


if __name__ == "__main__":
    unittest.main()
