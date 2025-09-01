from flask import Blueprint, request, jsonify
from app import mongo
from datetime import datetime, timedelta
import pytz
from bson.objectid import ObjectId

school_assignment_bp = Blueprint('school_assignment', __name__)
IST_TZ = pytz.timezone('Asia/Kolkata')


@school_assignment_bp.route('/assign-schools', methods=['POST'])
def assign_schools():
    """Controller assigns schools to field trainers"""
    try:
        data = request.get_json()

        required_fields = ['controller_email', 'trainer_email', 'assignment_date', 'schools']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({'error': f'Missing required field: {field}'}), 400

        controller_email = data['controller_email']
        trainer_email = data['trainer_email']
        assignment_date = data['assignment_date']
        schools = data['schools']  # Array of school objects
        allow_overwrite = data.get('allow_overwrite', False)  # New parameter

        # Validate assignment date (can be up to 7 days in future)
        try:
            date_obj = datetime.strptime(assignment_date, '%Y-%m-%d')
            today = datetime.now().date()
            if date_obj.date() < today:
                return jsonify({'error': 'Cannot assign schools for past dates'}), 400
            if (date_obj.date() - today).days > 7:
                return jsonify({'error': 'Cannot assign schools more than 7 days in advance'}), 400
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

        # Validate schools data
        for school in schools:
            if not school.get('school_name') or not school.get('city'):
                return jsonify({'error': 'Each school must have school_name and city'}), 400

        # Check if trainer exists
        trainer = mongo.db.users.find_one({'email': trainer_email})
        if not trainer:
            return jsonify({'error': 'Trainer not found'}), 404

        # Check if assignment already exists for this date
        existing_assignment = mongo.db.school_assignments.find_one({
            'trainer_email': trainer_email,
            'assignment_date': assignment_date,
            'status': 'active'
        })

        if existing_assignment and not allow_overwrite:
            # Return conflict with existing assignment details
            return jsonify({
                'error': 'Assignment already exists for this date',
                'conflict': True,
                'existing_assignment': {
                    'assignment_id': str(existing_assignment['_id']),
                    'schools': existing_assignment['schools'],
                    'schools_count': len(existing_assignment['schools']),
                    'created_by': existing_assignment['controller_email'],
                    'created_at': existing_assignment[
                        'created_at'].isoformat() if 'created_at' in existing_assignment else None
                }
            }), 409  # HTTP 409 Conflict

        assignment_record = {
            'controller_email': controller_email,
            'trainer_email': trainer_email,
            'assignment_date': assignment_date,
            'schools': schools,
            'created_at': datetime.now(IST_TZ),
            'status': 'active'
        }

        if existing_assignment and allow_overwrite:
            # Update existing assignment
            assignment_record['updated_at'] = datetime.now(IST_TZ)
            assignment_record['previous_assignment_id'] = str(existing_assignment['_id'])

            result = mongo.db.school_assignments.update_one(
                {'_id': existing_assignment['_id']},
                {'$set': assignment_record}
            )
            message = f'School assignment updated successfully (overwrote previous assignment with {len(existing_assignment["schools"])} schools)'
        else:
            # Create new assignment
            result = mongo.db.school_assignments.insert_one(assignment_record)
            message = 'Schools assigned successfully'

        return jsonify({
            'message': message,
            'assignment_date': assignment_date,
            'trainer_email': trainer_email,
            'schools_count': len(schools),
            'overwritten': existing_assignment is not None and allow_overwrite
        }), 201

    except Exception as e:
        print(f"❌ Error assigning schools: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


# Add new endpoint to check for existing assignments
@school_assignment_bp.route('/check-assignment/<trainer_email>/<assignment_date>', methods=['GET'])
def check_existing_assignment(trainer_email, assignment_date):
    """Check if an assignment already exists for a trainer on a specific date"""
    try:
        # Validate date format
        try:
            datetime.strptime(assignment_date, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

        existing_assignment = mongo.db.school_assignments.find_one({
            'trainer_email': trainer_email,
            'assignment_date': assignment_date,
            'status': 'active'
        })

        if existing_assignment:
            return jsonify({
                'exists': True,
                'assignment': {
                    'assignment_id': str(existing_assignment['_id']),
                    'schools': existing_assignment['schools'],
                    'schools_count': len(existing_assignment['schools']),
                    'created_by': existing_assignment['controller_email'],
                    'created_at': existing_assignment[
                        'created_at'].isoformat() if 'created_at' in existing_assignment else None
                }
            }), 200
        else:
            return jsonify({'exists': False}), 200

    except Exception as e:
        print(f"❌ Error checking assignment: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@school_assignment_bp.route('/trainer-assignments/<trainer_email>', methods=['GET'])
def get_trainer_assignments(trainer_email):
    """Get school assignments for a specific trainer"""
    try:
        # Get date range (default to next 7 days)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        if not start_date:
            today = datetime.now().date()
            start_date = today.strftime('%Y-%m-%d')
            end_date = (today + timedelta(days=7)).strftime('%Y-%m-%d')

        query = {
            'trainer_email': trainer_email,
            'assignment_date': {
                '$gte': start_date,
                '$lte': end_date
            },
            'status': 'active'
        }

        assignments = list(mongo.db.school_assignments.find(query).sort('assignment_date', 1))

        # Convert ObjectId to string and format response
        formatted_assignments = []
        for assignment in assignments:
            assignment['_id'] = str(assignment['_id'])
            if 'created_at' in assignment:
                assignment['created_at'] = assignment['created_at'].isoformat()
            formatted_assignments.append(assignment)

        return jsonify({
            'assignments': formatted_assignments,
            'trainer_email': trainer_email
        }), 200

    except Exception as e:
        print(f"❌ Error fetching assignments: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@school_assignment_bp.route('/today-assignments/<trainer_email>', methods=['GET'])
def get_today_assignments(trainer_email):
    """Get today's school assignments with audit status for each school"""
    try:
        assignment_date = request.args.get('date', datetime.now().date().strftime('%Y-%m-%d'))

        # Validate date format
        try:
            datetime.strptime(assignment_date, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

        assignment = mongo.db.school_assignments.find_one({
            'trainer_email': trainer_email,
            'assignment_date': assignment_date,
            'status': 'active'
        })

        if not assignment:
            return jsonify({
                'message': 'No schools assigned for this date',
                'schools': [],
                'assignment_date': assignment_date,
                'summary': {
                    'total_assigned': 0,
                    'completed': 0,
                    'in_progress': 0,
                    'pending': 0
                }
            }), 200

        all_schools = assignment.get('schools', [])

        # Remove duplicates from assignment using school_name + city
        unique_schools = []
        seen_identifiers = set()

        for school in all_schools:
            identifier = f"{school['school_name']}|{school['city']}".lower()
            if identifier not in seen_identifiers:
                seen_identifiers.add(identifier)
                unique_schools.append(school)
            else:
                print(f"⚠️ Duplicate school removed: {school['school_name']} - {school['city']}")

        # Get audit status for each school in a single query
        # Create a list of all school identifiers for efficient querying
        school_identifiers = []
        for school in unique_schools:
            school_identifiers.append({
                'school_name': school['school_name'],
                'city': school['city'],
                'auditor_email': trainer_email
            })

        # Query all audits for these schools and trainer
        audit_query = {
            'auditor_email': trainer_email,
            '$or': [
                {
                    'school_name': school['school_name'],
                    'city': school['city']
                }
                for school in unique_schools
            ]
        }

        audits = list(mongo.db.school_audits.find(audit_query))

        # Create lookup dictionaries for audit status
        audit_lookup = {}

        for audit in audits:
            identifier = f"{audit['school_name']}|{audit['city']}".lower()

            # For completed audits, only consider those from today
            if audit['status'] == 'completed' and audit.get('audit_date') == assignment_date:
                audit_lookup[identifier] = {
                    'status': 'completed',
                    'audit_id': str(audit['_id']),
                    'start_timestamp': audit.get('start_timestamp'),
                    'completion_timestamp': audit.get('completion_timestamp'),
                    'audit_data': audit
                }
            # For in-progress audits, include regardless of date (they might be from previous days)
            elif audit['status'] == 'in_progress':
                # Only keep the most recent in-progress audit if multiple exist
                if identifier not in audit_lookup or audit_lookup[identifier]['status'] != 'in_progress':
                    audit_lookup[identifier] = {
                        'status': 'in_progress',
                        'audit_id': str(audit['_id']),
                        'start_timestamp': audit.get('start_timestamp'),
                        'completion_timestamp': None,
                        'audit_data': audit
                    }

        # Enhance each school with audit status
        enhanced_schools = []
        summary_counts = {'completed': 0, 'in_progress': 0, 'pending': 0}

        for school in unique_schools:
            identifier = f"{school['school_name']}|{school['city']}".lower()

            # Check audit status from lookup
            if identifier in audit_lookup:
                audit_info = audit_lookup[identifier]
                audit_status = audit_info['status']
                summary_counts[audit_status] += 1

                enhanced_school = {
                    **school,
                    'audit_status': audit_status,
                    'audit_id': audit_info['audit_id'],
                    'start_timestamp': audit_info['start_timestamp'],
                    'completion_timestamp': audit_info['completion_timestamp']
                }

                # If audit is in progress, include additional data for continuation
                if audit_status == 'in_progress':
                    enhanced_school['current_audit_data'] = audit_info['audit_data']
            else:
                # No audit found - school is pending
                audit_status = 'pending'
                summary_counts['pending'] += 1

                enhanced_school = {
                    **school,
                    'audit_status': audit_status,
                    'audit_id': None,
                    'start_timestamp': None,
                    'completion_timestamp': None
                }

            enhanced_schools.append(enhanced_school)

        # Convert ObjectId to string
        assignment['_id'] = str(assignment['_id'])
        if 'created_at' in assignment:
            assignment['created_at'] = assignment['created_at'].isoformat()

        return jsonify({
            'assignment': assignment,
            'schools': enhanced_schools,
            'assignment_date': assignment_date,
            'summary': {
                'total_assigned': len(unique_schools),
                'completed': summary_counts['completed'],
                'in_progress': summary_counts['in_progress'],
                'pending': summary_counts['pending']
            },
            'duplicates_removed': len(all_schools) - len(unique_schools)
        }), 200

    except Exception as e:
        print(f"❌ Error fetching today's assignments: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@school_assignment_bp.route('/controller-assignments/<controller_email>', methods=['GET'])
def get_controller_assignments(controller_email):
    """Get all assignments created by a controller"""
    try:
        date_filter = request.args.get('date_filter', 'upcoming')  # 'upcoming', 'all', 'past'

        query = {'controller_email': controller_email, 'status': 'active'}

        today = datetime.now().date().strftime('%Y-%m-%d')

        if date_filter == 'upcoming':
            query['assignment_date'] = {'$gte': today}
        elif date_filter == 'past':
            query['assignment_date'] = {'$lt': today}
        # 'all' doesn't add date filter

        assignments = list(mongo.db.school_assignments.find(query).sort('assignment_date', -1))

        # Format response with trainer details
        formatted_assignments = []
        for assignment in assignments:
            assignment['_id'] = str(assignment['_id'])
            if 'created_at' in assignment:
                assignment['created_at'] = assignment['created_at'].isoformat()

            # Get trainer details
            trainer = mongo.db.users.find_one({'email': assignment['trainer_email']})
            if trainer:
                assignment['trainer_name'] = trainer.get('full_name', 'Unknown')
            else:
                assignment['trainer_name'] = 'Unknown'

            formatted_assignments.append(assignment)

        return jsonify({
            'assignments': formatted_assignments,
            'controller_email': controller_email,
            'filter': date_filter
        }), 200

    except Exception as e:
        print(f"❌ Error fetching controller assignments: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@school_assignment_bp.route('/delete-assignment/<assignment_id>', methods=['DELETE'])
def delete_assignment(assignment_id):
    """Delete a school assignment"""
    try:
        result = mongo.db.school_assignments.update_one(
            {'_id': ObjectId(assignment_id)},
            {'$set': {'status': 'deleted'}}
        )

        if result.modified_count == 0:
            return jsonify({'error': 'Assignment not found'}), 404

        return jsonify({'message': 'Assignment deleted successfully'}), 200

    except Exception as e:
        print(f"❌ Error deleting assignment: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@school_assignment_bp.route('/bulk-assign', methods=['POST'])
def bulk_assign_schools():
    """Bulk assign schools to multiple trainers for multiple dates"""
    try:
        data = request.get_json()

        required_fields = ['controller_email', 'assignments']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({'error': f'Missing required field: {field}'}), 400

        controller_email = data['controller_email']
        assignments = data['assignments']  # Array of assignment objects

        created_count = 0
        updated_count = 0
        errors = []

        for assignment in assignments:
            try:
                trainer_email = assignment['trainer_email']
                assignment_date = assignment['assignment_date']
                schools = assignment['schools']

                # Validate each assignment
                if not trainer_email or not assignment_date or not schools:
                    errors.append(f"Invalid assignment data for {trainer_email}")
                    continue

                # Check date validity
                date_obj = datetime.strptime(assignment_date, '%Y-%m-%d')
                today = datetime.now().date()
                if date_obj.date() < today or (date_obj.date() - today).days > 7:
                    errors.append(f"Invalid date {assignment_date} for {trainer_email}")
                    continue

                # Check if assignment exists
                existing = mongo.db.school_assignments.find_one({
                    'trainer_email': trainer_email,
                    'assignment_date': assignment_date
                })

                assignment_record = {
                    'controller_email': controller_email,
                    'trainer_email': trainer_email,
                    'assignment_date': assignment_date,
                    'schools': schools,
                    'created_at': datetime.now(IST_TZ),
                    'status': 'active'
                }

                if existing:
                    mongo.db.school_assignments.update_one(
                        {'_id': existing['_id']},
                        {'$set': assignment_record}
                    )
                    updated_count += 1
                else:
                    mongo.db.school_assignments.insert_one(assignment_record)
                    created_count += 1

            except Exception as e:
                errors.append(f"Error processing assignment for {assignment.get('trainer_email', 'unknown')}: {str(e)}")

        return jsonify({
            'message': 'Bulk assignment completed',
            'created': created_count,
            'updated': updated_count,
            'errors': errors
        }), 200

    except Exception as e:
        print(f"❌ Error in bulk assignment: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

# Add this new endpoint to your school_assignment_bp Blueprint

@school_assignment_bp.route('/update-assignment/<assignment_id>', methods=['PUT'])
def update_assignment(assignment_id):
    """Update an existing school assignment with date restrictions"""
    try:
        data = request.get_json()

        required_fields = ['schools']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400

        schools = data['schools']
        allow_past_edit = data.get('allow_past_edit', False)  # Optional parameter

        # Validate schools data
        for school in schools:
            if not school.get('school_name') or not school.get('city'):
                return jsonify({'error': 'Each school must have school_name and city'}), 400

        # Check if assignment exists
        existing_assignment = mongo.db.school_assignments.find_one({
            '_id': ObjectId(assignment_id),
            'status': 'active'
        })

        if not existing_assignment:
            return jsonify({'error': 'Assignment not found'}), 404

        # Date restriction check
        assignment_date = existing_assignment['assignment_date']
        today = datetime.now().date().strftime('%Y-%m-%d')

        if assignment_date < today and not allow_past_edit:
            return jsonify({
                'error': 'Cannot edit past assignments',
                'assignment_date': assignment_date,
                'is_past': True
            }), 403  # HTTP 403 Forbidden

        # Additional business rules (optional)
        if assignment_date == today:
            # Maybe allow editing today's assignments but with a warning
            pass

        # Update the assignment
        update_data = {
            'schools': schools,
            'updated_at': datetime.now(IST_TZ)
        }

        result = mongo.db.school_assignments.update_one(
            {'_id': ObjectId(assignment_id)},
            {'$set': update_data}
        )

        if result.modified_count == 0:
            return jsonify({'error': 'Failed to update assignment'}), 500

        return jsonify({
            'message': 'Assignment updated successfully',
            'assignment_id': assignment_id,
            'schools_count': len(schools),
            'assignment_date': assignment_date,
            'is_past_assignment': assignment_date < today
        }), 200

    except Exception as e:
        print(f"❌ Error updating assignment: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500