"""Job Tracker Flask Application Factory."""

import os
from pathlib import Path
from typing import Any

from flask import Flask

from app.config import DEMO_APPLICATIONS
from app.database import connect_db, init_db
from app.extensions import limiter
from app.models import fetch_applications, insert_application
from app.routes import register_routes
from app.utils import utc_now


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "job_tracker.db"
ENV_PATH = BASE_DIR / ".env"


def load_env_file(path: Path) -> None:
    """Load environment variables from a .env file.
    
    Args:
        path: Path to .env file
    """
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    """Create and configure the Flask application.
    
    Args:
        test_config: Optional test configuration dictionary
        
    Returns:
        Configured Flask application instance
    """
    load_env_file(ENV_PATH)
    
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        if os.getenv("FLASK_ENV") == "production":
            raise RuntimeError("SECRET_KEY must be set in production")
        secret_key = "dev"

    app.config.from_mapping(
        SECRET_KEY=secret_key,
        DATABASE_PATH=Path(os.getenv("DATABASE_PATH", str(DEFAULT_DB_PATH))),
        SEED_DEMO_DATA=os.getenv("SEED_DEMO_DATA", "true").lower() in {"1", "true", "yes"},
        SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        TESTING=False,
    )
    if test_config:
        app.config.update(test_config)

    init_db(app)
    if not app.config.get("TESTING", False) and app.config.get("SEED_DEMO_DATA", False):
        seed_demo_data(app)

    limiter.init_app(app)
    register_routes(app)
    return app


def seed_demo_data(app: Flask) -> None:
    """Seed database with demo data for owner account if empty."""
    connection = connect_db(app)
    try:
        owner = connection.execute(
            "SELECT id FROM users WHERE email = 'akibukzwork@gmail.com'"
        ).fetchone()
        if owner is None:
            return
        owner_id = owner["id"]
        if fetch_applications(app, user_id=owner_id):
            return
        timestamp = utc_now()
        for payload in DEMO_APPLICATIONS:
            insert_application(connection, payload, timestamp, owner_id)
        connection.commit()
    finally:
        connection.close()
