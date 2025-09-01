from flask import Blueprint, request, jsonify
from app import mongo
from datetime import datetime, timedelta
import pytz
from bson.objectid import ObjectId

school_assignment_bp = Blueprint('school_assignment', __name__)
IST_TZ = pytz.timezone('Asia/Kolkata')


def update_school_audit_status_in_assignment(user_email, school_name, city, audit_status, audit_id=None, assignment_date=None):
    """
    Update the audit status for a specific school in the assignment
    """
    try:
        # If assignment_date not provided, use today's date
        if not assignment_date:
            assignment_date = datetime.now().date().strftime('%Y-%m-%d')
        
        # Find the assignment for this trainer, school, and date
        assignment = mongo.db.school_assignments.find_one({
            'trainer_email': user_email,
            'assignment_date': assignment_date,
            'status': 'active'
        })
        
        if not assignment:
            print(f"⚠️ No assignment found for {user_email} on {assignment_date}")
            return False
        
        # Find the school in the assignment
        schools = assignment.get('schools', [])
        school_found = False
        
        for school in schools:
            if (school.get('school_name', '').lower() == school_name.lower() and 
                school.get('city', '').lower() == city.lower()):
                
                # Update the school's audit status
                school['audit_status'] = audit_status
                school['audit_id'] = audit_id
                school['last_updated'] = datetime.now(IST_TZ).isoformat()
                
                if audit_status == 'in_progress':
                    school['audit_started_at'] = datetime.now(IST_TZ).isoformat()
                elif audit_status == 'completed':
                    school['audit_completed_at'] = datetime.now(IST_TZ).isoformat()
                
                school_found = True
                break
        
        if not school_found:
            print(f"⚠️ School {school_name} in {city} not found in assignment")
            return False
        
        # Update the assignment in database
        result = mongo.db.school_assignments.update_one(
            {'_id': assignment['_id']},
            {
                '$set': {
                    'schools': schools,
                    'last_status_update': datetime.now(IST_TZ)
                }
            }
        )
        
        if result.modified_count > 0:
            print(f"✅ Updated audit status for {school_name} to {audit_status}")
            return True
        else:
            print(f"❌ Failed to update audit status for {school_name}")
            return False
            
    except Exception as e:
        print(f"❌ Error updating school audit status: {str(e)}")
        return False


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

        # Validate schools data and initialize audit status
        for school in schools:
            if not school.get('school_name') or not school.get('city'):
                return jsonify({'error': 'Each school must have school_name and city'}), 400
            
            # Initialize audit status fields for new assignments
            school['audit_status'] = 'pending'
            school['audit_id'] = None
            school['last_updated'] = None
            school['audit_started_at'] = None
            school['audit_completed_at'] = None

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
    """Get today's school assignments with accurate audit status for each school"""
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

        # IMPROVED: Query ALL audits for these schools by this trainer
        # Get all audits for this trainer and these specific schools
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

        # Sort by creation time to get the most recent audits first
        audits = list(mongo.db.school_audits.find(audit_query).sort('created_at', -1))

        # IMPROVED: Create more sophisticated audit lookup
        audit_lookup = {}

        for audit in audits:
            identifier = f"{audit['school_name']}|{audit['city']}".lower()

            # If we already have an audit status for this school, check priorities
            if identifier in audit_lookup:
                existing_status = audit_lookup[identifier]['status']
                current_status = audit['status']

                # Priority order: in_progress > completed (today) > completed (other days) > pending
                # If current audit is in_progress, it takes priority
                if current_status == 'in_progress':
                    audit_lookup[identifier] = {
                        'status': 'in_progress',
                        'audit_id': str(audit['_id']),
                        'start_timestamp': audit.get('start_timestamp'),
                        'completion_timestamp': None,
                        'audit_data': audit,
                        'audit_date': audit.get('audit_date')
                    }
                # If current audit is completed and we don't have in_progress, check date
                elif current_status == 'completed' and existing_status != 'in_progress':
                    # For completed audits, prefer today's completion
                    audit_date = audit.get('audit_date')
                    if audit_date == assignment_date:
                        audit_lookup[identifier] = {
                            'status': 'completed',
                            'audit_id': str(audit['_id']),
                            'start_timestamp': audit.get('start_timestamp'),
                            'completion_timestamp': audit.get('completion_timestamp'),
                            'audit_data': audit,
                            'audit_date': audit_date
                        }
                    elif existing_status != 'completed':
                        # Only set if we don't already have a completed status
                        audit_lookup[identifier] = {
                            'status': 'completed',
                            'audit_id': str(audit['_id']),
                            'start_timestamp': audit.get('start_timestamp'),
                            'completion_timestamp': audit.get('completion_timestamp'),
                            'audit_data': audit,
                            'audit_date': audit_date
                        }
            else:
                # First audit for this school
                if audit['status'] == 'in_progress':
                    audit_lookup[identifier] = {
                        'status': 'in_progress',
                        'audit_id': str(audit['_id']),
                        'start_timestamp': audit.get('start_timestamp'),
                        'completion_timestamp': None,
                        'audit_data': audit,
                        'audit_date': audit.get('audit_date')
                    }
                elif audit['status'] == 'completed':
                    audit_lookup[identifier] = {
                        'status': 'completed',
                        'audit_id': str(audit['_id']),
                        'start_timestamp': audit.get('start_timestamp'),
                        'completion_timestamp': audit.get('completion_timestamp'),
                        'audit_data': audit,
                        'audit_date': audit.get('audit_date')
                    }

        # IMPROVED: Enhanced school status assignment
        enhanced_schools = []
        summary_counts = {'completed': 0, 'in_progress': 0, 'pending': 0}

        for school in unique_schools:
            identifier = f"{school['school_name']}|{school['city']}".lower()

            if identifier in audit_lookup:
                audit_info = audit_lookup[identifier]
                audit_status = audit_info['status']

                # Additional validation for incomplete audits
                if audit_status == 'in_progress':
                    # Check if the in-progress audit has any substantial data
                    audit_data = audit_info.get('audit_data', {})
                    if not audit_data.get('sections') and not audit_data.get('responses'):
                        # If no sections or responses, treat as pending
                        audit_status = 'pending'
                        audit_info = None

                summary_counts[audit_status] += 1

                if audit_info:
                    enhanced_school = {
                        **school,
                        'audit_status': audit_status,
                        'audit_id': audit_info['audit_id'],
                        'start_timestamp': audit_info['start_timestamp'],
                        'completion_timestamp': audit_info['completion_timestamp'],
                        'audit_date': audit_info.get('audit_date')
                    }

                    # Include audit data for in-progress audits
                    if audit_status == 'in_progress':
                        enhanced_school['current_audit_data'] = audit_info['audit_data']
                else:
                    # Treated as pending due to incomplete data
                    enhanced_school = {
                        **school,
                        'audit_status': 'pending',
                        'audit_id': None,
                        'start_timestamp': None,
                        'completion_timestamp': None
                    }
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
            'duplicates_removed': len(all_schools) - len(unique_schools),
            'debug_info': {
                'total_audits_found': len(audits),
                'unique_school_identifiers': len(audit_lookup)
            }
        }), 200

    except Exception as e:
        print(f"❌ Error fetching today's assignments: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@school_assignment_bp.route('/today-assignments-with-audit-status/<trainer_email>', methods=['GET'])
def get_today_assignments_with_audit_status(trainer_email):
    """Get today's assignments with real-time audit status from assignment collection"""
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

        schools = assignment.get('schools', [])
        
        # Initialize audit status for schools that don't have it
        summary_counts = {'completed': 0, 'in_progress': 0, 'pending': 0}
        
        for school in schools:
            # Set default status if not present
            if 'audit_status' not in school:
                school['audit_status'] = 'pending'
                school['audit_id'] = None
                school['last_updated'] = None
            
            status = school.get('audit_status', 'pending')
            summary_counts[status] = summary_counts.get(status, 0) + 1

        # Convert ObjectId to string
        assignment['_id'] = str(assignment['_id'])
        if 'created_at' in assignment:
            assignment['created_at'] = assignment['created_at'].isoformat()

        return jsonify({
            'assignment': assignment,
            'schools': schools,
            'assignment_date': assignment_date,
            'summary': {
                'total_assigned': len(schools),
                'completed': summary_counts.get('completed', 0),
                'in_progress': summary_counts.get('in_progress', 0),
                'pending': summary_counts.get('pending', 0)
            }
        }), 200

    except Exception as e:
        print(f"❌ Error fetching assignments with audit status: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


# ADDITIONAL HELPER ENDPOINT: Get detailed audit status for debugging
@school_assignment_bp.route('/debug-school-status/<trainer_email>/<school_name>/<city>', methods=['GET'])
def debug_school_audit_status(trainer_email, school_name, city):
    """Debug endpoint to check all audits for a specific school"""
    try:
        audits = list(mongo.db.school_audits.find({
            'auditor_email': trainer_email,
            'school_name': school_name,
            'city': city
        }).sort('created_at', -1))

        formatted_audits = []
        for audit in audits:
            audit['_id'] = str(audit['_id'])
            if 'created_at' in audit:
                audit['created_at'] = audit['created_at'].isoformat()
            formatted_audits.append(audit)

        return jsonify({
            'school_name': school_name,
            'city': city,
            'trainer_email': trainer_email,
            'total_audits': len(formatted_audits),
            'audits': formatted_audits
        }), 200

    except Exception as e:
        print(f"❌ Error in debug endpoint: {str(e)}")
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

                # Initialize audit status for each school
                for school in schools:
                    school['audit_status'] = 'pending'
                    school['audit_id'] = None
                    school['last_updated'] = None
                    school['audit_started_at'] = None
                    school['audit_completed_at'] = None

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

        # Validate schools data and initialize audit status for new schools
        for school in schools:
            if not school.get('school_name') or not school.get('city'):
                return jsonify({'error': 'Each school must have school_name and city'}), 400
            
            # Initialize audit status if not present
            if 'audit_status' not in school:
                school['audit_status'] = 'pending'
                school['audit_id'] = None
                school['last_updated'] = None
                school['audit_started_at'] = None
                school['audit_completed_at'] = None

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


@school_assignment_bp.route('/sync-audit-status', methods=['POST'])
def sync_audit_status_from_audits():
    """Sync audit status from school_audits to school_assignments (maintenance endpoint)"""
    try:
        data = request.get_json() or {}
        date_range = data.get('date_range', 7)  # Default to last 7 days
        
        # Get recent assignments
        start_date = (datetime.now() - timedelta(days=date_range)).strftime('%Y-%m-%d')
        assignments = mongo.db.school_assignments.find({
            'assignment_date': {'$gte': start_date},
            'status': 'active'
        })
        
        synced_count = 0
        
        for assignment in assignments:
            schools = assignment.get('schools', [])
            assignment_updated = False
            
            for school in schools:
                school_name = school.get('school_name')
                city = school.get('city')
                trainer_email = assignment.get('trainer_email')
                assignment_date = assignment.get('assignment_date')
                
                if not school_name or not city:
                    continue
                
                # Find the most recent audit for this school
                audit = mongo.db.school_audits.find_one({
                    'user_email': trainer_email,
                    'school_name': school_name,
                    'city': city,
                    'assignment_date': assignment_date
                }, sort=[('created_at', -1)])
                
                if audit:
                    # Update school with audit status
                    old_status = school.get('audit_status', 'pending')
                    new_status = audit['status']
                    
                    if old_status != new_status:
                        school['audit_status'] = new_status
                        school['audit_id'] = str(audit['_id'])
                        school['last_updated'] = datetime.now(IST_TZ).isoformat()
                        
                        if new_status == 'in_progress':
                            school['audit_started_at'] = audit['created_at'].isoformat()
                        elif new_status == 'completed':
                            school['audit_completed_at'] = audit.get('completed_at', audit['created_at']).isoformat()
                        
                        assignment_updated = True
                else:
                    # No audit found - ensure status is pending
                    if school.get('audit_status') != 'pending':
                        school['audit_status'] = 'pending'
                        school['audit_id'] = None
                        school['last_updated'] = datetime.now(IST_TZ).isoformat()
                        assignment_updated = True
            
            # Update assignment if any schools were modified
            if assignment_updated:
                mongo.db.school_assignments.update_one(
                    {'_id': assignment['_id']},
                    {
                        '$set': {
                            'schools': schools,
                            'last_sync_update': datetime.now(IST_TZ)
                        }
                    }
                )
                synced_count += 1
        
        return jsonify({
            'message': f'Audit status sync completed. Updated {synced_count} assignments.',
            'synced_assignments': synced_count,
            'date_range_days': date_range
        }), 200
        
    except Exception as e:
        print(f"❌ Error syncing audit status: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500
