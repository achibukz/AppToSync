"""Flask routes for the Job Tracker application."""

import os
from datetime import date
from typing import Any

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

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
from app.gmail import (
    disconnect_gmail,
    finish_gmail_authorization,
    get_gmail_status,
    start_gmail_authorization,
    sync_gmail_messages,
)
from app.watchers import (
    delete_watchers_for_application,
    fetch_watchers_for_application,
    set_watchers_for_application,
)
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


def _render_dashboard(
    app: Flask,
    *,
    active_tab: str = "dashboard",
    parse_result: dict[str, Any] | None = None,
    email_text: str = "",
    selected_provider: str | None = None,
    selected_gemini_model: str | None = None,
) -> str:
    """Render the dashboard with shared data."""
    filters = {
        "status": request.args.get("status", "").strip(),
        "source": request.args.get("source", "").strip(),
        "search": request.args.get("search", "").strip(),
    }
    sort_by = request.args.get("sort_by", "applied_date").strip()
    order = request.args.get("order", "desc").strip()
    edit_id = request.args.get("edit", "").strip() or None

    applications = fetch_applications(app, filters, sort_by=sort_by, order=order)
    editing_application = fetch_application(app, edit_id) if edit_id else None
    stats = build_stats(applications)
    gmail_status = get_gmail_status(app)

    return render_template(
        "index.html",
        applications=applications,
        stats=stats,
        filters=filters,
        sort_by=sort_by,
        order=order,
        status_options=STATUS_OPTIONS,
        source_options=SOURCE_OPTIONS,
        source_type_options=SOURCE_TYPE_OPTIONS,
        status_styles=STATUS_STYLES,
        editing_application=editing_application,
        parse_result=parse_result,
        email_text=email_text,
        selected_provider=selected_provider or os.getenv("AI_PROVIDER", "local").strip(),
        selected_gemini_model=selected_gemini_model
        or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip(),
        gemini_model_options=GEMINI_MODEL_OPTIONS,
        active_tab=active_tab,
        today=date.today().isoformat(),
        default_currency="PHP",
        gmail_status=gmail_status,
    )


def register_routes(app: Flask) -> None:
    """Register all Flask routes.
    
    Args:
        app: Flask application instance
    """
    
    # ==================== Web Routes ====================
    
    @app.get("/")
    def dashboard() -> str:
        """Display the main dashboard with applications and filters."""
        return _render_dashboard(app)

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
            if updated is not None:
                patterns = request.form.getlist("watcher_patterns")
                set_watchers_for_application(connection, application_id, patterns)
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
        """Delete an application and its watchers."""
        connection = connect_db(app)
        try:
            delete_watchers_for_application(connection, application_id)
            connection.commit()
        finally:
            connection.close()
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
        return _render_dashboard(
            app,
            active_tab="parser",
            parse_result=parse_result,
            email_text=email_text,
            selected_provider=provider,
            selected_gemini_model=parse_result.get("gemini_model", gemini_model),
        )

    @app.get("/gmail/connect")
    def gmail_connect_route() -> Any:
        """Start the Gmail OAuth flow."""
        try:
            authorization_url, state, code_verifier = start_gmail_authorization()
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard"))

        session["gmail_oauth_state"] = state
        if code_verifier:
            session["gmail_oauth_code_verifier"] = code_verifier
        return redirect(authorization_url)

    @app.get("/gmail/callback")
    def gmail_callback_route() -> Any:
        """Complete the Gmail OAuth flow."""
        state = session.pop("gmail_oauth_state", None)
        code_verifier = session.pop("gmail_oauth_code_verifier", None)
        result = finish_gmail_authorization(app, request.url, state, code_verifier)
        if result["ok"]:
            email_address = result.get("email") or "your Gmail account"
            flash(f"Gmail connected for {email_address}.", "success")
        else:
            flash(result["error"], "error")
        return redirect(url_for("dashboard"))

    @app.post("/gmail/disconnect")
    def gmail_disconnect_route() -> Any:
        """Disconnect Gmail and clear stored tokens."""
        disconnect_gmail(app)
        flash("Gmail disconnected.", "success")
        return redirect(url_for("dashboard"))

    @app.get("/gmail/status")
    def gmail_status_route() -> Any:
        """Return Gmail connection status as JSON."""
        return jsonify(get_gmail_status(app))

    @app.post("/gmail/sync")
    def gmail_sync_route() -> Any:
        """Synchronize Gmail messages on demand."""
        result = sync_gmail_messages(app)
        if result["ok"]:
            flash(
                f"Gmail sync complete: {result['created']} created, {result['updated']} updated, {result['skipped']} skipped.",
                "success",
            )
        else:
            flash(result["error"], "error")
        return redirect(url_for("dashboard"))

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
        sort_by = request.args.get("sort_by", "applied_date").strip()
        order = request.args.get("order", "desc").strip()
        return jsonify(fetch_applications(app, filters, sort_by=sort_by, order=order))

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
        """Delete an application and its watchers."""
        connection = connect_db(app)
        try:
            delete_watchers_for_application(connection, application_id)
            connection.commit()
        finally:
            connection.close()
        deleted = delete_application(app, application_id)
        if not deleted:
            return jsonify({"error": "not found"}), 404
        return jsonify({"success": True})

    @app.get("/api/applications/<application_id>/watchers")
    def api_get_watchers(application_id: str) -> Any:
        """Return the sender patterns registered for an application."""
        connection = connect_db(app)
        try:
            patterns = fetch_watchers_for_application(connection, application_id)
        finally:
            connection.close()
        return jsonify(patterns)
