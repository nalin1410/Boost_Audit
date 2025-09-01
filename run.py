import os
from app import create_app

app = create_app()  # Factory creates Flask app

if __name__ == "__main__":
    # Render provides PORT environment variable
    port = int(os.environ.get("PORT", 10000))
    # Use debug=False for production
    app.run(host="0.0.0.0", port=port, debug=False)