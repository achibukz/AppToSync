"""Flask routes for the Job Tracker application."""

from datetime import date
from typing import Any

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

from app.auth import create_user, get_user_by_email, get_user_by_id, login_required, verify_password
from app.extensions import limiter
from app.config import (
    DEFAULT_PARSER_CHOICE,
    PARSER_MODEL_CHOICES,
    SOURCE_OPTIONS,
    SOURCE_TYPE_OPTIONS,
    STATUS_OPTIONS,
    STATUS_STYLES,
)
from app.database import connect_db
from app.gmail import (
    clear_gmail_sync_error,
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
from app.utils import utc_now
from app.validators import form_payload, normalize_payload


def _email_note(record: dict[str, Any]) -> str:
    parts = ["Gmail sync"]
    subject = record.get("subject")
    if subject:
        parts.append(subject)
    parsed_status = record.get("parsed_status")
    if parsed_status:
        parts.append(f"Parsed status: {parsed_status}")
    return " | ".join(parts)


def build_stats(applications: list[dict[str, Any]]) -> dict[str, int]:
    active_statuses = {"Applied", "Interview Scheduled", "Technical Test", "Final Interview"}
    return {
        "total": len(applications),
        "active": sum(1 for a in applications if a["status"] in active_statuses),
        "interviews": sum(1 for a in applications if a["status"] == "Interview Scheduled"),
        "overdue": sum(1 for a in applications if a["is_overdue"]),
    }


def _render_dashboard(app: Flask, user_id: int, *, active_tab: str = "dashboard") -> str:
    filters = {
        "status": request.args.get("status", "").strip(),
        "source": request.args.get("source", "").strip(),
        "search": request.args.get("search", "").strip(),
    }
    sort_by = request.args.get("sort_by", "applied_date").strip()
    order = request.args.get("order", "asc").strip()
    edit_id = request.args.get("edit", "").strip() or None

    applications = fetch_applications(app, filters, sort_by=sort_by, order=order, user_id=user_id)
    editing_application = fetch_application(app, edit_id, user_id=user_id) if edit_id else None
    stats = build_stats(applications)
    gmail_status = get_gmail_status(app, user_id)
    if gmail_status.get("last_sync_error"):
        clear_gmail_sync_error(app, user_id)

    connection = connect_db(app)
    try:
        pending_emails = fetch_pending_review(connection, user_id)
        paused_emails = fetch_paused(connection, user_id)
    finally:
        connection.close()

    connection = connect_db(app)
    try:
        current_user = get_user_by_id(connection, user_id)
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
        parser_choices=[(v, label) for (v, label, _p, _m) in PARSER_MODEL_CHOICES],
        current_parser_choice=session.get("parser_choice", DEFAULT_PARSER_CHOICE),
        current_user=current_user,
    )


def register_routes(app: Flask) -> None:

    # ==================== Auth Routes ====================

    @app.get("/login")
    def login_route() -> Any:
        if "user_id" in session:
            return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.post("/login")
    @limiter.limit("10 per minute")
    def login_post() -> Any:
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        connection = connect_db(app)
        try:
            user = get_user_by_email(connection, email)
        finally:
            connection.close()

        if user is None or not verify_password(user["password_hash"], password):
            flash("Invalid email or password.", "error")
            return render_template("login.html"), 401

        session.clear()
        session["user_id"] = user["id"]
        return redirect(url_for("dashboard"))

    @app.get("/register")
    def register_route() -> Any:
        if "user_id" in session:
            return redirect(url_for("dashboard"))
        return render_template("register.html")

    @app.post("/register")
    @limiter.limit("5 per minute")
    def register_post() -> Any:
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("register.html"), 400
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("register.html"), 400
        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("register.html"), 400

        connection = connect_db(app)
        try:
            if get_user_by_email(connection, email):
                flash("An account with that email already exists.", "error")
                return render_template("register.html"), 400
            create_user(connection, email, password)
            connection.commit()
            user = get_user_by_email(connection, email)
        finally:
            connection.close()

        session.clear()
        session["user_id"] = user["id"]
        flash("Account created. Welcome!", "success")
        return redirect(url_for("dashboard"))

    @app.post("/logout")
    def logout_route() -> Any:
        session.clear()
        return redirect(url_for("login_route"))

    @app.get("/forgot-password")
    def forgot_password_route() -> Any:
        return render_template("forgot_password.html")

    # ==================== Web Routes ====================

    @app.get("/")
    @login_required
    def dashboard() -> str:
        user_id: int = session["user_id"]
        tab = request.args.get("tab", "").strip().lower()
        active_tab = "emails" if tab == "emails" else "dashboard"
        return _render_dashboard(app, user_id, active_tab=active_tab)

    @app.post("/applications")
    @login_required
    def create_application_route() -> Any:
        user_id: int = session["user_id"]
        connection = connect_db(app)
        try:
            payload = form_payload(request.form)
            insert_application(connection, payload, utc_now(), user_id)
            connection.commit()
            flash("Application created.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        finally:
            connection.close()
        return redirect(url_for("dashboard"))

    @app.post("/applications/<application_id>/update")
    @login_required
    def update_application_route(application_id: str) -> Any:
        user_id: int = session["user_id"]
        connection = connect_db(app)
        try:
            payload = form_payload(request.form)
            updated = update_application(connection, application_id, payload, user_id=user_id)
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
    @login_required
    def delete_application_route(application_id: str) -> Any:
        user_id: int = session["user_id"]
        connection = connect_db(app)
        try:
            delete_watchers_for_application(connection, application_id)
            connection.commit()
        finally:
            connection.close()
        delete_application(app, application_id, user_id=user_id)
        flash("Application deleted.", "success")
        return redirect(url_for("dashboard"))

    @app.post("/emails/<message_id>/accept")
    @login_required
    def accept_email_route(message_id: str) -> Any:
        user_id: int = session["user_id"]
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
                or record.get("parsed_applied_date")
                or (record.get("received_at") or "").split("T")[0]
                or date.today().isoformat()
            )
            payload = {
                "company": company,
                "role": role,
                "job_url": request.form.get("job_url") or None,
                "source": request.form.get("source") or record.get("parsed_source") or "Other",
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
            created = insert_application(connection, normalized, utc_now(), user_id)
            mark_accepted(connection, message_id, created["id"])
            connection.commit()
            flash(f"Application created from email: {company} — {role}.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        finally:
            connection.close()
        return redirect(url_for("dashboard", tab="emails"))

    @app.post("/emails/<message_id>/reject")
    @login_required
    def reject_email_route(message_id: str) -> Any:
        connection = connect_db(app)
        try:
            mark_dismissed(connection, message_id)
            connection.commit()
            flash("Email dismissed.", "success")
        finally:
            connection.close()
        return redirect(url_for("dashboard", tab="emails"))

    @app.post("/emails/<message_id>/retry")
    @login_required
    def retry_email_route(message_id: str) -> Any:
        user_id: int = session["user_id"]
        choice = request.form.get("parser_choice", DEFAULT_PARSER_CHOICE)
        provider, gemini_model, groq_model = None, None, None
        for (v, _label, p, m) in PARSER_MODEL_CHOICES:
            if v == choice:
                provider = p
                if p == "gemini":
                    gemini_model = m
                else:
                    groq_model = m
                break
        result = retry_parse_email(
            app, message_id, user_id=user_id,
            parser_provider=provider,
            gemini_model=gemini_model,
            groq_model=groq_model,
        )
        if not result["ok"]:
            flash(result.get("error") or "Retry failed.", "error")
        elif result["parse_status"] == "paused":
            flash(f"Parse still failing: {result.get('error') or 'unknown error'}", "error")
        else:
            flash(f"Email re-parsed → {result['parse_status']}.", "success")
        return redirect(url_for("dashboard", tab="emails"))

    @app.get("/gmail/connect")
    @login_required
    def gmail_connect_route() -> Any:
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
    @login_required
    def gmail_callback_route() -> Any:
        user_id: int = session["user_id"]
        state = session.pop("gmail_oauth_state", None)
        code_verifier = session.pop("gmail_oauth_code_verifier", None)
        result = finish_gmail_authorization(app, request.url, state, code_verifier, user_id)
        if result["ok"]:
            email_address = result.get("email") or "your Gmail account"
            flash(f"Gmail connected for {email_address}.", "success")
        else:
            flash(result["error"], "error")
        return redirect(url_for("dashboard"))

    @app.post("/gmail/disconnect")
    @login_required
    def gmail_disconnect_route() -> Any:
        user_id: int = session["user_id"]
        disconnect_gmail(app, user_id)
        flash("Gmail disconnected.", "success")
        return redirect(url_for("dashboard"))

    @app.get("/gmail/status")
    @login_required
    def gmail_status_route() -> Any:
        user_id: int = session["user_id"]
        return jsonify(get_gmail_status(app, user_id))

    @app.post("/gmail/sync")
    @login_required
    @limiter.limit("6 per minute")
    def gmail_sync_route() -> Any:
        user_id: int = session["user_id"]
        choice = request.form.get("parser_choice", "").strip()
        choice_map = {v: (p, m) for (v, _label, p, m) in PARSER_MODEL_CHOICES}
        provider_arg: str | None = None
        gemini_arg: str | None = None
        groq_arg: str | None = None
        if choice in choice_map:
            session["parser_choice"] = choice
            prov, mdl = choice_map[choice]
            provider_arg = prov
            if prov == "gemini":
                gemini_arg = mdl
            else:
                groq_arg = mdl
        result = sync_gmail_messages(
            app,
            user_id=user_id,
            parser_provider=provider_arg,
            gemini_model=gemini_arg,
            groq_model=groq_arg,
        )
        if result["ok"]:
            flash(
                "Gmail sync complete: "
                f"{result.get('fetched', 0)} fetched, "
                f"{result.get('updated', 0)} auto-updated, "
                f"{result.get('pending_review', 0)} pending review, "
                f"{result.get('paused', 0)} paused.",
                "success",
            )
            names = result.get("auto_updated_names") or []
            if names:
                flash("Auto-updated: " + " · ".join(names), "success")
        else:
            flash(result["error"], "error")
        return redirect(url_for("dashboard"))

    # ==================== API Routes ====================

    @app.get("/api/health")
    def health() -> Any:
        return jsonify({"status": "ok", "service": "job-tracker"})

    @app.get("/api/applications")
    @login_required
    def api_list_applications() -> Any:
        user_id: int = session["user_id"]
        filters = {
            "status": request.args.get("status", "").strip(),
            "source": request.args.get("source", "").strip(),
            "search": request.args.get("search", "").strip(),
        }
        sort_by = request.args.get("sort_by", "applied_date").strip()
        order = request.args.get("order", "desc").strip()
        return jsonify(fetch_applications(app, filters, sort_by=sort_by, order=order, user_id=user_id))

    @app.post("/api/applications")
    @login_required
    def api_create_application() -> Any:
        user_id: int = session["user_id"]
        payload = request.get_json(silent=True) or {}
        connection = connect_db(app)
        try:
            normalized = normalize_payload(payload)
            created = insert_application(connection, normalized, utc_now(), user_id)
            connection.commit()
        finally:
            connection.close()
        return jsonify(created), 201

    @app.get("/api/applications/<application_id>")
    @login_required
    def api_get_application(application_id: str) -> Any:
        user_id: int = session["user_id"]
        application = fetch_application(app, application_id, user_id=user_id)
        if application is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(application)

    @app.put("/api/applications/<application_id>")
    @login_required
    def api_update_application(application_id: str) -> Any:
        user_id: int = session["user_id"]
        payload = request.get_json(silent=True) or {}
        connection = connect_db(app)
        try:
            normalized = normalize_payload(payload, partial=True)
            updated = update_application(connection, application_id, normalized, partial=True, user_id=user_id)
            connection.commit()
        finally:
            connection.close()
        if updated is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(updated)

    @app.delete("/api/applications/<application_id>")
    @login_required
    def api_delete_application(application_id: str) -> Any:
        user_id: int = session["user_id"]
        connection = connect_db(app)
        try:
            delete_watchers_for_application(connection, application_id)
            connection.commit()
        finally:
            connection.close()
        deleted = delete_application(app, application_id, user_id=user_id)
        if not deleted:
            return jsonify({"error": "not found"}), 404
        return jsonify({"success": True})

    @app.get("/api/applications/<application_id>/watchers")
    @login_required
    def api_get_watchers(application_id: str) -> Any:
        connection = connect_db(app)
        try:
            patterns = fetch_watchers_for_application(connection, application_id)
        finally:
            connection.close()
        return jsonify(patterns)

    @app.get("/api/emails")
    @login_required
    def api_list_emails() -> Any:
        user_id: int = session["user_id"]
        connection = connect_db(app)
        try:
            return jsonify({
                "pending_review": fetch_pending_review(connection, user_id),
                "paused": fetch_paused(connection, user_id),
            })
        finally:
            connection.close()

    @app.get("/api/emails/<message_id>")
    @login_required
    def api_get_email(message_id: str) -> Any:
        connection = connect_db(app)
        try:
            record = fetch_email(connection, message_id)
        finally:
            connection.close()
        if record is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(record)

    @app.post("/api/gmail/sync")
    @login_required
    @limiter.limit("6 per minute")
    def api_gmail_sync() -> Any:
        user_id: int = session["user_id"]
        payload = request.get_json(silent=True) or {}
        choice = payload.get("parser_choice", "").strip()
        choice_map = {v: (p, m) for (v, _label, p, m) in PARSER_MODEL_CHOICES}
        provider_arg: str | None = None
        gemini_arg: str | None = None
        groq_arg: str | None = None
        if choice in choice_map:
            session["parser_choice"] = choice
            prov, mdl = choice_map[choice]
            provider_arg = prov
            if prov == "gemini":
                gemini_arg = mdl
            else:
                groq_arg = mdl
        result = sync_gmail_messages(
            app,
            user_id=user_id,
            parser_provider=provider_arg,
            gemini_model=gemini_arg,
            groq_model=groq_arg,
        )
        return jsonify(result)
