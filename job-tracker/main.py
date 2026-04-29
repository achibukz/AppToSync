from app import create_app


def main() -> None:
    """Run the Job Tracker application."""
    app = create_app()
    app.run(host="127.0.0.1", port=3000, debug=True)


if __name__ == "__main__":
    main()
