import jwt
import datetime
from flask import current_app


def generate_tokens(user_id, role):
    """Generate both access token and refresh token"""
    try:
        # Access token (shorter expiration)
        access_payload = {
            "user_id": str(user_id),
            "role": role,
            "type": "access",
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=2)  # 2 hours
        }
        access_token = jwt.encode(access_payload, current_app.config['SECRET_KEY'], algorithm="HS256")

        # Refresh token (longer expiration)
        refresh_payload = {
            "user_id": str(user_id),
            "role": role,
            "type": "refresh",
            "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7)  # 7 days
        }
        refresh_token = jwt.encode(refresh_payload, current_app.config['SECRET_KEY'], algorithm="HS256")

        return access_token, refresh_token
    except Exception as e:
        print("❌ Error generating tokens:", str(e))
        return None, None


def generate_token(user_id, role):
    """Generate access token only (for backward compatibility)"""
    try:
        payload = {
            "user_id": str(user_id),
            "role": role,
            "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7)  # Extended to 7 days for development
        }
        token = jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm="HS256")
        return token
    except Exception as e:
        print("❌ Error generating token:", str(e))
        return None


def decode_token(token):
    """Decode and validate token"""
    try:
        payload = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        print("❌ Token expired")
        return None
    except jwt.InvalidTokenError as e:
        print("❌ Invalid token:", str(e))
        return None


def refresh_access_token(refresh_token):
    """Generate new access token using refresh token"""
    try:
        payload = jwt.decode(refresh_token, current_app.config['SECRET_KEY'], algorithms=["HS256"])

        if payload.get('type') != 'refresh':
            print("❌ Invalid token type for refresh")
            return None

        # Generate new access token
        new_access_token = generate_token(payload['user_id'], payload['role'])
        return new_access_token
    except jwt.ExpiredSignatureError:
        print("❌ Refresh token expired")
        return None
    except jwt.InvalidTokenError as e:
        print("❌ Invalid refresh token:", str(e))
        return None


def is_token_expired(token):
    """Check if token is expired without decoding"""
    try:
        jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=["HS256"])
        return False
    except jwt.ExpiredSignatureError:
        return True
    except jwt.InvalidTokenError:
        return True