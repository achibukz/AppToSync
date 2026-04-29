"""Flask routes for the Job Tracker application."""

from datetime import date
from typing import Any

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

from app.config import (
    SOURCE_OPTIONS,
    SOURCE_TYPE_OPTIONS,
    STATUS_OPTIONS,
    STATUS_STYLES,
)
from app.database import connect_db
from app.gmail import (
    clear_sync_summary,
    disconnect_gmail,
    finish_gmail_authorization,
    get_gmail_status,
    retry_parse_email,
    start_gmail_authorization,
    sync_gmail_messages,
)
from app.parsed_emails import (
    fetch_email,
    fetch_paused,
    fetch_pending_review,
    mark_accepted,
    mark_dismissed,
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
from app.utils import format_list, utc_now
from app.validators import form_payload, normalize_payload


def _email_note(record: dict[str, Any]) -> str:
    """Build the seed note for an application accepted from a parsed email."""
    parts = ["Gmail sync"]
    subject = record.get("subject")
    if subject:
        parts.append(subject)
    parsed_status = record.get("parsed_status")
    if parsed_status:
        parts.append(f"Parsed status: {parsed_status}")
    return " | ".join(parts)


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

    # If there's a pending summary from an auto-sync or manual sync, flash it now.
    if gmail_status.get("last_sync_summary"):
        flash(gmail_status["last_sync_summary"], "success")
        clear_sync_summary(app)

    connection = connect_db(app)
    try:
        pending_emails = fetch_pending_review(connection)
        paused_emails = fetch_paused(connection)
    finally:
        connection.close()

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
        active_tab=active_tab,
        today=date.today().isoformat(),
        default_currency="PHP",
        gmail_status=gmail_status,
        pending_emails=pending_emails,
        paused_emails=paused_emails,
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
        tab = request.args.get("tab", "").strip().lower()
        active_tab = "emails" if tab == "emails" else "dashboard"
        return _render_dashboard(app, active_tab=active_tab)

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

    @app.post("/emails/<message_id>/accept")
    def accept_email_route(message_id: str) -> Any:
        """Accept a queued email and create a new application from it."""
        connection = connect_db(app)
        try:
            record = fetch_email(connection, message_id)
            if record is None:
                flash("Email not found.", "error")
                return redirect(url_for("dashboard", tab="emails"))
            if record.get("parse_status") not in {"pending_review", "paused"}:
                flash("This email is not awaiting review.", "error")
                return redirect(url_for("dashboard", tab="emails"))

            company = (request.form.get("company") or record.get("parsed_company") or "").strip()
            role = (request.form.get("role") or record.get("parsed_role") or "").strip()
            if not company or not role:
                flash("Company and role are required to accept this email.", "error")
                return redirect(url_for("dashboard", tab="emails"))

            applied_date = (
                request.form.get("applied_date")
                or (record.get("received_at") or "").split("T")[0]
                or date.today().isoformat()
            )
            payload = {
                "company": company,
                "role": role,
                "job_url": request.form.get("job_url") or None,
                "source": request.form.get("source") or "Other",
                "status": request.form.get("status") or record.get("parsed_status") or "Applied",
                "applied_date": applied_date,
                "salary_min": request.form.get("salary_min") or None,
                "salary_max": request.form.get("salary_max") or None,
                "salary_currency": request.form.get("salary_currency") or "PHP",
                "notes": request.form.get("notes") or _email_note(record),
                "follow_up_date": request.form.get("follow_up_date") or None,
                "source_type": "gmail",
                "gmail_message_id": message_id,
            }
            normalized = normalize_payload(payload)
            created = insert_application(connection, normalized, utc_now())
            mark_accepted(connection, message_id, created["id"])
            connection.commit()
            flash(f"Application created from email: {company} — {role}.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        finally:
            connection.close()
        return redirect(url_for("dashboard", tab="emails"))

    @app.post("/emails/<message_id>/reject")
    def reject_email_route(message_id: str) -> Any:
        """Dismiss a queued email; it will not surface again."""
        connection = connect_db(app)
        try:
            mark_dismissed(connection, message_id)
            connection.commit()
            flash("Email dismissed.", "success")
        finally:
            connection.close()
        return redirect(url_for("dashboard", tab="emails"))

    @app.post("/emails/<message_id>/retry")
    def retry_email_route(message_id: str) -> Any:
        """Force a paused email back through the Gemini parser."""
        result = retry_parse_email(app, message_id)
        if not result["ok"]:
            flash(result.get("error") or "Retry failed.", "error")
        elif result["parse_status"] == "paused":
            flash(f"Parse still failing: {result.get('error') or 'unknown error'}", "error")
        else:
            flash(f"Email re-parsed → {result['parse_status']}.", "success")
        return redirect(url_for("dashboard", tab="emails"))

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
            # The dashboard (_render_dashboard) will pick up the last_sync_summary
            # and flash it automatically upon redirect.
            flash(
                f"Gmail sync complete: {result.get('fetched', 0)} fetched, "
                f"{result.get('pending_review', 0)} pending review, "
                f"{result.get('paused', 0)} paused.",
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

    @app.get("/api/emails")
    def api_list_emails() -> Any:
        """Return parsed emails grouped by review state."""
        connection = connect_db(app)
        try:
            return jsonify({
                "pending_review": fetch_pending_review(connection),
                "paused": fetch_paused(connection),
            })
        finally:
            connection.close()

    @app.get("/api/emails/<message_id>")
    def api_get_email(message_id: str) -> Any:
        """Return a single parsed-email record."""
        connection = connect_db(app)
        try:
            record = fetch_email(connection, message_id)
        finally:
            connection.close()
        if record is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(record)
