from flask import Blueprint, request, jsonify
from app import mongo
from datetime import datetime
import pytz
import base64
import os
from werkzeug.utils import secure_filename

# Import the OneDrive upload function (you'll need to implement this)
from app.utils.gcs_upload import upload_to_onedrive_and_get_url

audit_bp = Blueprint('audit', __name__)

IST_TZ = pytz.timezone('Asia/Kolkata')


def get_image_url_from_onedrive(record):
    """
    Get the actual image URL from OneDrive or return the stored URL
    """
    try:
        image_url = record.get('image_url', '')

        # If it's already a OneDrive URL, return as is
        if 'onedrive' in image_url.lower() or 'sharepoint' in image_url.lower():
            return image_url

        # Return the stored URL
        return image_url

    except Exception as e:
        print(f"❌ Error getting image URL: {str(e)}")
        return record.get('image_url', '')


@audit_bp.route('/submit', methods=['POST'])
def submit_audit():
    try:
        data = request.get_json()
        print("📥 Raw audit request data type:", type(data))
        print("📥 Raw audit request data keys:", list(data.keys()) if data else "No data")
        print("📥 Full received audit data:", data)  # Debug log

        # Check if data exists
        if not data:
            print("❌ No data received in request")
            return jsonify({'error': 'No data received'}), 400

        # Required fields for mystery audit
        required = ['latitude', 'longitude', 'image', 'timestamp', 'user_email', 'audit_type', 'evaluations',
                    'cityName']

        # Check each required field individually
        missing_fields = []
        for field in required:
            if field not in data:
                missing_fields.append(field)
                print(f"❌ Missing field: {field}")
            elif data[field] is None:
                missing_fields.append(f"{field} (null)")
                print(f"❌ Null field: {field}")
            elif field != 'evaluations' and data[field] == '':
                missing_fields.append(f"{field} (empty)")
                print(f"❌ Empty field: {field}")
            else:
                if field == 'evaluations':
                    print(f"✅ Field {field}: [{len(data[field])} evaluations]")
                elif field == 'image':
                    print(f"✅ Field {field}: [image data present]")
                else:
                    print(f"✅ Field {field}: {data[field]}")

        if missing_fields:
            print("❌ Missing/invalid fields:", missing_fields)
            return jsonify({'error': f'Missing or invalid required fields: {missing_fields}'}), 400

        # Extract and validate audit_type
        audit_type = data.get('audit_type', '').strip().lower()
        print("📝 Raw audit_type:", repr(audit_type))

        if audit_type != 'mystery_audit':
            print("❌ Invalid audit_type:", audit_type)
            return jsonify({'error': f'Invalid audit_type: {audit_type}. Must be mystery_audit'}), 400

        # Validate evaluations
        evaluations = data.get('evaluations', [])
        if not evaluations or len(evaluations) == 0:
            print("❌ No evaluations provided")
            return jsonify({'error': 'At least one staff evaluation is required'}), 400

        # Validate each evaluation
        required_eval_fields = [
            'storeCode', 'outletName', 'promoterName', 'groomingCompliance',
            'greetingEngagement', 'productKnowledge', 'communicationSkills',
            'salesClosingSkills', 'handlingObjections', 'crossSelling',
            'explainingOffers', 'otherObservations'
        ]

        for i, evaluation in enumerate(evaluations):
            print(f"🔍 Validating evaluation {i + 1}:")
            for field in required_eval_fields:
                if field not in evaluation or not evaluation[field] or str(evaluation[field]).strip() == '':
                    print(f"❌ Evaluation {i + 1} missing field: {field}")
                    return jsonify({
                        'error': f'Evaluation {i + 1} is missing required field: {field}'
                    }), 400
                else:
                    print(f"   ✅ {field}: {evaluation[field]}")

        # Process timestamp
        timestamp_from_frontend_str = data['timestamp']
        print("📅 Timestamp from frontend:", timestamp_from_frontend_str)

        try:
            dt_from_frontend = datetime.fromisoformat(timestamp_from_frontend_str)
            if dt_from_frontend.tzinfo is None:
                localized_dt = IST_TZ.localize(dt_from_frontend)
            else:
                localized_dt = dt_from_frontend.astimezone(IST_TZ)
        except ValueError:
            try:
                dt_naive = datetime.strptime(timestamp_from_frontend_str.split('.')[0], '%Y-%m-%d %H:%M:%S')
                localized_dt = IST_TZ.localize(dt_naive)
            except ValueError:
                print("❌ Invalid timestamp format:", timestamp_from_frontend_str)
                return jsonify({'error': 'Invalid timestamp format provided to submit_audit'}), 400

        # Store the formatted IST string directly
        ist_display_str = localized_dt.strftime('%d %b %Y, %I:%M %p IST')
        print("🕐 Formatted timestamp:", ist_display_str)

        # Get user email
        user_email = data['user_email']
        print("👤 User email:", user_email)

        # Upload image to OneDrive and get URL
        try:
            print("📤 Starting OneDrive upload...")
            # Create a unique filename for the audit image
            filename = f"mystery_audit_{user_email}_{localized_dt.strftime('%Y%m%d_%H%M%S')}.jpg"
            image_url = upload_to_onedrive_and_get_url(data['image'], filename)
            print(f"✅ OneDrive upload successful: {image_url}")
        except Exception as upload_error:
            print(f"❌ OneDrive upload failed: {str(upload_error)}")
            # You can decide whether to fail the entire request or continue with a placeholder
            image_url = f"UPLOAD_FAILED: {str(upload_error)}"

        # Create the audit record
        audit_record = {
            "user_email": user_email,
            "audit_type": "mystery_audit",
            "location": {
                "latitude": data['latitude'],
                "longitude": data['longitude']
            },
            "image_url": image_url,
            "timestamp": ist_display_str,
            "cityName": data.get('cityName', 'Unknown'),
            "evaluations": evaluations,
            "staff_count": len(evaluations),
            "created_at": datetime.utcnow(),
            "status": "completed"
        }

        print("💾 Final audit record structure:")
        for key, value in audit_record.items():
            if key == 'evaluations':
                print(f"   - {key}: [{len(value)} evaluations]")
            elif key == 'location':
                print(f"   - {key}: {value}")
            elif key == 'image_url' and len(str(value)) > 50:
                print(f"   - {key}: {str(value)[:50]}...")
            else:
                print(f"   - {key}: {repr(value)}")

        # Insert the audit record
        result = mongo.db.mystery_audits.insert_one(audit_record)
        inserted_id = result.inserted_id
        print("✅ Insert result ID:", inserted_id)

        # VERIFICATION: Immediately fetch the inserted record to verify
        inserted_record = mongo.db.mystery_audits.find_one({"_id": inserted_id})
        if inserted_record:
            print("🔍 Verification - Inserted record audit_type:",
                  inserted_record.get('audit_type', 'MISSING!'))
            print("🔍 Verification - Staff evaluations count:",
                  len(inserted_record.get('evaluations', [])))
        else:
            print("❌ Could not find inserted record for verification!")

        return jsonify({
            'message': f'Mystery audit submitted successfully with {len(evaluations)} staff evaluation(s)',
            'audit_type': 'mystery_audit',
            'timestamp': ist_display_str,
            'record_id': str(inserted_id),
            'staff_count': len(evaluations),
            'evaluations': len(evaluations),
            'image_url': image_url if not image_url.startswith('UPLOAD_FAILED') else None,
            'storage_provider': 'OneDrive' if not image_url.startswith('UPLOAD_FAILED') else 'Local/Error',
            'cityName': data.get('cityName')
        }), 201

    except Exception as e:
        print("❌ Error submitting mystery audit:", str(e))
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Internal Server Error: {str(e)}'}), 500


@audit_bp.route('/image_url/<record_id>', methods=['GET'])
def get_audit_image_url(record_id):
    """Get the OneDrive image URL for a specific audit record"""
    try:
        from bson.objectid import ObjectId

        # Find the record by ID
        record = mongo.db.mystery_audits.find_one({"_id": ObjectId(record_id)})

        if not record:
            return jsonify({'error': 'Record not found'}), 404

        # Get the image URL from OneDrive
        image_url = get_image_url_from_onedrive(record)

        return jsonify({
            'record_id': str(record['_id']),
            'image_url': image_url,
            'user_email': record.get('user_email'),
            'timestamp': record.get('timestamp'),
            'audit_type': record.get('audit_type'),
            'staff_count': record.get('staff_count'),
            'storage_type': 'OneDrive' if 'onedrive' in image_url.lower() else 'Local/Other'
        }), 200

    except Exception as e:
        print("❌ Error fetching image URL:", str(e))
        return jsonify({'error': 'Server error'}), 500


@audit_bp.route('/list/<user_email>', methods=['GET'])
def list_user_audits(user_email):
    """Get all audits for a specific user"""
    try:
        # Get pagination parameters
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 10))
        skip = (page - 1) * limit

        # Find audits for the user
        audits = list(mongo.db.mystery_audits.find(
            {"user_email": user_email}
        ).sort("created_at", -1).skip(skip).limit(limit))

        # Convert ObjectId to string and format response
        formatted_audits = []
        for audit in audits:
            audit['_id'] = str(audit['_id'])
            audit['created_at'] = audit['created_at'].isoformat() if 'created_at' in audit else None
            formatted_audits.append(audit)

        # Get total count
        total_count = mongo.db.mystery_audits.count_documents({"user_email": user_email})

        return jsonify({
            'audits': formatted_audits,
            'total_count': total_count,
            'page': page,
            'limit': limit,
            'has_more': (skip + limit) < total_count
        }), 200

    except Exception as e:
        print("❌ Error fetching user audits:", str(e))
        return jsonify({'error': 'Server error'}), 500


@audit_bp.route('/stats/<user_email>', methods=['GET'])
def get_audit_stats(user_email):
    """Get audit statistics for a user"""
    try:
        # Get total audits
        total_audits = mongo.db.mystery_audits.count_documents({"user_email": user_email})

        # Get audits this month
        now = datetime.utcnow()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        this_month = mongo.db.mystery_audits.count_documents({
            "user_email": user_email,
            "created_at": {"$gte": start_of_month}
        })

        # Get total staff evaluated
        pipeline = [
            {"$match": {"user_email": user_email}},
            {"$group": {"_id": None, "total_staff": {"$sum": "$staff_count"}}}
        ]
        staff_result = list(mongo.db.mystery_audits.aggregate(pipeline))
        total_staff = staff_result[0]['total_staff'] if staff_result else 0

        return jsonify({
            'total_audits': total_audits,
            'this_month': this_month,
            'total_staff_evaluated': total_staff,
            'average_staff_per_audit': round(total_staff / total_audits, 1) if total_audits > 0 else 0
        }), 200

    except Exception as e:
        print("❌ Error fetching audit stats:", str(e))
        return jsonify({'error': 'Server error'}), 500



@audit_bp.route('/users', methods=['GET'])
def get_field_workers():
    try:
        controller_email = request.args.get('controllerEmail')
        print(f"🧩 controllerEmail received: '{controller_email}'")
        print(f"🧩 controllerEmail type: {type(controller_email)}")

        # Check for missing, empty, or null-like values
        if not controller_email or controller_email.strip() == '' or controller_email.lower() == 'null':
            print("❌ Invalid controllerEmail parameter")
            return jsonify({
                "error": "Missing or invalid controllerEmail parameter",
                "details": "Please provide a valid controller email address"
            }), 400

        # Clean the email parameter
        controller_email = controller_email.strip()
        print(f"🧩 Cleaned controllerEmail: '{controller_email}'")

        # Verify the controller exists and has the correct role
        controller = mongo.db.users.find_one({
            "email": controller_email,
            "role": "controller"
        })

        if not controller:
            print(f"❌ Controller not found: {controller_email}")
            return jsonify({
                "error": "Controller not found",
                "details": "The specified controller email does not exist or is not a controller"
            }), 404

        print(f"✅ Controller verified: {controller_email}")

        # Fetch users with role "field_worker" and matching controller email
        field_workers_cursor = mongo.db.users.find(
            {
                "role": "field_worker",
                "controller_email": controller_email
            },
            {"_id": 0, "email": 1}
        )

        # Convert cursor to list and extract emails
        field_workers = list(field_workers_cursor)
        emails = [user["email"] for user in field_workers]

        print(f"📋 Found {len(emails)} field workers for controller {controller_email}")
        print(f"📋 Field workers: {emails}")

        return jsonify({
            "users": emails,
            "count": len(emails),
            "controller_email": controller_email
        }), 200

    except Exception as e:
        print(f"❌ Error fetching field workers: {str(e)}")
        print(f"❌ Error type: {type(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": "Internal Server Error",
            "details": str(e)
        }), 500