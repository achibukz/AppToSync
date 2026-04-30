import os

from app import create_app
from app.gmail import start_gmail_polling

app = create_app()


def main() -> None:
    """Run the Job Tracker application."""
    if os.environ.get("GMAIL_AUTO_POLL", "false").lower() == "true":
        start_gmail_polling(app)
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)


if __name__ == "__main__":
    main()
