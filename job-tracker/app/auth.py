"""User authentication helpers and login_required decorator."""

from __future__ import annotations

import sqlite3
from functools import wraps
from typing import Any

from flask import redirect, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app.utils import utc_now


def create_user(connection: sqlite3.Connection, email: str, password: str) -> None:
    connection.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        (email.lower().strip(), generate_password_hash(password), utc_now()),
    )


def get_user_by_email(connection: sqlite3.Connection, email: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
    ).fetchone()
    return dict(row) if row else None


def get_user_by_id(connection: sqlite3.Connection, user_id: int) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def verify_password(stored_hash: str, password: str) -> bool:
    return check_password_hash(stored_hash, password)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login_route"))
        return f(*args, **kwargs)
    return decorated
