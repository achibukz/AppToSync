from __future__ import annotations

import csv
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import create_app
from app.auth import create_user
from app.database import connect_db


def _create_app():
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "test.db"
    app = create_app({"TESTING": True, "DATABASE_PATH": db_path, "SEED_DEMO_DATA": False})
    conn = connect_db(app)
    create_user(conn, "test@example.com", "password123")
    conn.commit()
    conn.close()
    return app


def _post_app(client, **kwargs):
    defaults = {
        "company": "Acme",
        "role": "Engineer",
        "status": "Applied",
        "applied_date": "2026-04-28",
        "source": "LinkedIn",
        "source_type": "manual",
    }
    defaults.update(kwargs)
    return client.post("/api/applications", json=defaults)


class ExportCsvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app({"TESTING": True, "DATABASE_PATH": db_path, "SEED_DEMO_DATA": False})
        conn = connect_db(self.app)
        create_user(conn, "test@example.com", "password123")
        conn.commit()
        conn.close()
        self.client = self.app.test_client()
        self.client.post("/login", data={"email": "test@example.com", "password": "password123"})

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _parse_csv(self, response) -> list[dict]:
        text = response.data.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        return list(reader)

    def test_export_requires_login(self) -> None:
        with self.app.test_client() as anon:
            resp = anon.get("/export/csv")
            self.assertEqual(resp.status_code, 302)

    def test_export_empty(self) -> None:
        resp = self.client.get("/export/csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.content_type)
        rows = self._parse_csv(resp)
        self.assertEqual(rows, [])

    def test_export_content_disposition(self) -> None:
        resp = self.client.get("/export/csv")
        disposition = resp.headers.get("Content-Disposition", "")
        today = date.today().isoformat()
        self.assertIn(f"applications_{today}.csv", disposition)
        self.assertIn("attachment", disposition)

    def test_export_all_columns_present(self) -> None:
        _post_app(self.client)
        resp = self.client.get("/export/csv")
        rows = self._parse_csv(resp)
        self.assertEqual(len(rows), 1)
        expected_cols = {
            "id", "company", "role", "status", "applied_date", "source",
            "follow_up_date", "salary_min", "salary_max", "salary_currency",
            "notes", "job_url", "source_type", "created_at", "updated_at",
        }
        self.assertEqual(expected_cols, set(rows[0].keys()))

    def test_export_data_matches(self) -> None:
        _post_app(self.client, company="Stripe", role="Backend Engineer", status="Interview Scheduled")
        resp = self.client.get("/export/csv")
        rows = self._parse_csv(resp)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "Stripe")
        self.assertEqual(rows[0]["role"], "Backend Engineer")
        self.assertEqual(rows[0]["status"], "Interview Scheduled")

    def test_export_respects_status_filter(self) -> None:
        _post_app(self.client, company="Alpha", status="Applied")
        _post_app(self.client, company="Beta", status="Rejected")
        resp = self.client.get("/export/csv?status=Applied")
        rows = self._parse_csv(resp)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "Alpha")

    def test_export_respects_source_filter(self) -> None:
        _post_app(self.client, company="Alpha", source="LinkedIn")
        _post_app(self.client, company="Beta", source="Indeed")
        resp = self.client.get("/export/csv?source=Indeed")
        rows = self._parse_csv(resp)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "Beta")

    def test_export_respects_search_filter(self) -> None:
        _post_app(self.client, company="DeepMind", role="Researcher")
        _post_app(self.client, company="OpenAI", role="Engineer")
        resp = self.client.get("/export/csv?search=deepmind")
        rows = self._parse_csv(resp)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "DeepMind")

    def test_export_multiple_rows(self) -> None:
        for i in range(3):
            _post_app(self.client, company=f"Company {i}")
        resp = self.client.get("/export/csv")
        rows = self._parse_csv(resp)
        self.assertEqual(len(rows), 3)

    def test_export_user_isolation(self) -> None:
        # Add an application for the primary user
        _post_app(self.client, company="Private Corp")

        # Create a second user and log in with a fresh client
        conn = connect_db(self.app)
        create_user(conn, "other@example.com", "password123")
        conn.commit()
        conn.close()

        with self.app.test_client() as other_client:
            other_client.post("/login", data={"email": "other@example.com", "password": "password123"})
            resp = other_client.get("/export/csv")
            rows = self._parse_csv(resp)
            self.assertEqual(rows, [], "Second user must not see first user's data")

    def test_existing_api_list_unaffected(self) -> None:
        _post_app(self.client, company="Figma")
        resp = self.client.get("/api/applications")
        self.assertEqual(resp.status_code, 200)
        apps = resp.get_json()
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0]["company"], "Figma")

    def test_existing_create_unaffected(self) -> None:
        resp = _post_app(self.client, company="Notion")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json()["company"], "Notion")

    def test_existing_delete_unaffected(self) -> None:
        created = _post_app(self.client, company="Dropbox").get_json()
        del_resp = self.client.delete(f"/api/applications/{created['id']}")
        self.assertEqual(del_resp.status_code, 200)
        self.assertEqual(self.client.get("/api/applications").get_json(), [])

    def test_existing_update_unaffected(self) -> None:
        created = _post_app(self.client, company="Slack", status="Applied").get_json()
        upd = self.client.put(f"/api/applications/{created['id']}", json={"status": "Rejected"})
        self.assertEqual(upd.status_code, 200)
        self.assertEqual(upd.get_json()["status"], "Rejected")


if __name__ == "__main__":
    unittest.main()
