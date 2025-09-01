from flask import Flask
from flask_pymongo import PyMongo
from flask_cors import CORS

from app.config import Config

mongo = PyMongo()  # global mongo instance


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize MongoDB
    try:
        mongo.init_app(app)
        print("✅ MongoDB extension initialized.")

        # Test the actual database connection within app context
        with app.app_context():
            try:
                # Test database connectivity
                db_info = mongo.db.command('ping')
                db_name = mongo.db.name
                print(f"✅ MongoDB connected successfully to database: '{db_name}'")

                # List collections to verify access
                collections = mongo.db.list_collection_names()
                print(f"📊 Available collections: {collections}")

            except Exception as db_error:
                print(f"❌ Database connection test failed: {str(db_error)}")
                print(f"🔍 mongo.db value: {mongo.db}")
                print(f"🔍 MONGO_URI: {app.config.get('MONGO_URI', 'Not set')}")

    except Exception as e:
        print(f"❌ MongoDB initialization failed: {str(e)}")

    # Initialize CORS
    CORS(app)

    # Register Blueprints
    from app.routes.auth_routes import auth_bp
    app.register_blueprint(auth_bp, url_prefix='/api/auth')

    from app.routes.attendance_routes import audit_bp
    app.register_blueprint(audit_bp, url_prefix='/api/attendance')

    from app.routes.school_audit_routes import school_audit_bp
    app.register_blueprint(school_audit_bp, url_prefix='/api/school-audit')

    from app.routes.school_assignment_routes import school_assignment_bp
    app.register_blueprint(school_assignment_bp, url_prefix='/api/school-assignment')


    return app