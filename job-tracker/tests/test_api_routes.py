from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from app import create_app
from app.auth import create_user
from app.database import connect_db


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
        # Create a test user and log in
        conn = connect_db(self.app)
        create_user(conn, "test@example.com", "password123")
        conn.commit()
        conn.close()

        self.client = self.app.test_client()
        self.client.post("/login", data={"email": "test@example.com", "password": "password123"})

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_api_create_and_list_application(self) -> None:
        response = self.client.post(
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

        list_response = self.client.get("/api/applications")
        self.assertEqual(list_response.status_code, 200)
        applications = list_response.get_json()
        self.assertEqual(len(applications), 1)
        self.assertEqual(applications[0]["role"], "Developer Advocate")

    def test_api_delete_application(self) -> None:
        created = self.client.post(
            "/api/applications",
            json={
                "company": "Figma",
                "role": "Designer",
                "status": "Applied",
                "applied_date": "2026-04-28",
            },
        ).get_json()

        delete_response = self.client.delete(f"/api/applications/{created['id']}")
        self.assertEqual(delete_response.status_code, 200)

        empty_response = self.client.get("/api/applications")
        self.assertEqual(empty_response.get_json(), [])

    def test_api_list_sorting(self) -> None:
        self.client.post(
            "/api/applications",
            json={
                "company": "B Company",
                "role": "Role 1",
                "status": "Applied",
                "applied_date": "2026-04-20",
            },
        )
        self.client.post(
            "/api/applications",
            json={
                "company": "A Company",
                "role": "Role 2",
                "status": "Applied",
                "applied_date": "2026-04-21",
            },
        )

        resp = self.client.get("/api/applications")
        apps = resp.get_json()
        self.assertEqual(apps[0]["company"], "A Company")
        self.assertEqual(apps[1]["company"], "B Company")

        resp = self.client.get("/api/applications?sort_by=company&order=asc")
        apps = resp.get_json()
        self.assertEqual(apps[0]["company"], "A Company")
        self.assertEqual(apps[1]["company"], "B Company")

        resp = self.client.get("/api/applications?sort_by=company&order=desc")
        apps = resp.get_json()
        self.assertEqual(apps[0]["company"], "B Company")
        self.assertEqual(apps[1]["company"], "A Company")

    def test_api_status_update(self) -> None:
        created = self.client.post(
            "/api/applications",
            json={
                "company": "Stripe",
                "role": "Backend Engineer",
                "status": "Applied",
                "applied_date": "2026-04-28",
            },
        ).get_json()

        update_response = self.client.put(
            f"/api/applications/{created['id']}",
            json={"status": "Interview Scheduled"},
        )
        self.assertEqual(update_response.status_code, 200)
        updated = update_response.get_json()
        self.assertEqual(updated["status"], "Interview Scheduled")

        # Verify the change persisted
        get_response = self.client.get(f"/api/applications/{created['id']}")
        self.assertEqual(get_response.get_json()["status"], "Interview Scheduled")

    def test_unauthenticated_access_redirects(self) -> None:
        # Verify protected routes require login
        with self.app.test_client() as anon:
            resp = anon.get("/api/applications")
            self.assertEqual(resp.status_code, 302)
            resp = anon.post("/api/applications", json={"company": "X", "role": "Y"})
            self.assertEqual(resp.status_code, 302)


if __name__ == "__main__":
    unittest.main()
