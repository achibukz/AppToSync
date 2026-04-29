"""Database initialization and connection management."""

import sqlite3
from pathlib import Path

from flask import Flask


def get_db_path(app: Flask) -> Path:
    """Get the database path from Flask config."""
    return app.config["DATABASE_PATH"]


def connect_db(app: Flask) -> sqlite3.Connection:
    """Create and return a database connection.
    
    Args:
        app: Flask application instance
        
    Returns:
        sqlite3 connection with row_factory set to sqlite3.Row
    """
    connection = sqlite3.connect(get_db_path(app))
    connection.row_factory = sqlite3.Row
    return connection


def init_db(app: Flask) -> None:
    """Initialize the database schema.
    
    Creates the applications table and indexes if they don't exist.
    
    Args:
        app: Flask application instance
    """
    connection = connect_db(app)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS gmail_connections (
                id INTEGER PRIMARY KEY CHECK (id = 1),
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

        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(applications)").fetchall()
        }
        if "gmail_message_id" not in columns:
            connection.execute("ALTER TABLE applications ADD COLUMN gmail_message_id TEXT")

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_gmail_message_id
                ON applications(gmail_message_id)
                WHERE gmail_message_id IS NOT NULL
            """
        )

        # Remove old global-scope watcher table if it exists from a previous version.
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

        connection.commit()
    finally:
        connection.close()
