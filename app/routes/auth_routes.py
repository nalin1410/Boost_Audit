from flask import Blueprint, request, jsonify, current_app
from app.utils.jwt_utils import generate_token, generate_tokens, refresh_access_token, decode_token
import bcrypt
from bson.objectid import ObjectId
import re  # for password validation regex

from app import mongo
import bcrypt

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['POST'])
def login():
    try:
        print(f"🔍 Login attempt - mongo: {mongo}")

        # CRITICAL FIX: Ensure we're in the application context
        with current_app.app_context():
            print(f"🔍 mongo.db (with context): {mongo.db}")

            if mongo.db is None:
                print("❌ Database still None even with context")
                return jsonify({"error": "Database connection failed"}), 500

            if not request.is_json:
                return jsonify({"error": "Request must be JSON"}), 415

            data = request.get_json()
            email = data.get("email")
            password = data.get("password")

            if not email or not password:
                return jsonify({"error": "Email and password are required"}), 400

            print(f"🔐 Login attempt for: {email}")

            # Find user in database - now with proper context
            user = mongo.db.users.find_one({"email": email})
            if not user:
                print(f"❌ User not found: {email}")
                return jsonify({"error": "Invalid email"}), 401

            # Verify password
            if bcrypt.checkpw(password.encode(), user['password']):
                # Generate tokens
                access_token, refresh_token = generate_tokens(user['_id'], user['role'])

                if not access_token:
                    print("❌ Token generation failed")
                    return jsonify({"error": "Token generation failed"}), 500

                print(f"✅ Login successful for: {email}")

                response_data = {
                    "message": "Login successful",
                    "access_token": access_token,
                    "role": user['role'],
                    "email": user['email']
                }

                # Include refresh token if generated
                if refresh_token:
                    response_data["refresh_token"] = refresh_token

                return jsonify(response_data), 200
            else:
                print(f"❌ Invalid password for: {email}")
                return jsonify({"error": "Invalid password"}), 401

    except Exception as e:
        print("❌ Login Internal Server Error:", str(e))
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500


@auth_bp.route('/refresh', methods=['POST'])
def refresh_token():
    """Refresh access token using refresh token"""
    try:
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 415

        data = request.get_json()
        refresh_token = data.get("refresh_token")

        if not refresh_token:
            return jsonify({"error": "Refresh token is required"}), 400

        print("🔄 Token refresh attempt")

        # Generate new access token
        new_access_token = refresh_access_token(refresh_token)

        if not new_access_token:
            print("❌ Failed to refresh token")
            return jsonify({"error": "Invalid or expired refresh token"}), 401

        print("✅ Token refreshed successfully")
        return jsonify({
            "message": "Token refreshed successfully",
            "access_token": new_access_token
        }), 200

    except Exception as e:
        print("❌ Refresh token error:", str(e))
        return jsonify({"error": "Internal server error"}), 500


@auth_bp.route('/quick-login', methods=['POST'])
def quick_login():
    """Quick login endpoint for development - generates long-lived token"""
    try:
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 415

        data = request.get_json()
        email = data.get("email")

        if not email:
            return jsonify({"error": "Email is required"}), 400

        print(f"⚡ Quick login attempt for: {email}")

        # Find user in database
        user = mongo.db.users.find_one({"email": email})
        if not user:
            print(f"❌ User not found: {email}")
            return jsonify({"error": "User not found"}), 404

        # Generate long-lived token (7 days)
        token = generate_token(user['_id'], user['role'])

        if not token:
            print("❌ Token generation failed")
            return jsonify({"error": "Token generation failed"}), 500

        print(f"✅ Quick login successful for: {email}")

        return jsonify({
            "message": "Quick login successful",
            "token": token,  # For backward compatibility
            "access_token": token,
            "role": user['role'],
            "email": user['email']
        }), 200

    except Exception as e:
        print("❌ Quick login error:", str(e))
        return jsonify({"error": "Internal server error"}), 500

@auth_bp.route('/register', methods=['POST'])
def register():
    try:
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 415

        data = request.get_json()
        email = data.get("email")
        password = data.get("password")
        role = data.get("role", "field_worker")  # Default role if not provided
        fullName = data.get("fullName")  # Remove the comma and tuple creation
        phoneNumber = data.get("phoneNumber")  # Remove the comma and tuple creation
        controller_email = data.get("controller_email")  # Fix the key name to match frontend

        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400

        print(f"📝 Registration attempt for: {email}")
        print(f"📝 Full Name: {fullName}")
        print(f"📝 Phone Number: {phoneNumber}")
        print(f"📝 Controller Email: {controller_email}")

        # Check if user already exists
        existing_user = mongo.db.users.find_one({"email": email})
        if existing_user:
            print(f"❌ User already exists: {email}")
            return jsonify({"error": "User with this email already exists"}), 409

        # Hash the password
        hashed_pw = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

        # Insert user into MongoDB
        new_user = {
            "email": email,
            "password": hashed_pw,
            "role": role,
            "full_name": fullName,  # Now it's a string, not a tuple
            "phone_number": phoneNumber,  # Now it's a string, not a tuple
            "controller_email": controller_email  # Now it's a string, not a tuple
        }

        result = mongo.db.users.insert_one(new_user)
        print(f"✅ User registered: {email}")

        return jsonify({
            "message": "User created successfully",
            "user_id": str(result.inserted_id),
            "email": email,
            "role": role
        }), 201

    except Exception as e:
        print("❌ Registration Error:", str(e))
        return jsonify({"error": "Internal server error"}), 500


@auth_bp.route('/user/<email>', methods=['GET'])
def get_user_by_email(email):
    """Get user details by email from users collection"""
    try:
        print(f"🔍 Fetching user details for: {email}")

        # Find user by email
        user = mongo.db.users.find_one({"email": email})

        if not user:
            print(f"❌ User not found: {email}")
            return jsonify({"error": "User not found"}), 404

        # Remove sensitive data and convert ObjectId to string
        user_data = {
            "_id": str(user['_id']),
            "email": user.get('email'),
            "role": user.get('role'),
            "full_name": user.get('full_name', 'N/A'),
            "phone_number": user.get('phone_number', 'N/A'),
            "controller_email": user.get('controller_email', 'N/A'),
            'phone': user.get('phone', ''),
            'state': user.get('state', ''),
            'city': user.get('city', ''),
            'created_at': user.get('created_at', ''),
            'is_active': user.get('is_active', True)
        }

        print(f"✅ User details fetched for: {email}")
        return jsonify(user_data), 200

    except Exception as e:
        print("❌ Error fetching user:", str(e))
        return jsonify({"error": "Internal server error"}), 500


@auth_bp.route('/validate-token', methods=['POST'])
def validate_token():
    """Validate if a token is still valid"""
    try:
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 415

        data = request.get_json()
        token = data.get("token")

        if not token:
            return jsonify({"error": "Token is required"}), 400

        payload = decode_token(token)
        if not payload:
            return jsonify({"valid": False, "error": "Invalid or expired token"}), 200

        return jsonify({
            "valid": True,
            "user_id": payload.get('user_id'),
            "role": payload.get('role')
        }), 200

    except Exception as e:
        print("❌ Token validation error:", str(e))
        return jsonify({"error": "Internal server error"}), 500


@auth_bp.route('/change-password', methods=['POST'])
def change_password():
    """Change user password - works for both field_worker and controller roles"""
    try:
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 415

        data = request.get_json()
        email = data.get("email")
        current_password = data.get("currentPassword")
        new_password = data.get("newPassword")

        if not email or not current_password or not new_password:
            return jsonify({"error": "Email, current password, and new password are required"}), 400

        print(f"🔐 Password change attempt for: {email}")

        # Find user in database
        user = mongo.db.users.find_one({"email": email})
        if not user:
            print(f"❌ User not found: {email}")
            return jsonify({"error": "User not found"}), 404

        # Verify current password
        if not bcrypt.checkpw(current_password.encode(), user['password']):
            print(f"❌ Invalid current password for: {email}")
            return jsonify({"error": "Current password is incorrect"}), 401

        # Validate new password requirements
        if len(new_password) < 8:
            return jsonify({"error": "Password must be at least 8 characters long"}), 400

        # Check for uppercase, lowercase, and digit
        import re
        if not re.search(r'(?=.*[a-z])(?=.*[A-Z])(?=.*\d)', new_password):
            return jsonify({
                               "error": "Password must contain at least one uppercase letter, one lowercase letter, and one number"}), 400

        # Check if new password is different from current password
        if bcrypt.checkpw(new_password.encode(), user['password']):
            return jsonify({"error": "New password must be different from current password"}), 400

        # Hash new password
        hashed_new_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())

        # Update password in database
        result = mongo.db.users.update_one(
            {"email": email},
            {"$set": {"password": hashed_new_password}}
        )

        if result.modified_count == 0:
            print(f"❌ Failed to update password for: {email}")
            return jsonify({"error": "Failed to update password"}), 500

        print(f"✅ Password changed successfully for: {email}")

        return jsonify({
            "message": "Password changed successfully",
            "email": email
        }), 200

    except Exception as e:
        print("❌ Change password error:", str(e))
        return jsonify({"error": "Internal server error"}), 500