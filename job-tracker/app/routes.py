"""Flask routes for the Job Tracker application."""

import os
from datetime import date
from typing import Any

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from app.config import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_MODEL_OPTIONS,
    SOURCE_OPTIONS,
    SOURCE_TYPE_OPTIONS,
    STATUS_OPTIONS,
    STATUS_STYLES,
)
from app.database import connect_db
from app.email_parser import parse_job_email
from app.models import (
    delete_application,
    fetch_application,
    fetch_applications,
    insert_application,
    update_application,
)
from app.utils import utc_now
from app.validators import form_payload, normalize_payload


def build_stats(applications: list[dict[str, Any]]) -> dict[str, int]:
    """Build statistics from applications list.
    
    Args:
        applications: List of application dictionaries
        
    Returns:
        Dictionary with total, active, interviews, and overdue counts
    """
    active_statuses = {"Applied", "Interview Scheduled", "Technical Test", "Final Interview"}
    return {
        "total": len(applications),
        "active": sum(1 for application in applications if application["status"] in active_statuses),
        "interviews": sum(1 for application in applications if application["status"] == "Interview Scheduled"),
        "overdue": sum(1 for application in applications if application["is_overdue"]),
    }


def register_routes(app: Flask) -> None:
    """Register all Flask routes.
    
    Args:
        app: Flask application instance
    """
    
    # ==================== Web Routes ====================
    
    @app.get("/")
    def dashboard() -> str:
        """Display the main dashboard with applications and filters."""
        filters = {
            "status": request.args.get("status", "").strip(),
            "source": request.args.get("source", "").strip(),
            "search": request.args.get("search", "").strip(),
        }
        edit_id = request.args.get("edit", "").strip() or None
        applications = fetch_applications(app, filters)
        editing_application = fetch_application(app, edit_id) if edit_id else None
        stats = build_stats(applications)
        return render_template(
            "index.html",
            applications=applications,
            stats=stats,
            filters=filters,
            status_options=STATUS_OPTIONS,
            source_options=SOURCE_OPTIONS,
            source_type_options=SOURCE_TYPE_OPTIONS,
            status_styles=STATUS_STYLES,
            editing_application=editing_application,
            parse_result=None,
            email_text="",
            selected_provider=os.getenv("AI_PROVIDER", "local").strip(),
            selected_gemini_model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip(),
            gemini_model_options=GEMINI_MODEL_OPTIONS,
            active_tab="dashboard",
            today=date.today().isoformat(),
            default_currency="PHP",
        )

    @app.post("/applications")
    def create_application_route() -> Any:
        """Create a new application from form data."""
        connection = connect_db(app)
        try:
            payload = form_payload(request.form)
            insert_application(connection, payload, utc_now())
            connection.commit()
            flash("Application created.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        finally:
            connection.close()
        return redirect(url_for("dashboard"))

    @app.post("/applications/<application_id>/update")
    def update_application_route(application_id: str) -> Any:
        """Update an existing application from form data."""
        connection = connect_db(app)
        try:
            payload = form_payload(request.form)
            updated = update_application(connection, application_id, payload)
            connection.commit()
            if updated is None:
                flash("Application not found.", "error")
            else:
                flash("Application updated.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        finally:
            connection.close()
        return redirect(url_for("dashboard"))

    @app.post("/applications/<application_id>/delete")
    def delete_application_route(application_id: str) -> Any:
        """Delete an application."""
        delete_application(app, application_id)
        flash("Application deleted.", "success")
        return redirect(url_for("dashboard"))

    @app.post("/parse-email")
    def parse_email_route() -> str:
        """Parse email and display results."""
        email_text = request.form.get("email_text", "").strip()
        provider = request.form.get("provider", os.getenv("AI_PROVIDER", "local")).strip()
        gemini_model = request.form.get(
            "gemini_model",
            os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        ).strip()
        parse_result = parse_job_email(email_text, provider=provider, gemini_model=gemini_model)
        active_tab = "parser"
        filters = {
            "status": request.args.get("status", "").strip(),
            "source": request.args.get("source", "").strip(),
            "search": request.args.get("search", "").strip(),
        }
        applications = fetch_applications(app, filters)
        stats = build_stats(applications)
        return render_template(
            "index.html",
            applications=applications,
            stats=stats,
            filters=filters,
            status_options=STATUS_OPTIONS,
            source_options=SOURCE_OPTIONS,
            source_type_options=SOURCE_TYPE_OPTIONS,
            status_styles=STATUS_STYLES,
            editing_application=None,
            parse_result=parse_result,
            email_text=email_text,
            selected_provider=provider,
            selected_gemini_model=parse_result.get("gemini_model", gemini_model),
            gemini_model_options=GEMINI_MODEL_OPTIONS,
            active_tab=active_tab,
            today=date.today().isoformat(),
            default_currency="PHP",
        )

    # ==================== API Routes ====================

    @app.get("/api/health")
    def health() -> Any:
        """Health check endpoint."""
        return jsonify({"status": "ok", "service": "job-tracker"})

    @app.get("/api/applications")
    def api_list_applications() -> Any:
        """List all applications (with optional filtering)."""
        filters = {
            "status": request.args.get("status", "").strip(),
            "source": request.args.get("source", "").strip(),
            "search": request.args.get("search", "").strip(),
        }
        return jsonify(fetch_applications(app, filters))

    @app.post("/api/applications")
    def api_create_application() -> Any:
        """Create a new application from JSON payload."""
        payload = request.get_json(silent=True) or {}
        connection = connect_db(app)
        try:
            normalized = normalize_payload(payload)
            created = insert_application(connection, normalized, utc_now())
            connection.commit()
        finally:
            connection.close()
        return jsonify(created), 201

    @app.get("/api/applications/<application_id>")
    def api_get_application(application_id: str) -> Any:
        """Get a single application by ID."""
        application = fetch_application(app, application_id)
        if application is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(application)

    @app.put("/api/applications/<application_id>")
    def api_update_application(application_id: str) -> Any:
        """Update an application from JSON payload."""
        payload = request.get_json(silent=True) or {}
        connection = connect_db(app)
        try:
            normalized = normalize_payload(payload, partial=True)
            updated = update_application(connection, application_id, normalized, partial=True)
            connection.commit()
        finally:
            connection.close()
        if updated is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(updated)

    @app.delete("/api/applications/<application_id>")
    def api_delete_application(application_id: str) -> Any:
        """Delete an application."""
        deleted = delete_application(app, application_id)
        if not deleted:
            return jsonify({"error": "not found"}), 404
        return jsonify({"success": True})
