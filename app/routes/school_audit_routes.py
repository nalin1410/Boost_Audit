from flask import Blueprint, request, jsonify, Response
from app import mongo
from datetime import datetime, timedelta
import pytz
import io
import xlsxwriter
from flask import send_file
from bson.objectid import ObjectId
import uuid
import re
import urllib.parse
import base64

# Import the updated OneDrive upload functions
from app.utils.gcs_upload import (
    upload_to_onedrive_and_get_url,
    get_onedrive_image_content,
    get_onedrive_file_info,
    convert_sharepoint_urls_to_file_ids
)

school_audit_bp = Blueprint('school_audit', __name__)
IST_TZ = pytz.timezone('Asia/Kolkata')


def generate_unique_filename(user_email, audit_type, original_extension='jpg'):
    """Generate unique filename for OneDrive upload"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    unique_id = str(uuid.uuid4())[:8]
    sanitized_email = user_email.replace('@', '_').replace('.', '_')

    return f"{sanitized_email}_{audit_type}_{timestamp}_{unique_id}.{original_extension}"


def extract_file_id_from_url(url_or_id):
    """
    Extract file ID from OneDrive URL or return as-is if already an ID
    Handles multiple OneDrive/SharePoint URL formats
    """
    if not url_or_id or url_or_id.startswith('UPLOAD_FAILED'):
        return None

    # If it's a SharePoint sharing URL, return it as-is (we'll handle it in the image serving)
    if isinstance(url_or_id, str) and 'sharepoint.com' in url_or_id:
        return url_or_id

    # If it's already a file ID (alphanumeric string without special characters), return as-is
    if re.match(r'^[A-Za-z0-9_-]{20,}$', str(url_or_id)) and '/' not in url_or_id and '.' not in url_or_id:
        return url_or_id

    try:
        url_str = str(url_or_id)

        # Pattern 1: Graph API URLs - /me/drive/items/{file_id}
        graph_match = re.search(r'/items/([A-Za-z0-9_-]+)', url_str)
        if graph_match:
            return graph_match.group(1)

        # Pattern 2: Direct download URLs
        download_match = re.search(r'download\.aspx.*sourceid=([A-Za-z0-9_-]+)', url_str)
        if download_match:
            return download_match.group(1)

        # Pattern 3: OneDrive web URLs
        onedrive_match = re.search(r'onedrive\.live\.com.*id=([A-Za-z0-9_-]+)', url_str)
        if onedrive_match:
            return onedrive_match.group(1)

        # If no pattern matches, return the original (might be a valid file ID)
        print(f"⚠️ Could not extract file ID from URL: {url_str}")
        return url_or_id

    except Exception as e:
        print(f"❌ Error extracting file ID from {url_or_id}: {str(e)}")
        return url_or_id


def upload_image_to_onedrive(image_data, filename):
    """Helper function to upload image to OneDrive with error handling"""
    try:
        if not image_data:
            return None

        file_metadata = upload_to_onedrive_and_get_url(
            image_data,
            filename,
            use_date_folder=True
        )
        return file_metadata['file_id']
    except Exception as e:
        print(f"❌ Image upload failed for {filename}: {str(e)}")
        return f"UPLOAD_FAILED: {str(e)}"


def process_sessions_data(sessions_data, user_email):
    """Process sessions data and upload images"""
    processed_sessions = {}

    if not sessions_data or not isinstance(sessions_data, dict):
        return processed_sessions

    for session_key, session_data in sessions_data.items():
        if not session_data.get('enabled', False):
            continue

        processed_session = {
            'enabled': True,
            'name': session_data.get('name', ''),
            'studentsCount': session_data.get('studentsCount', ''),
            'sachetCount': session_data.get('sachetCount', ''),
            'winnerName': session_data.get('winnerName', ''),
            'winnerClass': session_data.get('winnerClass', ''),
            'startSelfie': None,
            'endSelfie': None,
            'winnerPhoto': None,
            'sachetDistributionPhoto': None
        }

        # Upload session images
        if session_data.get('startSelfie', {}).get('base64'):
            filename = generate_unique_filename(user_email, f'{session_key}_start_selfie', 'jpg')
            processed_session['startSelfie'] = upload_image_to_onedrive(
                f"data:image/jpeg;base64,{session_data['startSelfie']['base64']}",
                filename
            )

        if session_data.get('endSelfie', {}).get('base64'):
            filename = generate_unique_filename(user_email, f'{session_key}_end_selfie', 'jpg')
            processed_session['endSelfie'] = upload_image_to_onedrive(
                f"data:image/jpeg;base64,{session_data['endSelfie']['base64']}",
                filename
            )

        if session_data.get('winnerPhoto', {}).get('base64'):
            filename = generate_unique_filename(user_email, f'{session_key}_winner', 'jpg')
            processed_session['winnerPhoto'] = upload_image_to_onedrive(
                f"data:image/jpeg;base64,{session_data['winnerPhoto']['base64']}",
                filename
            )

        if session_data.get('sachetDistributionPhoto', {}).get('base64'):
            filename = generate_unique_filename(user_email, f'{session_key}_distribution', 'jpg')
            processed_session['sachetDistributionPhoto'] = upload_image_to_onedrive(
                f"data:image/jpeg;base64,{session_data['sachetDistributionPhoto']['base64']}",
                filename
            )

        processed_sessions[session_key] = processed_session

    return processed_sessions


def create_image_url(base_url, file_id):
    """Helper function to create image URLs from file IDs - FIXED"""
    if not file_id or file_id == '' or file_id is None or str(file_id).startswith('UPLOAD_FAILED'):
        print(f"DEBUG: Skipping invalid file_id: {file_id}")
        return ''

    # Clean the file_id
    clean_file_id = str(file_id).strip()
    if not clean_file_id or clean_file_id == 'None':
        print(f"DEBUG: Empty file_id after cleaning: '{clean_file_id}'")
        return ''

    try:
        # URL encode the file ID to handle special characters
        encoded_file_id = urllib.parse.quote(clean_file_id, safe='')
        image_url = f"{base_url}/api/school-audit/image/{encoded_file_id}?resize=true"

        print(f"DEBUG: Created image URL: {image_url}")
        return image_url

    except Exception as e:
        print(f"DEBUG: Error creating image URL for {file_id}: {str(e)}")
        return ''


def format_audit_response(audit, base_url):
    """Format audit response with session image URLs - COMPLETELY FIXED VERSION"""
    # Convert ObjectId to string
    audit['_id'] = str(audit['_id'])

    # Convert datetime objects to ISO strings
    if 'created_at' in audit:
        audit['created_at'] = audit['created_at'].isoformat()
    if 'completed_at' in audit:
        audit['completed_at'] = audit['completed_at'].isoformat()

    # Create main image URLs
    audit['start_image_url'] = create_image_url(base_url, audit.get('start_image_file_id'))
    audit['end_image_url'] = create_image_url(base_url, audit.get('end_image_file_id'))
    audit['audit_sheet_image_url'] = create_image_url(base_url, audit.get('audit_sheet_image_file_id'))

    # Create URLs for session images - COMPLETELY FIXED
    if 'sessions' in audit and isinstance(audit['sessions'], dict):
        print(f"DEBUG: Processing sessions for audit {audit['_id']}")
        for session_key, session_data in audit['sessions'].items():
            if isinstance(session_data, dict):
                print(f"DEBUG: Processing session {session_key}")

                # Get the actual file IDs from session data
                start_selfie_id = session_data.get('startSelfie')
                end_selfie_id = session_data.get('endSelfie')
                winner_photo_id = session_data.get('winnerPhoto')
                sachet_photo_id = session_data.get('sachetDistributionPhoto')

                # Debug the raw file IDs
                print(f"  Raw startSelfie: '{start_selfie_id}'")
                print(f"  Raw endSelfie: '{end_selfie_id}'")
                print(f"  Raw winnerPhoto: '{winner_photo_id}'")
                print(f"  Raw sachetDistributionPhoto: '{sachet_photo_id}'")

                # Create URLs from file IDs - FIXED: Use the actual IDs, not None
                session_data['startSelfieUrl'] = create_image_url(base_url, start_selfie_id)
                session_data['endSelfieUrl'] = create_image_url(base_url, end_selfie_id)
                session_data['winnerPhotoUrl'] = create_image_url(base_url, winner_photo_id)
                session_data['sachetDistributionPhotoUrl'] = create_image_url(base_url, sachet_photo_id)

                # Debug the generated URLs
                print(f"  Generated startSelfieUrl: '{session_data['startSelfieUrl']}'")
                print(f"  Generated endSelfieUrl: '{session_data['endSelfieUrl']}'")
                print(f"  Generated winnerPhotoUrl: '{session_data['winnerPhotoUrl']}'")
                print(f"  Generated sachetDistributionPhotoUrl: '{session_data['sachetDistributionPhotoUrl']}'")

    return audit


@school_audit_bp.route('/start-audit', methods=['POST'])
def start_audit():
    """Start school audit session"""
    try:
        data = request.get_json()

        required_fields = ['latitude', 'longitude', 'school_name', 'city', 'start_image',
                           'timestamp', 'user_email', 'promoters_count', 'sessions']

        missing_fields = []
        for field in required_fields:
            if field not in data or data[field] is None:
                missing_fields.append(field)

        if missing_fields:
            return jsonify({'error': f'Missing required fields: {missing_fields}'}), 400

        # Validate that at least one session is enabled
        sessions_data = data.get('sessions', {})
        enabled_sessions = [s for s in sessions_data.values() if s.get('enabled', False)]
        if not enabled_sessions:
            return jsonify({'error': 'At least one session must be enabled'}), 400

        # Process timestamp
        timestamp_str = data['timestamp']
        try:
            dt_from_frontend = datetime.fromisoformat(timestamp_str)
            if dt_from_frontend.tzinfo is None:
                localized_dt = IST_TZ.localize(dt_from_frontend)
            else:
                localized_dt = dt_from_frontend.astimezone(IST_TZ)
        except ValueError:
            return jsonify({'error': 'Invalid timestamp format'}), 400

        ist_display_str = localized_dt.strftime('%d %b %Y, %I:%M %p IST')

        # Upload start image to OneDrive
        start_filename = generate_unique_filename(data['user_email'], 'audit_start', 'jpg')
        start_file_id = upload_image_to_onedrive(data['start_image'], start_filename)

        if not start_file_id:
            return jsonify({'error': 'Failed to upload start image'}), 500

        # Check if audit already started for this school today
        today_date_str = localized_dt.strftime('%d %b %Y')
        existing_audit = mongo.db.school_audits.find_one({
            "user_email": data['user_email'],
            "school_name": data['school_name'],
            "city": data['city'],
            "audit_date": today_date_str,
            "status": "in_progress"
        })

        if existing_audit:
            return jsonify({'error': 'Audit already in progress for this school today'}), 400

        # Process sessions data and upload session images
        processed_sessions = process_sessions_data(sessions_data, data['user_email'])

        # Calculate total students from enabled sessions
        total_students = sum(
            int(session.get('studentsCount', 0) or 0)
            for session in processed_sessions.values()
        )

        # Create audit record
        audit_record = {
            "user_email": data['user_email'],
            "school_name": data['school_name'],
            "city": data['city'],
            "location": {
                "latitude": data['latitude'],
                "longitude": data['longitude']
            },
            "start_timestamp": ist_display_str,
            "audit_date": today_date_str,
            "start_image_file_id": start_file_id,
            "promoters_count": int(data['promoters_count']),
            "boost_sachets_given": int(data.get('boost_sachets_given', 0)),
            "giveaways_given": data.get('giveaways_given', ''),
            "sessions": processed_sessions,
            "total_students": total_students,
            "audit_sheet_image_file_id": '',
            "status": "in_progress",
            "created_at": localized_dt
        }

        # Upload audit sheet image if provided
        if data.get('audit_sheet_image'):
            sheet_filename = generate_unique_filename(data['user_email'], 'audit_sheet', 'jpg')
            audit_record['audit_sheet_image_file_id'] = upload_image_to_onedrive(
                data['audit_sheet_image'],
                sheet_filename
            )

        result = mongo.db.school_audits.insert_one(audit_record)

        return jsonify({
            'message': 'School audit started successfully',
            'audit_id': str(result.inserted_id),
            'school_name': data['school_name'],
            'start_time': ist_display_str,
            'total_students': total_students,
            'sessions_enabled': len(processed_sessions)
        }), 201

    except Exception as e:
        print(f"❌ Error starting audit: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Internal server error'}), 500


@school_audit_bp.route('/end-audit', methods=['POST'])
def end_audit():
    """End school audit session"""
    try:
        data = request.get_json()

        required_fields = ['audit_id', 'end_image', 'timestamp', 'sessions_completed',
                           'teacher_count', 'auditor_remarks']

        missing_fields = []
        for field in required_fields:
            if field not in data or data[field] is None:
                missing_fields.append(field)

        if missing_fields:
            return jsonify({'error': f'Missing required fields: {missing_fields}'}), 400

        # Find the audit record
        audit = mongo.db.school_audits.find_one({"_id": ObjectId(data['audit_id'])})

        if not audit:
            return jsonify({'error': 'Audit record not found'}), 404

        if audit['status'] != 'in_progress':
            return jsonify({'error': 'Audit is not in progress'}), 400

        # Process timestamp
        timestamp_str = data['timestamp']
        try:
            dt_from_frontend = datetime.fromisoformat(timestamp_str)
            if dt_from_frontend.tzinfo is None:
                localized_dt = IST_TZ.localize(dt_from_frontend)
            else:
                localized_dt = dt_from_frontend.astimezone(IST_TZ)
        except ValueError:
            return jsonify({'error': 'Invalid timestamp format'}), 400

        ist_display_str = localized_dt.strftime('%d %b %Y, %I:%M %p IST')

        # Upload end image to OneDrive
        end_filename = generate_unique_filename(audit['user_email'], 'audit_end', 'jpg')
        end_file_id = upload_image_to_onedrive(data['end_image'], end_filename)

        if not end_file_id:
            return jsonify({'error': 'Failed to upload end image'}), 500

        # Calculate session duration
        try:
            start_time = datetime.strptime(audit['start_timestamp'].split(',')[1].strip(), '%I:%M %p IST')
            end_time = datetime.strptime(ist_display_str.split(',')[1].strip(), '%I:%M %p IST')

            # Handle day boundary crossing
            if end_time < start_time:
                end_time += timedelta(days=1)

            duration_minutes = int((end_time - start_time).total_seconds() / 60)
        except Exception as duration_error:
            print(f"⚠️ Error calculating duration: {str(duration_error)}")
            duration_minutes = 0

        # Update audit record
        update_data = {
            "end_timestamp": ist_display_str,
            "end_image_file_id": end_file_id,
            "sessions_completed": int(data['sessions_completed']),
            "teacher_count": int(data['teacher_count']),
            "auditor_remarks": data['auditor_remarks'],
            "session_duration_minutes": duration_minutes,
            "status": "completed",
            "completed_at": localized_dt
        }

        result = mongo.db.school_audits.update_one(
            {"_id": ObjectId(data['audit_id'])},
            {"$set": update_data}
        )

        if result.modified_count == 0:
            return jsonify({'error': 'Failed to update audit record'}), 500

        return jsonify({
            'message': 'School audit completed successfully',
            'audit_id': data['audit_id'],
            'school_name': audit['school_name'],
            'duration_minutes': duration_minutes,
            'end_time': ist_display_str
        }), 200

    except Exception as e:
        print(f"❌ Error ending audit: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Internal server error'}), 500


@school_audit_bp.route('/image/<path:file_id>')
def get_audit_image(file_id):
    """Enhanced proxy endpoint that handles both file IDs and SharePoint URLs"""
    try:
        # Decode the file_id in case it's URL encoded
        decoded_file_id = urllib.parse.unquote(file_id)

        print(f"🔍 Image request - Original: {file_id}")
        print(f"🔍 Image request - Decoded: {decoded_file_id}")

        if not decoded_file_id or decoded_file_id.startswith('UPLOAD_FAILED'):
            print(f"❌ Invalid file ID: {decoded_file_id}")
            return jsonify({'error': 'Invalid file ID'}), 400

        # Get resized parameter
        resize = request.args.get('resize', 'true').lower() == 'true'

        # Use the enhanced function that handles both file IDs and SharePoint URLs
        image_content = get_onedrive_image_content(decoded_file_id, resize=resize)

        if not image_content:
            print(f"❌ Image not found for: {decoded_file_id}")
            return jsonify({'error': 'Image not found'}), 404

        # Return image with appropriate headers
        response = Response(
            image_content,
            mimetype='image/jpeg',
            headers={
                'Cache-Control': 'public, max-age=86400',  # Cache for 1 day
                'Content-Type': 'image/jpeg'
            }
        )
        return response

    except Exception as e:
        print(f"❌ Error serving image {file_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Failed to load image'}), 500


@school_audit_bp.route('/audits/<user_email>', methods=['GET'])
def get_user_audits(user_email):
    """Get audit history for a user"""
    try:
        # Get query parameters
        status = request.args.get('status', 'all')  # all, in_progress, completed
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')

        query = {"user_email": user_email}

        if status != 'all':
            query["status"] = status

        if date_from and date_to:
            query["audit_date"] = {
                "$gte": date_from,
                "$lte": date_to
            }

        audits = list(mongo.db.school_audits.find(query).sort('created_at', -1))

        # Format response with image proxy URLs
        base_url = request.url_root.rstrip('/')
        print(f"DEBUG: Base URL for image generation: {base_url}")
        print(f"DEBUG: Found {len(audits)} audits to format")

        formatted_audits = [format_audit_response(audit, base_url) for audit in audits]

        return jsonify({
            'audits': formatted_audits,
            'user_email': user_email,
            'total_count': len(formatted_audits)
        }), 200

    except Exception as e:
        print(f"❌ Error fetching audits: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Internal server error'}), 500


@school_audit_bp.route('/current-audit/<user_email>', methods=['GET'])
def get_current_audit(user_email):
    """Get current in-progress audit for a user"""
    try:
        today_date = datetime.now(IST_TZ).strftime('%d %b %Y')

        audit = mongo.db.school_audits.find_one({
            "user_email": user_email,
            "audit_date": today_date,
            "status": "in_progress"
        })

        if not audit:
            return jsonify({
                'message': 'No audit in progress',
                'current_audit': None
            }), 200

        # Format response
        base_url = request.url_root.rstrip('/')
        formatted_audit = format_audit_response(audit, base_url)

        return jsonify({
            'current_audit': formatted_audit,
            'user_email': user_email
        }), 200

    except Exception as e:
        print(f"❌ Error fetching current audit: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@school_audit_bp.route('/audit-summary/<user_email>', methods=['GET'])
def get_audit_summary(user_email):
    """Get audit summary statistics for a user"""
    try:
        # Get date range from query params
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        query = {"user_email": user_email}

        if start_date and end_date:
            query["audit_date"] = {
                "$gte": start_date,
                "$lte": end_date
            }

        audits = list(mongo.db.school_audits.find(query))

        # Calculate statistics
        total_audits = len(audits)
        completed_audits = len([a for a in audits if a['status'] == 'completed'])
        in_progress_audits = len([a for a in audits if a['status'] == 'in_progress'])

        total_students_reached = 0
        total_sachets_distributed = 0
        total_sessions_completed = 0
        unique_schools = set()
        unique_cities = set()

        for audit in audits:
            # Use new total_students field or calculate from sessions
            if 'total_students' in audit:
                total_students_reached += audit['total_students']
            else:
                # Fallback: calculate from sessions or old structure
                sessions = audit.get('sessions', {})
                if isinstance(sessions, dict):
                    for session in sessions.values():
                        if isinstance(session, dict) and session.get('enabled'):
                            total_students_reached += int(session.get('studentsCount', 0) or 0)
                else:
                    # Old structure fallback
                    total_students_reached += (
                            audit.get('students_session1', 0) +
                            audit.get('students_session2', 0) +
                            audit.get('students_session3', 0)
                    )

            total_sachets_distributed += audit.get('boost_sachets_given', 0)
            if audit['status'] == 'completed':
                total_sessions_completed += audit.get('sessions_completed', 0)
            unique_schools.add(audit['school_name'])
            unique_cities.add(audit['city'])

        summary = {
            'total_audits': total_audits,
            'completed_audits': completed_audits,
            'in_progress_audits': in_progress_audits,
            'completion_rate': round((completed_audits / max(total_audits, 1)) * 100, 1),
            'total_students_reached': total_students_reached,
            'total_sachets_distributed': total_sachets_distributed,
            'total_sessions_completed': total_sessions_completed,
            'unique_schools_visited': len(unique_schools),
            'unique_cities_covered': len(unique_cities),
            'average_students_per_audit': round(total_students_reached / max(completed_audits, 1), 1),
            'schools_list': list(unique_schools),
            'cities_list': list(unique_cities)
        }

        return jsonify(summary), 200

    except Exception as e:
        print(f"❌ Error generating audit summary: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


# Replace your export_audits_excel function with this enhanced version

@school_audit_bp.route('/export-audits', methods=['POST'])
def export_audits_excel():
    """Export audit data to Excel with ALL image URLs included"""
    try:
        data = request.get_json()
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        controller_email = data.get('controller_email')
        user_email = data.get('user_email')

        if not start_date or not end_date:
            return jsonify({"error": "Start date and end date are required"}), 400

        # Get controller users if specified
        controller_user_emails = []
        if controller_email:
            try:
                import requests
                auth_header = request.headers.get('Authorization')
                if not auth_header:
                    return jsonify({"error": "Authorization header required"}), 401

                controller_users_response = requests.get(
                    f'https://field-app-346502099828.asia-south1.run.app/api/attendance/users?controllerEmail={controller_email}',
                    headers={'Authorization': auth_header}
                )

                if controller_users_response.status_code == 200:
                    controller_data = controller_users_response.json()
                    controller_user_emails = controller_data.get('users', [])
                else:
                    return jsonify({"error": "Failed to fetch users under controller"}), 400

            except Exception as e:
                print(f"❌ Error fetching controller users: {str(e)}")
                return jsonify({"error": "Failed to fetch controller users"}), 500

        # Build query
        query = {}
        if controller_email and controller_user_emails:
            if user_email and user_email in controller_user_emails:
                query['user_email'] = user_email
            else:
                query['user_email'] = {'$in': controller_user_emails}
        elif user_email:
            query['user_email'] = user_email

        query['audit_date'] = {'$gte': start_date, '$lte': end_date}

        audits = list(mongo.db.school_audits.find(query).sort('created_at', -1))

        if len(audits) == 0:
            return jsonify({"error": "No audit data found for the selected criteria"}), 404

        # Create Excel file
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})

        # Define formats
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#245132',
            'font_color': 'white',
            'border': 1,
            'align': 'center',
            'valign': 'vcenter'
        })

        cell_format = workbook.add_format({
            'border': 1,
            'align': 'center',
            'valign': 'vcenter'
        })

        # Create main worksheet
        worksheet = workbook.add_worksheet("School Audits")

        # ENHANCED headers with ALL image URLs
        headers = [
            'Audit ID', 'Date', 'Auditor Email', 'School Name', 'City', 'Latitude', 'Longitude',
            'Start Time', 'End Time', 'Duration (min)', 'Promoters Count', 'Total Students',
            'Boost Sachets', 'Giveaways', 'Sessions Completed', 'Teacher Count',

            # Session 1 data + images
            'Session 1 Enabled', 'Session 1 Students', 'Session 1 Winner', 'Session 1 Winner Class',
            'Session 1 Start Selfie URL', 'Session 1 End Selfie URL', 'Session 1 Winner Photo URL',
            'Session 1 Distribution Photo URL',

            # Session 2 data + images
            'Session 2 Enabled', 'Session 2 Students', 'Session 2 Winner', 'Session 2 Winner Class',
            'Session 2 Start Selfie URL', 'Session 2 End Selfie URL', 'Session 2 Winner Photo URL',
            'Session 2 Distribution Photo URL',

            # Session 3 data + images
            'Session 3 Enabled', 'Session 3 Students', 'Session 3 Winner', 'Session 3 Winner Class',
            'Session 3 Start Selfie URL', 'Session 3 End Selfie URL', 'Session 3 Winner Photo URL',
            'Session 3 Distribution Photo URL',

            # Main audit images
            'Auditor Remarks', 'Status', 'Start Image URL', 'End Image URL', 'Audit Sheet URL'
        ]

        # Write headers
        for col, header in enumerate(headers):
            worksheet.write(0, col, header, header_format)

        # Write data
        row = 1
        base_url = request.url_root.rstrip('/')

        for audit in audits:
            # Format the audit to generate image URLs
            formatted_audit = format_audit_response(dict(audit), base_url)

            # Calculate total students
            total_students = formatted_audit.get('total_students', 0)
            if not total_students:
                # Fallback calculation
                sessions = formatted_audit.get('sessions', {})
                if isinstance(sessions, dict):
                    total_students = sum(
                        int(session.get('studentsCount', 0) or 0)
                        for session in sessions.values()
                        if session.get('enabled', False)
                    )

            # Get session data with image URLs
            sessions = formatted_audit.get('sessions', {})
            session1 = sessions.get('session1', {})
            session2 = sessions.get('session2', {})
            session3 = sessions.get('session3', {})

            # Extract location coordinates
            location = formatted_audit.get('location', {})
            latitude = location.get('latitude', '') if isinstance(location, dict) else ''
            longitude = location.get('longitude', '') if isinstance(location, dict) else ''

            data_row = [
                str(formatted_audit.get('_id', '')),
                formatted_audit.get('audit_date', ''),
                formatted_audit.get('user_email', ''),
                formatted_audit.get('school_name', ''),
                formatted_audit.get('city', ''),
                latitude,
                longitude,
                formatted_audit.get('start_timestamp', '').split(',')[1].strip() if formatted_audit.get(
                    'start_timestamp') else '',
                formatted_audit.get('end_timestamp', '').split(',')[1].strip() if formatted_audit.get(
                    'end_timestamp') else '',
                formatted_audit.get('session_duration_minutes', 0),
                formatted_audit.get('promoters_count', 0),
                total_students,
                formatted_audit.get('boost_sachets_given', 0),
                formatted_audit.get('giveaways_given', ''),
                formatted_audit.get('sessions_completed', 0),
                formatted_audit.get('teacher_count', 0),

                # Session 1 data + ALL image URLs
                'Yes' if session1.get('enabled', False) else 'No',
                session1.get('studentsCount', '') if session1.get('enabled', False) else '',
                session1.get('winnerName', '') if session1.get('enabled', False) else '',
                session1.get('winnerClass', '') if session1.get('enabled', False) else '',
                session1.get('startSelfieUrl', ''),  # NEW: Session 1 start selfie
                session1.get('endSelfieUrl', ''),  # NEW: Session 1 end selfie
                session1.get('winnerPhotoUrl', ''),  # NEW: Session 1 winner photo
                session1.get('sachetDistributionPhotoUrl', ''),  # NEW: Session 1 distribution photo

                # Session 2 data + ALL image URLs
                'Yes' if session2.get('enabled', False) else 'No',
                session2.get('studentsCount', '') if session2.get('enabled', False) else '',
                session2.get('winnerName', '') if session2.get('enabled', False) else '',
                session2.get('winnerClass', '') if session2.get('enabled', False) else '',
                session2.get('startSelfieUrl', ''),  # NEW: Session 2 start selfie
                session2.get('endSelfieUrl', ''),  # NEW: Session 2 end selfie
                session2.get('winnerPhotoUrl', ''),  # NEW: Session 2 winner photo
                session2.get('sachetDistributionPhotoUrl', ''),  # NEW: Session 2 distribution photo

                # Session 3 data + ALL image URLs
                'Yes' if session3.get('enabled', False) else 'No',
                session3.get('studentsCount', '') if session3.get('enabled', False) else '',
                session3.get('winnerName', '') if session3.get('enabled', False) else '',
                session3.get('winnerClass', '') if session3.get('enabled', False) else '',
                session3.get('startSelfieUrl', ''),  # NEW: Session 3 start selfie
                session3.get('endSelfieUrl', ''),  # NEW: Session 3 end selfie
                session3.get('winnerPhotoUrl', ''),  # NEW: Session 3 winner photo
                session3.get('sachetDistributionPhotoUrl', ''),  # NEW: Session 3 distribution photo

                # Main audit data
                formatted_audit.get('auditor_remarks', ''),
                formatted_audit.get('status', ''),
                formatted_audit.get('start_image_url', ''),  # Main start image
                formatted_audit.get('end_image_url', ''),  # Main end image
                formatted_audit.get('audit_sheet_image_url', '')  # Audit sheet image
            ]

            for col, value in enumerate(data_row):
                worksheet.write(row, col, value, cell_format)
            row += 1

        # Add summary row
        summary_row = row + 1
        worksheet.write(summary_row, 0, 'SUMMARY:', header_format)
        worksheet.write(summary_row, 1, f'Total Audits: {len(audits)}', header_format)
        worksheet.write(summary_row, 2, f'Controller: {controller_email}', header_format)
        worksheet.write(summary_row, 3, f'Date Range: {start_date} to {end_date}', header_format)

        # Calculate totals
        total_students_all = 0
        total_sachets_all = 0
        completed_audits = 0

        for audit in audits:
            if audit.get('status') == 'completed':
                completed_audits += 1

            # Calculate total students
            if 'total_students' in audit:
                total_students_all += audit['total_students']
            else:
                sessions = audit.get('sessions', {})
                if isinstance(sessions, dict):
                    for session in sessions.values():
                        if isinstance(session, dict) and session.get('enabled'):
                            total_students_all += int(session.get('studentsCount', 0) or 0)

            total_sachets_all += audit.get('boost_sachets_given', 0)

        worksheet.write(summary_row + 1, 0, 'Total Students Reached:', cell_format)
        worksheet.write(summary_row + 1, 1, total_students_all, cell_format)
        worksheet.write(summary_row + 1, 2, 'Total Sachets Distributed:', cell_format)
        worksheet.write(summary_row + 1, 3, total_sachets_all, cell_format)
        worksheet.write(summary_row + 1, 4, 'Completed Audits:', cell_format)
        worksheet.write(summary_row + 1, 5, completed_audits, cell_format)

        # Set column widths - UPDATED for new columns
        worksheet.set_column(0, 0, 25)  # Audit ID
        worksheet.set_column(1, 1, 15)  # Date
        worksheet.set_column(2, 2, 25)  # Email
        worksheet.set_column(3, 3, 30)  # School name
        worksheet.set_column(4, 4, 20)  # City
        worksheet.set_column(5, 6, 12)  # Lat/Long
        worksheet.set_column(7, 8, 15)  # Times
        worksheet.set_column(9, 16, 12)  # Basic numbers
        worksheet.set_column(17, 40, 15)  # Session data and image URLs
        worksheet.set_column(41, 41, 40)  # Remarks
        worksheet.set_column(42, 42, 12)  # Status
        worksheet.set_column(43, 45, 50)  # Main image URLs

        workbook.close()
        output.seek(0)

        controller_suffix = f"_{controller_email.split('@')[0]}" if controller_email else ""
        filename = f"school_audits_complete{controller_suffix}_{start_date}_{end_date}.xlsx"

        return send_file(
            output,
            download_name=filename,
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        print(f"❌ Error exporting audits: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to generate Excel report: {str(e)}"}), 500


@school_audit_bp.route('/controller-audit-summary/<controller_email>', methods=['GET'])
def get_controller_audit_summary(controller_email):
    """Get audit summary statistics for all users under a controller"""
    try:
        # Get date range from query params
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        # Get auth token from request headers
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({"error": "Authorization header required"}), 401

        # First, get all users under this controller
        try:
            import requests

            controller_users_response = requests.get(
                f'https://field-app-346502099828.asia-south1.run.app/api/attendance/users?controllerEmail={controller_email}',
                headers={'Authorization': auth_header}
            )

            if controller_users_response.status_code != 200:
                return jsonify({"error": "Failed to fetch users under controller"}), 400

            controller_data = controller_users_response.json()
            controller_user_emails = controller_data.get('users', [])

        except Exception as e:
            print(f"❌ Error fetching controller users: {str(e)}")
            return jsonify({"error": "Failed to fetch controller users"}), 500

        if not controller_user_emails:
            return jsonify({
                "message": "No users found under this controller",
                "summary": {
                    'total_audits': 0,
                    'completed_audits': 0,
                    'in_progress_audits': 0,
                    'completion_rate': 0,
                    'total_students_reached': 0,
                    'total_sachets_distributed': 0,
                    'total_sessions_completed': 0,
                    'unique_schools_visited': 0,
                    'unique_cities_covered': 0,
                    'average_students_per_audit': 0,
                    'schools_list': [],
                    'cities_list': [],
                    'users_list': []
                }
            }), 200

        # Build query for all users under the controller
        query = {"user_email": {"$in": controller_user_emails}}

        if start_date and end_date:
            query["audit_date"] = {
                "$gte": start_date,
                "$lte": end_date
            }

        audits = list(mongo.db.school_audits.find(query))

        # Calculate statistics
        total_audits = len(audits)
        completed_audits = len([a for a in audits if a['status'] == 'completed'])
        in_progress_audits = len([a for a in audits if a['status'] == 'in_progress'])

        total_students_reached = 0
        total_sachets_distributed = 0
        total_sessions_completed = 0
        unique_schools = set()
        unique_cities = set()
        active_users = set()

        for audit in audits:
            # Use new total_students field or calculate from sessions
            if 'total_students' in audit:
                total_students_reached += audit['total_students']
            else:
                # Fallback: calculate from sessions
                sessions = audit.get('sessions', {})
                if isinstance(sessions, dict):
                    for session in sessions.values():
                        if isinstance(session, dict) and session.get('enabled'):
                            total_students_reached += int(session.get('studentsCount', 0) or 0)

            total_sachets_distributed += audit.get('boost_sachets_given', 0)
            if audit['status'] == 'completed':
                total_sessions_completed += audit.get('sessions_completed', 0)
            unique_schools.add(audit['school_name'])
            unique_cities.add(audit['city'])
            active_users.add(audit['user_email'])

        summary = {
            'total_audits': total_audits,
            'completed_audits': completed_audits,
            'in_progress_audits': in_progress_audits,
            'completion_rate': round((completed_audits / max(total_audits, 1)) * 100, 1),
            'total_students_reached': total_students_reached,
            'total_sachets_distributed': total_sachets_distributed,
            'total_sessions_completed': total_sessions_completed,
            'unique_schools_visited': len(unique_schools),
            'unique_cities_covered': len(unique_cities),
            'average_students_per_audit': round(total_students_reached / max(completed_audits, 1), 1),
            'schools_list': list(unique_schools),
            'cities_list': list(unique_cities),
            'active_users': len(active_users),
            'total_users_under_controller': len(controller_user_emails),
            'users_list': list(active_users)
        }

        return jsonify({
            'controller_email': controller_email,
            'summary': summary
        }), 200

    except Exception as e:
        print(f"❌ Error generating controller audit summary: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@school_audit_bp.route('/edit-audit/<audit_id>', methods=['PUT'])
def edit_audit(audit_id):
    """Edit audit - only allowed for today's audits"""
    try:
        data = request.get_json()

        # Get the audit record
        audit = mongo.db.school_audits.find_one({"_id": ObjectId(audit_id)})
        if not audit:
            return jsonify({'error': 'Audit record not found'}), 404

        # Check if audit is from today
        today_date = datetime.now(IST_TZ).strftime('%d %b %Y')
        if audit['audit_date'] != today_date:
            return jsonify({'error': 'Only today\'s audits can be edited'}), 403

        # Store original data for logging
        original_data = dict(audit)
        original_data['_id'] = str(original_data['_id'])
        if 'created_at' in original_data:
            original_data['created_at'] = original_data['created_at'].isoformat()
        if 'completed_at' in original_data:
            original_data['completed_at'] = original_data['completed_at'].isoformat()

        # Prepare update fields
        update_fields = {}
        updatable_fields = [
            'school_name', 'city', 'promoters_count', 'boost_sachets_given',
            'giveaways_given', 'sessions_completed', 'teacher_count', 'auditor_remarks'
        ]

        for field in updatable_fields:
            if field in data:
                if field in ['promoters_count', 'boost_sachets_given', 'sessions_completed', 'teacher_count']:
                    update_fields[field] = int(data[field])
                else:
                    update_fields[field] = data[field]

        # Handle sessions data update
        if 'sessions' in data:
            processed_sessions = process_sessions_data(data['sessions'], audit['user_email'])
            update_fields['sessions'] = processed_sessions

            # Recalculate total students
            total_students = sum(
                int(session.get('studentsCount', 0) or 0)
                for session in processed_sessions.values()
            )
            update_fields['total_students'] = total_students

        # Handle image updates if provided
        user_email = audit['user_email']

        if data.get('start_image'):
            start_filename = generate_unique_filename(user_email, 'audit_start_updated', 'jpg')
            update_fields['start_image_file_id'] = upload_image_to_onedrive(data['start_image'], start_filename)

        if data.get('end_image'):
            end_filename = generate_unique_filename(user_email, 'audit_end_updated', 'jpg')
            update_fields['end_image_file_id'] = upload_image_to_onedrive(data['end_image'], end_filename)

        if data.get('audit_sheet_image'):
            sheet_filename = generate_unique_filename(user_email, 'audit_sheet_updated', 'jpg')
            update_fields['audit_sheet_image_file_id'] = upload_image_to_onedrive(data['audit_sheet_image'],
                                                                                  sheet_filename)

        if not update_fields:
            return jsonify({'error': 'No valid fields to update'}), 400

        # Add last modified timestamp
        update_fields['last_modified_at'] = datetime.now(IST_TZ)
        update_fields['last_modified_by'] = user_email

        # Update the audit record
        result = mongo.db.school_audits.update_one(
            {"_id": ObjectId(audit_id)},
            {"$set": update_fields}
        )

        if result.modified_count == 0:
            return jsonify({'error': 'Failed to update audit record'}), 500

        # Log the edit action
        edit_log = {
            "action": "EDIT",
            "audit_id": audit_id,
            "original_data": original_data,
            "updated_fields": update_fields,
            "performed_by": user_email,
            "performed_at": datetime.now(IST_TZ),
            "ip_address": request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR')),
            "user_agent": request.headers.get('User-Agent'),
            "school_name": audit['school_name'],
            "audit_date": audit['audit_date']
        }

        mongo.db.audit_logs.insert_one(edit_log)

        # Get updated audit record
        updated_audit = mongo.db.school_audits.find_one({"_id": ObjectId(audit_id)})
        base_url = request.url_root.rstrip('/')
        formatted_audit = format_audit_response(updated_audit, base_url)

        return jsonify({
            'message': 'Audit updated successfully',
            'audit_id': audit_id,
            'updated_audit': formatted_audit,
            'updated_fields': list(update_fields.keys())
        }), 200

    except Exception as e:
        print(f"❌ Error editing audit: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Internal server error'}), 500


@school_audit_bp.route('/delete-audit/<audit_id>', methods=['DELETE'])
def delete_audit(audit_id):
    """Delete audit - only allowed for today's audits"""
    try:
        data = request.get_json() or {}
        deletion_reason = data.get('reason', 'No reason provided')

        # Get the audit record
        audit = mongo.db.school_audits.find_one({"_id": ObjectId(audit_id)})
        if not audit:
            return jsonify({'error': 'Audit record not found'}), 404

        # Check if audit is from today
        today_date = datetime.now(IST_TZ).strftime('%d %b %Y')
        if audit['audit_date'] != today_date:
            return jsonify({'error': 'Only today\'s audits can be deleted'}), 403

        # Store complete audit data for logging
        audit_data = dict(audit)
        audit_data['_id'] = str(audit_data['_id'])
        if 'created_at' in audit_data:
            audit_data['created_at'] = audit_data['created_at'].isoformat()
        if 'completed_at' in audit_data:
            audit_data['completed_at'] = audit_data['completed_at'].isoformat()

        # Log the deletion before deleting
        deletion_log = {
            "action": "DELETE",
            "audit_id": audit_id,
            "deleted_audit_data": audit_data,
            "deletion_reason": deletion_reason,
            "performed_by": audit['user_email'],
            "performed_at": datetime.now(IST_TZ),
            "ip_address": request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR')),
            "user_agent": request.headers.get('User-Agent'),
            "school_name": audit['school_name'],
            "audit_date": audit['audit_date'],
            "recoverable": True
        }

        mongo.db.audit_logs.insert_one(deletion_log)

        # Delete the audit record
        result = mongo.db.school_audits.delete_one({"_id": ObjectId(audit_id)})

        if result.deleted_count == 0:
            return jsonify({'error': 'Failed to delete audit record'}), 500

        return jsonify({
            'message': 'Audit deleted successfully',
            'audit_id': audit_id,
            'school_name': audit['school_name'],
            'deletion_logged': True
        }), 200

    except Exception as e:
        print(f"❌ Error deleting audit: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Internal server error'}), 500


@school_audit_bp.route('/audit-logs/<user_email>', methods=['GET'])
def get_audit_logs(user_email):
    """Get audit logs for a user"""
    try:
        # Get query parameters
        action = request.args.get('action', 'all')  # all, EDIT, DELETE
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        limit = int(request.args.get('limit', 50))

        query = {"performed_by": user_email}

        if action != 'all':
            query["action"] = action.upper()

        if date_from and date_to:
            try:
                from_date = datetime.fromisoformat(date_from)
                to_date = datetime.fromisoformat(date_to)
                query["performed_at"] = {
                    "$gte": from_date,
                    "$lte": to_date
                }
            except ValueError:
                return jsonify({'error': 'Invalid date format'}), 400

        logs = list(mongo.db.audit_logs.find(query).sort('performed_at', -1).limit(limit))

        # Format response
        formatted_logs = []
        for log in logs:
            log['_id'] = str(log['_id'])
            if 'performed_at' in log:
                log['performed_at'] = log['performed_at'].isoformat()

            # Remove sensitive data for response
            if 'deleted_audit_data' in log:
                deleted_data = log['deleted_audit_data']
                log['deleted_audit_summary'] = {
                    'school_name': deleted_data.get('school_name'),
                    'city': deleted_data.get('city'),
                    'audit_date': deleted_data.get('audit_date'),
                    'status': deleted_data.get('status'),
                    'students_total': deleted_data.get('total_students', 0)
                }
                del log['deleted_audit_data']

            formatted_logs.append(log)

        return jsonify({
            'logs': formatted_logs,
            'user_email': user_email,
            'total_count': len(formatted_logs)
        }), 200

    except Exception as e:
        print(f"❌ Error fetching audit logs: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


# Migration and maintenance endpoints
@school_audit_bp.route('/migrate-legacy-audits', methods=['POST'])
def migrate_legacy_audits():
    """Migration endpoint to convert old audit structure to new session structure"""
    try:
        migrated_count = 0
        audits = mongo.db.school_audits.find({
            "sessions": {"$exists": False},  # Audits without new sessions structure
            "$or": [
                {"students_session1": {"$exists": True}},
                {"students_session2": {"$exists": True}},
                {"students_session3": {"$exists": True}}
            ]
        })

        for audit in audits:
            # Create new sessions structure from old data
            sessions = {}

            # Convert session 1
            if audit.get('students_session1'):
                sessions['session1'] = {
                    'enabled': True,
                    'name': 'Class 1-5',
                    'studentsCount': str(audit.get('students_session1', 0)),
                    'sachetCount': '',
                    'winnerName': audit.get('winners_session1', ''),
                    'winnerClass': '',
                    'startSelfie': None,
                    'endSelfie': None,
                    'winnerPhoto': None,
                    'sachetDistributionPhoto': None
                }

            # Convert session 2
            if audit.get('students_session2'):
                sessions['session2'] = {
                    'enabled': True,
                    'name': 'Class 6-8',
                    'studentsCount': str(audit.get('students_session2', 0)),
                    'sachetCount': '',
                    'winnerName': audit.get('winners_session2', ''),
                    'winnerClass': '',
                    'startSelfie': None,
                    'endSelfie': None,
                    'winnerPhoto': None,
                    'sachetDistributionPhoto': None
                }

            # Convert session 3
            if audit.get('students_session3'):
                sessions['session3'] = {
                    'enabled': True,
                    'name': 'Class 9+',
                    'studentsCount': str(audit.get('students_session3', 0)),
                    'sachetCount': '',
                    'winnerName': audit.get('winners_session3', ''),
                    'winnerClass': '',
                    'startSelfie': None,
                    'endSelfie': None,
                    'winnerPhoto': None,
                    'sachetDistributionPhoto': None
                }

            # Calculate total students
            total_students = (
                    audit.get('students_session1', 0) +
                    audit.get('students_session2', 0) +
                    audit.get('students_session3', 0)
            )

            # Update the audit record
            update_fields = {
                'sessions': sessions,
                'total_students': total_students,
                'migrated_at': datetime.now(IST_TZ),
                'migration_version': '2.0'
            }

            mongo.db.school_audits.update_one(
                {"_id": audit['_id']},
                {"$set": update_fields}
            )
            migrated_count += 1

        return jsonify({
            'message': f'Migration completed. Migrated {migrated_count} audit records to new session structure.',
            'migrated_count': migrated_count
        }), 200

    except Exception as e:
        print(f"❌ Error during migration: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Migration failed'}), 500


# Existing endpoints for SharePoint URL conversion and other maintenance tasks
@school_audit_bp.route('/convert-sharepoint-urls', methods=['POST'])
def convert_sharepoint_urls():
    """Endpoint to convert SharePoint URLs to OneDrive file IDs"""
    try:
        result = convert_sharepoint_urls_to_file_ids(mongo.db)

        return jsonify({
            'message': 'SharePoint URL conversion completed',
            'converted_count': result.get('converted_count', 0),
            'failed_count': result.get('failed_count', 0),
            'total_processed': result.get('total_audits_processed', 0)
        }), 200

    except Exception as e:
        print(f"❌ Error converting SharePoint URLs: {str(e)}")
        return jsonify({'error': 'Failed to convert SharePoint URLs'}), 500