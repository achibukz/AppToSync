"""Database initialization and connection management."""

import sqlite3
from pathlib import Path

from flask import Flask
from werkzeug.security import generate_password_hash

OWNER_EMAIL = "akibukzwork@gmail.com"
OWNER_DEFAULT_PASSWORD = "JobPilot2026!"


def get_db_path(app: Flask) -> Path:
    return app.config["DATABASE_PATH"]


def connect_db(app: Flask) -> sqlite3.Connection:
    connection = sqlite3.connect(get_db_path(app), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def init_db(app: Flask) -> None:
    connection = connect_db(app)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        # Per-user Gmail tokens (replaces the old singleton gmail_connections table).
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS gmail_tokens (
                user_id INTEGER PRIMARY KEY,
                credentials_json TEXT NOT NULL,
                connected_email TEXT,
                connected_at TEXT NOT NULL,
                last_sync_at TEXT,
                last_sync_error TEXT,
                sync_interval_minutes INTEGER NOT NULL DEFAULT 15,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        # Keep old table definition so migration can read it on existing DBs.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS gmail_connections (
                id INTEGER PRIMARY KEY,
                credentials_json TEXT NOT NULL,
                connected_email TEXT,
                connected_at TEXT NOT NULL,
                last_sync_at TEXT,
                last_sync_error TEXT,
                sync_interval_minutes INTEGER NOT NULL DEFAULT 15,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id TEXT PRIMARY KEY,
                company TEXT NOT NULL,
                role TEXT NOT NULL,
                job_url TEXT,
                source TEXT,
                status TEXT NOT NULL,
                applied_date TEXT NOT NULL,
                salary_min REAL,
                salary_max REAL,
                salary_currency TEXT,
                notes TEXT,
                follow_up_date TEXT,
                source_type TEXT,
                gmail_message_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
            CREATE INDEX IF NOT EXISTS idx_applications_company ON applications(company);
            CREATE INDEX IF NOT EXISTS idx_applications_applied_date ON applications(applied_date);
            """
        )

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_gmail_message_id
                ON applications(gmail_message_id)
                WHERE gmail_message_id IS NOT NULL
            """
        )

        connection.execute("DROP TABLE IF EXISTS company_watchers")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS application_watchers (
                id TEXT PRIMARY KEY,
                application_id TEXT NOT NULL,
                sender_pattern TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_app_watchers_application_id
                ON application_watchers(application_id)
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS parsed_emails (
                gmail_message_id TEXT PRIMARY KEY,
                received_at TEXT,
                from_address TEXT,
                subject TEXT,
                body_text TEXT,
                parse_status TEXT NOT NULL,
                parse_error TEXT,
                parse_attempts INTEGER NOT NULL DEFAULT 0,
                last_parsed_at TEXT,
                is_job_related INTEGER,
                parsed_company TEXT,
                parsed_role TEXT,
                parsed_status TEXT,
                parsed_confidence REAL,
                parsed_reasoning TEXT,
                application_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_parsed_emails_status ON parsed_emails(parse_status)"
        )

        # Run column migrations after all tables exist.
        _migrate_add_columns(connection)

        connection.commit()

        _seed_owner_and_migrate(connection)
        connection.commit()
    finally:
        connection.close()


def _migrate_add_columns(connection: sqlite3.Connection) -> None:
    app_cols = {r["name"] for r in connection.execute("PRAGMA table_info(applications)").fetchall()}
    if "gmail_message_id" not in app_cols:
        connection.execute("ALTER TABLE applications ADD COLUMN gmail_message_id TEXT")
    if "user_id" not in app_cols:
        connection.execute("ALTER TABLE applications ADD COLUMN user_id INTEGER REFERENCES users(id)")

    email_cols = {r["name"] for r in connection.execute("PRAGMA table_info(parsed_emails)").fetchall()}
    if "user_id" not in email_cols:
        connection.execute("ALTER TABLE parsed_emails ADD COLUMN user_id INTEGER REFERENCES users(id)")


def _seed_owner_and_migrate(connection: sqlite3.Connection) -> None:
    from app.utils import utc_now

    owner = connection.execute("SELECT id FROM users WHERE email = ?", (OWNER_EMAIL,)).fetchone()
    if owner is None:
        connection.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
            (OWNER_EMAIL, generate_password_hash(OWNER_DEFAULT_PASSWORD), utc_now()),
        )
        owner = connection.execute("SELECT id FROM users WHERE email = ?", (OWNER_EMAIL,)).fetchone()

    owner_id = owner["id"]

    connection.execute("UPDATE applications SET user_id = ? WHERE user_id IS NULL", (owner_id,))
    connection.execute("UPDATE parsed_emails SET user_id = ? WHERE user_id IS NULL", (owner_id,))

    # One-time migration: copy gmail_connections row → gmail_tokens for owner.
    gmail_row = connection.execute("SELECT * FROM gmail_connections WHERE id = 1").fetchone()
    if gmail_row:
        exists = connection.execute(
            "SELECT user_id FROM gmail_tokens WHERE user_id = ?", (owner_id,)
        ).fetchone()
        if exists is None:
            connection.execute(
                """
                INSERT INTO gmail_tokens (
                    user_id, credentials_json, connected_email, connected_at, last_sync_at,
                    last_sync_error, sync_interval_minutes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_id,
                    gmail_row["credentials_json"],
                    gmail_row["connected_email"],
                    gmail_row["connected_at"],
                    gmail_row["last_sync_at"],
                    gmail_row["last_sync_error"],
                    gmail_row["sync_interval_minutes"],
                    gmail_row["created_at"],
                    gmail_row["updated_at"],
                ),
            )
