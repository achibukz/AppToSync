from app import create_app
from app.gmail import start_gmail_polling


def main() -> None:
    """Run the Job Tracker application."""
    app = create_app()
    start_gmail_polling(app)
    app.run(host="127.0.0.1", port=3000, debug=True, use_reloader=False)


if __name__ == "__main__":
    main()
