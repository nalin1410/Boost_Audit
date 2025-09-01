import os
from dotenv import load_dotenv

# Try loading from specific path
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path)


class Config:
    MONGO_URI = os.getenv("MONGO_URI")
    SECRET_KEY = os.getenv("SECRET_KEY")

    # Fallback for development
    if not MONGO_URI:
        MONGO_URI = "mongodb://localhost:27017/your_app_db"
        print("⚠️  Using fallback MONGO_URI")

    if not SECRET_KEY:
        SECRET_KEY = "dev-secret-key-change-in-production"
        print("⚠️  Using fallback SECRET_KEY")

    print(f"✅ Config loaded - MONGO_URI: {MONGO_URI[:20]}...")