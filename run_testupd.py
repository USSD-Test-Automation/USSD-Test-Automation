# --- Existing Imports ---
from collections import defaultdict
from flask import (Flask, render_template, request, jsonify, Response,
                   redirect, url_for, flash, session) # Added redirect, url_for, flash
import subprocess
import os
import datetime
import threading
import time
import json
import sys
import io
from flask_cors import CORS
import mysql.connector
import logging # For better logging

# --- New Imports ---
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
# from werkzeug.security import generate_password_hash, check_password_hash # Handled in models.py
from android_helper import get_android_version, get_connected_device
from models import User, TestExecution, TestAssignment, get_db_connection as get_db_conn_from_models # Use this for new DB interactions
from forms import LoginForm, CreateUserForm, AssignTestCaseForm, EditUserForm
from decorators import admin_required, manager_required, tester_required, manager_or_admin_required
from wtforms import StringField, PasswordField, SubmitField, SelectField
from wtforms.validators import DataRequired, EqualTo, ValidationError, Length, Regexp

# --- App Setup ---
app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'your_very_strong_unguessable_secret_key_here_39#@!') # IMPORTANT!
CORS(app, resources={r"/api/*": {"origins": "http://localhost:3000"}})

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app.logger.setLevel(logging.INFO)


# --- Flask-Login Setup ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # Name of the login route
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    return User.get(int(user_id))

# --- Global State for Test Run (from your existing code) ---
test_status = {
    'running': False,
    'output_file': None,
    'final_output': '',
    'report_path': None,
    'process': None,
    'thread': None,
    'current_assignment_id': None,
    'current_batch_assignment_id': None
}
state_lock = threading.Lock()

# --- Database Configuration (from your existing code or models.py) ---
# Using your existing DB_CONFIG for functions you already have.
# New functions related to user management will use get_db_conn_from_models()
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'actual_db'
}

def get_db_connection(): # Your existing function
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as err:
        app.logger.error(f"Original get_db_connection error: {err}")
        raise # Or handle as per your app's needs

@app.route('/admin/users/deactivate/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def deactivate_user(user_id):
    if int(current_user.id) == int(user_id):
        # Check if trying to deactivate self AND is the last active admin
        user_to_deactivate = User.get(user_id) # Get user details
        if user_to_deactivate and user_to_deactivate.role == 'admin':
            conn_check = get_db_conn_from_models()
            cursor_check = conn_check.cursor(dictionary=True)
            cursor_check.execute("SELECT COUNT(*) AS admin_count FROM users WHERE Role = 'admin' AND IsActive = TRUE")
            admin_count = cursor_check.fetchone()['admin_count']
            cursor_check.close()
            conn_check.close()
            if admin_count <= 1:
                flash('You cannot deactivate your own account as the last active admin.', 'danger')
                app.logger.warning(f"Admin {current_user.username} attempted to self-deactivate as last admin.")
                return redirect(url_for('list_users'))
        # else:
            # flash('You cannot deactivate your own account through this button for safety.', 'warning')
            # app.logger.warning(f"Admin {current_user.username} attempted to self-deactivate (ID: {user_id}).")
            # return redirect(url_for('list_users'))

    success, message = User.deactivate(user_id_to_deactivate=user_id, current_admin_id=current_user.id)
    if success:
        flash(message, 'success')
        app.logger.info(f"Admin {current_user.username} deactivated user ID {user_id}. Message: {message}")
    else:
        flash(message, 'danger')
        app.logger.error(f"Admin {current_user.username} failed to deactivate user ID {user_id}. Error: {message}")
    return redirect(url_for('list_users'))

@app.route('/admin/users/activate/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def activate_user(user_id):
    success, message = User.activate(user_id_to_activate=user_id, current_admin_id=current_user.id)
    if success:
        flash(message, 'success')
        app.logger.info(f"Admin {current_user.username} activated user ID {user_id}. Message: {message}")
    else:
        flash(message, 'danger')
        app.logger.error(f"Admin {current_user.username} failed to activate user ID {user_id}. Error: {message}")
    return redirect(url_for('list_users'))

# ******************************************************************************
# * AUTHENTICATION ROUTES (NEW)                                                *
# ******************************************************************************
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.find_by_username(form.username.data)
        # Check if user exists, password is correct, AND user is active
        if user and user.check_password(form.password.data):
            if not user.is_active: # Check if user is active
                flash('Your account is deactivated. Please contact an administrator.', 'warning')
                app.logger.warning(f"Deactivated user login attempt: {form.username.data}")
                return render_template('auth/login.html', title='Login', form=form)
            
            login_user(user, remember=form.remember_me.data)
            # flash('Logged in successfully!', 'success')
            app.logger.info(f"User {user.username} logged in successfully.")
            next_page = request.args.get('next')
            if user.role == 'admin':
                return redirect(next_page or url_for('admin_dashboard', user=user))
            elif user.role == 'manager':
                return redirect(next_page or url_for('manager_dashboard'))
            elif user.role == 'tester':
                return redirect(next_page or url_for('tester_dashboard'))
            return redirect(next_page or url_for('index'))
        else:
            flash('Login Unsuccessful. Please check username and password or account status.', 'danger')
            app.logger.warning(f"Failed login attempt for username: {form.username.data}")
    return render_template('auth/login.html', title='Login', form=form)

@app.route('/logout')
@login_required
def logout():
    app.logger.info(f"User {current_user.username} logging out.")
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# ******************************************************************************
# * DASHBOARD ROUTES (NEW)                                                     *
# ******************************************************************************
@app.route('/admin/dashboardold')
@login_required
@admin_required
def admin_dashboardold():
    return render_template('admin/dashboard.html', title='Admin Dashboard')

@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():

    user_name = current_user.username
    total_users = User.count_all()  # Count all users in the table
    total_tests = TestAssignment.count_all()
    completed_tests = TestAssignment.count_completed()
    pending_tests = TestAssignment.count_pending()

    completed_percent = 0
    if total_tests > 0:
        completed_percent = round((completed_tests / total_tests) * 100)


    return render_template(
        'admin/index.html', 
        total_users=total_users, 
        total_tests=total_tests, 
        completed_percent=completed_percent,
        pending_tests=pending_tests,
        user_name=user_name, 
        title='Admin Dashboard'
        )
    # return render_template('admin/index.html', title='Admin Dashboard')

@app.route('/manager/dashboard')
@login_required
@manager_required # Or @manager_or_admin_required if admins also use this view
def manager_dashboard():
    selected_app_id_str = request.args.get('app_id')
    selected_suite_id_str = request.args.get('suite_id')

    selected_app_id = None
    if selected_app_id_str and selected_app_id_str.isdigit():
        selected_app_id = int(selected_app_id_str)

    selected_suite_id = None
    if selected_suite_id_str and selected_suite_id_str.isdigit():
        selected_suite_id = int(selected_suite_id_str)
    
    app.logger.info(f"Manager dashboard access: app_id={selected_app_id}, suite_id={selected_suite_id}")

    # Data to pass to the template
    applications_list = []
    selected_app_details = None
    test_suites_list = []
    selected_suite_details = None
    test_cases_list = [] # Renamed from 'test_cases' in your original to avoid conflict

    # Level 1: Always fetch applications for the first level or breadcrumbs
    applications_list = get_all_applications_for_dashboard()

    if selected_app_id:
        selected_app_details = get_application_by_id_for_dashboard(selected_app_id)
        if not selected_app_details:
            flash(f"Application with ID {selected_app_id} not found.", "warning")
            app.logger.warning(f"Manager dashboard: Invalid app_id {selected_app_id} requested.")
            return redirect(url_for('manager_dashboard')) # Redirect to base manager dashboard
        
        # Level 2: If app is selected, fetch its suites
        test_suites_list = get_suites_for_application_for_dashboard(selected_app_id)

        if selected_suite_id:
            selected_suite_details = get_suite_by_id_for_dashboard(selected_suite_id)
            if not selected_suite_details:
                flash(f"Test Suite with ID {selected_suite_id} not found.", "warning")
                app.logger.warning(f"Manager dashboard: Invalid suite_id {selected_suite_id} for app_id {selected_app_id} requested.")
                # Redirect to the app's suite selection level
                return redirect(url_for('manager_dashboard', app_id=selected_app_id)) 

            # Security/Sanity Check: Ensure the suite belongs to the selected application
            if selected_suite_details['AppType'] != selected_app_id:
                flash("Invalid suite selection for the chosen application.", "danger")
                app.logger.error(f"Manager dashboard: Mismatch! Suite {selected_suite_id} (AppType {selected_suite_details['AppType']}) requested under App {selected_app_id}.")
                return redirect(url_for('manager_dashboard', app_id=selected_app_id))

            # Level 3: If app and suite are selected, fetch test cases
            test_cases_list = get_test_cases_for_suite_for_dashboard(selected_suite_id)

    return render_template('manager/dashboard.html', 
                           title='Manager Dashboard',
                           applications=applications_list, # For Level 1 display
                           selected_app_id=selected_app_id,
                           selected_app=selected_app_details, # For breadcrumbs and Level 2 title
                           test_suites=test_suites_list,    # For Level 2 display
                           selected_suite_id=selected_suite_id,
                           selected_suite=selected_suite_details, # For breadcrumbs and Level 3 title
                           test_cases=test_cases_list) # For Level 3 display (this matches your new template)

@app.route('/tester/dashboard')
@login_required
@tester_required
def tester_dashboard():
    assigned_tests = []
    conn = None
    try:
        conn = get_db_conn_from_models()
        cursor = conn.cursor(dictionary=True)

        # Original query (example, yours might be different)
        # query = """
        #     SELECT ta.AssignmentID, tc.Code, tc.Name, tc.Module, u_assigner.Username AS AssignedBy,
        #            ta.AssignmentDate, ta.Status, ta.Notes
        #     FROM test_assignments ta
        #     JOIN testcases tc ON ta.TestCaseID = tc.TestCaseID
        #     JOIN users u_assigner ON ta.AssignedByUserID = u_assigner.UserID
        #     WHERE ta.AssignedToUserID = %s AND ta.Status IN ('PENDING', 'IN_PROGRESS')
        #     ORDER BY ta.AssignmentDate DESC
        # """

        # --- MODIFIED QUERY ---

        if current_user.role.lower() != 'tester':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

        query = """
            SELECT 
                ta.AssignmentID, 
                tc.Code, 
                tc.Name, 
                tc.Module, 
                u_assigner.Username AS AssignedBy,
                ta.AssignmentDate, 
                ta.Status, 
                ta.Notes,
                ta.Priority  -- <<<< ADDED Priority column
            FROM test_assignments ta
            JOIN testcases tc ON ta.TestCaseID = tc.TestCaseID
            JOIN users u_assigner ON ta.AssignedByUserID = u_assigner.UserID  -- Assuming 'users' table for assigner
            WHERE ta.AssignedToUserID = %s AND ta.Status IN ('PENDING', 'IN_PROGRESS')
            ORDER BY 
                FIELD(ta.Priority, 'HIGH', 'MEDIUM', 'LOW'), -- <<<< ADDED Custom Priority Sort
                ta.AssignmentDate ASC  -- Secondary sort: older assignments first within same priority
        """
        # --- END OF MODIFIED QUERY ---

        cursor.execute(query, (current_user.id,)) # Assuming current_user.id is the UserID
        assigned_tests = cursor.fetchall()

    except mysql.connector.Error as err:
        app.logger.error(f"DB error fetching assigned tests for tester {current_user.id}: {err}", exc_info=True)
        flash('Error fetching your assigned tests.', 'danger')
    except Exception as e:
        app.logger.error(f"Unexpected error fetching assigned tests for tester {current_user.id}: {e}", exc_info=True)
        flash('An unexpected error occurred while fetching your tests.', 'danger')
    finally:
        if conn and conn.is_connected():
            if 'cursor' in locals() and cursor:
                cursor.close()
            conn.close()
            
    return render_template('tester/dashboard.html', assigned_tests=assigned_tests)

# ******************************************************************************
# * USER MANAGEMENT (ADMIN - NEW ROUTES)                                       *
# ******************************************************************************
@app.route('/admin/users')
@login_required
@admin_required
def list_users():
    users = User.get_all_users()
    user_name = current_user.username
    return render_template('admin/list_users.html', title='Manage Users', users=users, user_name=user_name)

@app.route('/admin/users/create', methods=['GET', 'POST'])
@login_required
@admin_required
def create_user():
    form = CreateUserForm()
    if form.validate_on_submit():
        success, message_or_id = User.create(
            username=form.username.data,
            password=form.password.data,
            role=form.role.data
        )
        if success:
            flash(f'User "{form.username.data}" created successfully (ID: {message_or_id}).', 'success')
            app.logger.info(f"Admin {current_user.username} created user {form.username.data} with role {form.role.data}.")
            return redirect(url_for('list_users'))
        else:
            flash(f'Error creating user: {message_or_id}', 'danger')
            app.logger.error(f"Admin {current_user.username} failed to create user {form.username.data}: {message_or_id}")
    return render_template('admin/create_user.html', title='Create New User', form=form)

# In app.py
# ... (other imports)
from forms import LoginForm, CreateUserForm, EditUserForm, AssignTestCaseForm # Make sure EditUserForm is imported

# ... (your existing app setup and routes) ...

# USER MANAGEMENT (ADMIN - NEW ROUTES)
# ... (your existing list_users and create_user routes) ...

@app.route('/admin/users/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    user_to_edit = User.get(user_id) # Assumes User.get() returns a User object or a dict-like object
    if not user_to_edit:
        flash('User not found.', 'danger')
        return redirect(url_for('list_users'))

    # If User.get returns a dict: user_to_edit['Username'], user_to_edit['Role']
    # If User.get returns an object: user_to_edit.username, user_to_edit.role
    # Adjust access accordingly. I'll assume object access for consistency with current_user.
    form = EditUserForm(original_username=user_to_edit.username) 

    if form.validate_on_submit():
        new_password = form.password.data if form.password.data.strip() else None # Ensure empty strings are treated as no change

        # Prevent demoting the last admin or changing role of self if last admin
        is_last_admin = False
        if user_to_edit.role == 'admin':
            conn_check = get_db_conn_from_models() # Use the specified DB connection getter
            cursor_check = conn_check.cursor(dictionary=True)
            cursor_check.execute("SELECT COUNT(*) AS admin_count FROM users WHERE Role = 'admin'")
            admin_count = cursor_check.fetchone()['admin_count']
            cursor_check.close()
            conn_check.close()
            if admin_count <= 1:
                is_last_admin = True
        
        if is_last_admin and form.role.data != 'admin':
            flash('Cannot change the role of the last admin.', 'danger')
            return render_template('admin/edit_user.html', title='Edit User', form=form, user=user_to_edit)
        
        if int(current_user.id) == int(user_id) and is_last_admin and form.role.data != 'admin':
             flash('As the last admin, you cannot change your own role from Admin.', 'danger')
             return render_template('admin/edit_user.html', title='Edit User', form=form, user=user_to_edit)

        success, message = User.update(
            user_id=user_id,
            username=form.username.data,
            role=form.role.data,
            new_password=new_password
        )
        if success:
            flash(f'User "{form.username.data}" updated successfully.', 'success')
            app.logger.info(f"Admin {current_user.username} updated user ID {user_id} (username: {form.username.data}, role: {form.role.data}).")
            return redirect(url_for('list_users'))
        else:
            flash(f'Error updating user: {message}', 'danger')
            app.logger.error(f"Admin {current_user.username} failed to update user ID {user_id}: {message}")
    
    elif request.method == 'GET':
        form.username.data = user_to_edit.username
        form.role.data = user_to_edit.role
        # Password fields are intentionally left blank on GET

    return render_template('admin/edit_user.html', title='Edit User', form=form, user=user_to_edit)


@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user_to_delete = User.get(user_id)
    if not user_to_delete:
        flash("User not found.", "danger")
        return redirect(url_for('list_users'))

    if int(current_user.id) == int(user_id):
        flash("You cannot delete your own account.", "warning")
        app.logger.warning(f"Admin {current_user.username} attempted to self-delete (ID: {user_id}).")
        return redirect(url_for('list_users'))
            
    success, message = User.delete(user_id_to_delete=user_id, current_admin_id=current_user.id) # Pass current_admin_id for checks in model
    
    if success:
        flash(f'User (ID: {user_id}, Username: {user_to_delete.username}) deleted successfully.', 'success')
        app.logger.info(f"Admin {current_user.username} deleted user ID {user_id} (username: {user_to_delete.username}).")
    else:
        flash(f'Error deleting user: {message}', 'danger')
        app.logger.error(f"Admin {current_user.username} failed to delete user ID {user_id}: {message}")
    return redirect(url_for('list_users'))

# Placeholder for edit/delete user if you want to add later
# @app.route('/admin/users/edit/<int:user_id>', methods=['GET', 'POST'])
# @app.route('/admin/users/delete/<int:user_id>', methods=['POST'])

# ******************************************************************************
# * TEST CASE CREATION (MODIFIED FOR ADMIN ROLE AND LOGGING CreatedBy)         *
# ******************************************************************************
# app.py

# ... (other imports and Flask app setup) ...
# Make sure datetime is imported if not already:
# import datetime
# from flask import current_user, jsonify, request, render_template etc.

@app.route('/admindashboard', methods=['GET'])
def admindashboard():
    return render_template('admin/index.html')

@app.route('/forgot_password', methods=['GET'])
def forgot_password():
    return render_template('auth/forgot-password.html')

@app.route('/create-testcase', methods=['GET'])
@login_required
@admin_required
def create_testcase_form():
    return render_template('admin/create_testcase.html') # Your template

@app.route('/create-testcase', methods=['POST'])
@login_required
@admin_required
def create_testcase():
    payload = request.get_json()
    try:
        payload['created_by_user_id'] = current_user.id
        
        # --- Crucial Check ---
        selected_suite_id = payload.get('selected_suite_id')
        if not selected_suite_id:
            app.logger.error(f"Error in create_testcase by {current_user.username}: selected_suite_id is missing from payload.")
            return jsonify({'success': False, 'error': 'Module (Test Suite) ID is required.'}), 400

        new_id = create_testcase_in_db_with_user(payload) # Pass the whole payload
        app.logger.info(f"Admin {current_user.username} created test case '{payload.get('name')}' with ID {new_id} and linked to Suite ID {selected_suite_id}.")
        return jsonify({'success':True, 'testcase_id': new_id})
    except Exception as e:
        app.logger.error(f"Error in create_testcase by {current_user.username}: {e}", exc_info=True)
        return jsonify({'success':False, 'error': str(e)}), 500

def create_testcase_in_db_with_user(payload):
    """
    payload = {
      'code': str,
      'name': str,
      'module': str or None, # Textual name of the module/suite
      'description': str or None,
      'created_by_user_id': int,
      'selected_suite_id': int, # <<< The SuiteID to link in suitetestcases
      'steps': [ ... ]
    }
    Returns new TestCaseID.
    """
    conn = None
    try:
        conn = get_db_connection() # Use your existing connection getter
        cursor = conn.cursor()
        conn.autocommit = False # Manage transaction explicitly

        now = datetime.datetime.now()

        # 1. Insert into testcases table
        # If you also want to store selected_suite_id in testcases.Module_id:
        # You'd add Module_id = %s and pass payload.get('selected_suite_id')
        # For now, I'm assuming testcases.Module stores the text name, and Module_id might be separate or unused here.
        cursor.execute("""
            INSERT INTO testcases
              (Code, Name, Module, Module_id, Description, CreatedBy, CreatedAt, ModifiedBy, ModifiedAt)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            payload['code'],
            payload['name'],
            payload.get('module'), # This is the textual module name from the dropdown
            payload.get('selected_suite_id'),
            payload.get('description'),
            payload['created_by_user_id'],
            now,
            payload['created_by_user_id'],
            now
        ))
        testcase_id = cursor.lastrowid

        # 2. Insert steps (your existing logic)
        step_sql = """
            INSERT INTO steps
              (TestCaseID, StepOrder, Input, ExpectedResponse, InputType, ParamName, InpType)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        for step in payload['steps']:
            expected_str = ','.join(step['expected_keywords'])
            cursor.execute(step_sql, (
                testcase_id, step['step_order'], step['input'],
                expected_str, step['input_type'], step.get('param_name'), step.get('param_type')
            ))
        
        # 3. <<< NEW: Link test case to the selected suite in suitetestcases >>>
        selected_suite_id = payload.get('selected_suite_id')
        if testcase_id and selected_suite_id:
            # Determine CaseOrder. For simplicity, let's find the max order for that suite and add 1.
            # Or you can set a default like 0 or 999, or not have a strict order here if not needed.
            cursor.execute("""
                SELECT COALESCE(MAX(CaseOrder), 0) + 1 FROM suitetestcases WHERE SuiteID = %s
            """, (selected_suite_id,))
            case_order_result = cursor.fetchone()
            case_order = case_order_result[0] if case_order_result else 1

            cursor.execute("""
                INSERT INTO suitetestcases (SuiteID, TestCaseID, CaseOrder)
                VALUES (%s, %s, %s)
            """, (selected_suite_id, testcase_id, case_order))
            app.logger.info(f"Linked TestCaseID {testcase_id} to SuiteID {selected_suite_id} in suitetestcases.")
        else:
            # This case should ideally be caught by the check in the route.
            # If it reaches here, it's an issue.
            app.logger.warning(f"Could not link test case to suite: TestCaseID={testcase_id}, SelectedSuiteID={selected_suite_id}")


        conn.commit()
        return testcase_id
    except mysql.connector.Error as db_err:
        if conn: conn.rollback()
        app.logger.error(f"Database error in create_testcase_in_db_with_user: {db_err}", exc_info=True)
        # Raise a more specific error or re-raise
        raise ValueError(f"Database operation failed: {db_err.msg}") from db_err
    except Exception as e:
        if conn: conn.rollback()
        app.logger.error(f"General error in create_testcase_in_db_with_user: {e}", exc_info=True)
        raise # Re-raise
    finally:
        if conn and conn.is_connected():
            if 'cursor' in locals() and cursor:
                 cursor.close()
            conn.close()

# ... (rest of your Flask app code) ...

# ******************************************************************************
# * TEST ASSIGNMENT (MANAGER - NEW ROUTES AND HELPERS)                         *
# ******************************************************************************
def get_all_test_cases_for_assignment(): # Helper function
    conn = None
    try:
        conn = get_db_conn_from_models() # Use model's connection getter
        cursor = conn.cursor(dictionary=True)
        # Shows test cases and who they are currently assigned to if status is PENDING
        query = """
            SELECT
                tc.TestCaseID, tc.Code, tc.Name, tc.Module,
                GROUP_CONCAT(DISTINCT IF(ta.Status = 'PENDING', u_assigned.Username, NULL) SEPARATOR ', ') AS pending_assigned_to_usernames,
                (SELECT COUNT(*) FROM test_assignments ta_count
                 WHERE ta_count.TestCaseID = tc.TestCaseID AND ta_count.Status = 'PENDING') AS pending_assignments_count
            FROM testcases tc
            LEFT JOIN test_assignments ta ON tc.TestCaseID = ta.TestCaseID
            LEFT JOIN users u_assigned ON ta.AssignedToUserID = u_assigned.UserID AND ta.Status = 'PENDING'
            GROUP BY tc.TestCaseID, tc.Code, tc.Name, tc.Module
            ORDER BY tc.Module, tc.Name;
        """
        cursor.execute(query)
        test_cases = cursor.fetchall()
        return test_cases
    except mysql.connector.Error as err:
        app.logger.error(f"DB error in get_all_test_cases_for_assignment: {err}", exc_info=True)
        return []
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/manager/assign/<int:testcase_id>', methods=['GET', 'POST'])
@login_required
@manager_or_admin_required # Managers or Admins can assign
def assign_test_case(testcase_id):
    conn_tc = None
    try:
        conn_tc = get_db_conn_from_models()
        cursor_tc = conn_tc.cursor(dictionary=True)
        cursor_tc.execute("SELECT TestCaseID, Code, Name FROM testcases WHERE TestCaseID = %s", (testcase_id,))
        test_case = cursor_tc.fetchone()
    except mysql.connector.Error as err:
        app.logger.error(f"DB error fetching test case {testcase_id} for assignment: {err}", exc_info=True)
        flash('Error fetching test case details.', 'danger')
        return redirect(url_for('manager_dashboard'))
    finally:
        if conn_tc and conn_tc.is_connected():
            cursor_tc.close()
            conn_tc.close()

    if not test_case:
        flash('Test Case not found.', 'danger')
        return redirect(url_for('manager_dashboard'))

    form = AssignTestCaseForm()
    # Populate tester choices dynamically
    testers = User.get_testers() # From models.py
    form.tester_id.choices = [(t['UserID'], t['Username']) for t in testers]
    if not testers:
         flash('No testers available to assign to.', 'warning')
         # Potentially disable form or handle differently

    if form.validate_on_submit():
        assigned_to_user_id = form.tester_id.data
        assigned_by_user_id = current_user.id
        notes = form.notes.data if hasattr(form, 'notes') else None # If you add notes to form

        conn_assign = None
        try:
            conn_assign = get_db_conn_from_models()
            cursor_assign = conn_assign.cursor()
            # Check if already assigned and PENDING for this specific tester to avoid duplicates
            cursor_assign.execute("""
                SELECT AssignmentID FROM test_assignments
                WHERE TestCaseID = %s AND AssignedToUserID = %s AND Status = 'PENDING'
            """, (testcase_id, assigned_to_user_id))
            existing_assignment = cursor_assign.fetchone()

            if existing_assignment:
                flash(f'Test Case "{test_case["Code"]}" is already PENDING assignment for the selected tester.', 'warning')
            else:
                cursor_assign.execute("""
                    INSERT INTO test_assignments (TestCaseID, AssignedToUserID, AssignedByUserID, Status, Notes)
                    VALUES (%s, %s, %s, %s, %s)
                """, (testcase_id, assigned_to_user_id, assigned_by_user_id, 'PENDING', notes))
                conn_assign.commit() # Assuming autocommit is False for get_db_conn_from_models if not set in DB_CONFIG
                flash(f'Test Case "{test_case["Code"]}" assigned successfully!', 'success')
                app.logger.info(f"Manager/Admin {current_user.username} assigned TC {testcase_id} to user {assigned_to_user_id}.")
            return redirect(url_for('manager_dashboard'))
        except mysql.connector.Error as err:
            if conn_assign: conn_assign.rollback()
            flash(f'Error assigning test case: {err}', 'danger')
            app.logger.error(f"DB error during assignment of TC {testcase_id} by {current_user.username}: {err}", exc_info=True)
        finally:
            if conn_assign and conn_assign.is_connected():
                cursor_assign.close()
                conn_assign.close()
                
    return render_template('manager/assign_test_case.html', title=f'Assign Test Case {test_case["Code"]}', form=form, test_case=test_case)

# ******************************************************************************
# * TESTER ROUTES (NEW ROUTES AND HELPERS)                                     *
# ******************************************************************************
def get_assigned_tests_for_tester(tester_user_id): # Helper
    conn = None
    try:
        conn = get_db_conn_from_models()
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT
                ta.AssignmentID, tc.TestCaseID, tc.Code, tc.Name, tc.Module, ta.Status,
                u_assigner.Username AS AssignedBy, ta.AssignmentDate, ta.Notes
            FROM test_assignments ta
            JOIN testcases tc ON ta.TestCaseID = tc.TestCaseID
            JOIN users u_assigner ON ta.AssignedByUserID = u_assigner.UserID
            WHERE ta.AssignedToUserID = %s AND ta.Status IN ('PENDING', 'IN_PROGRESS')
            ORDER BY ta.AssignmentDate DESC;
        """
        cursor.execute(query, (tester_user_id,))
        assigned_tests = cursor.fetchall()
        return assigned_tests
    except mysql.connector.Error as err:
        app.logger.error(f"DB error in get_assigned_tests_for_tester for user {tester_user_id}: {err}", exc_info=True)
        return []
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/tester/run_assigned/<int:assignment_id>')
@login_required
@tester_required
def run_assigned_test_page(assignment_id):
    conn = None
    try:
        conn = get_db_conn_from_models()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT ta.AssignmentID, ta.TestCaseID, tc.Code, tc.Name, tc.Module, ta.Status, tc.Description
            FROM test_assignments ta
            JOIN testcases tc ON ta.TestCaseID = tc.TestCaseID
            WHERE ta.AssignmentID = %s AND ta.AssignedToUserID = %s
        """, (assignment_id, current_user.id))
        assignment = cursor.fetchone()
    except mysql.connector.Error as err:
        app.logger.error(f"DB error fetching assignment {assignment_id} for user {current_user.id}: {err}", exc_info=True)
        flash('Error fetching assignment details.', 'danger')
        return redirect(url_for('tester_dashboard'))
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

    if not assignment:
        flash('Assignment not found or you are not authorized for it.', 'danger')
        return redirect(url_for('tester_dashboard'))

    if assignment['Status'] not in ['PENDING', 'IN_PROGRESS']:
        flash(f'This test assignment (Status: {assignment["Status"]}) cannot be run at this time.', 'warning')
        return redirect(url_for('tester_dashboard'))

    # Fetch device info (your existing android_helper logic)
    try:
        device_id = get_connected_device()
        android_ver = get_android_version(device_id)
    except NameError: # If android_helper functions are not defined due to import error
        app.logger.warning("android_helper not available, device auto-detection disabled for run_assigned_test_page.")
        device_id = ''
        android_ver = ''
    except Exception as e:
        app.logger.error(f"Error getting device info for run_assigned_test_page: {e}")
        device_id = ''
        android_ver = ''

    # Fetch dynamic params for this specific test case (your existing /test-case/<tcid>/params logic)
    params_for_tc = get_testcase_dynamic_params_from_db(assignment['TestCaseID']) # Helper defined below

    return render_template('tester/run_assigned_test.html',
                           assignment=assignment,
                           device_id=device_id,
                           android_version=android_ver,
                           test_case_id=assignment['TestCaseID'],
                           params_for_tc=params_for_tc,
                           title=f"Run Assigned Test: {assignment['Code']}")

def get_testcase_dynamic_params_from_db(tcid): # Helper (modified from your /test-case/<tcid>/params)
    conn = None
    try:
        conn = get_db_connection() # Your original connection
        cursor = conn.cursor(dictionary=True)
        # Fetch only DYNAMIC params needed for input fields
        cursor.execute("""
          SELECT ParamName, InputType, StepOrder, InpType
          FROM steps
          WHERE TestCaseID = %s AND InputType = 'dynamic' AND ParamName IS NOT NULL AND ParamName != ''
          ORDER BY StepOrder
        """, (tcid,))
        params = cursor.fetchall()
        return params
    except mysql.connector.Error as err:
        app.logger.error(f"DB error in get_testcase_dynamic_params_from_db for TCID {tcid}: {err}", exc_info=True)
        return []
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()


# ******************************************************************************
# * EXISTING ROUTES TO BE MODIFIED/PROTECTED                                   *
# ******************************************************************************

# --- Your original index route, now acts as a dispatcher or a simple landing ---
@app.route('/')
@login_required # Now requires login
def index():
    # Clean up old output file if it exists (your existing logic)
    with state_lock:
        if test_status['output_file'] and not test_status['running']:
            try:
                if os.path.exists(test_status['output_file']):
                    os.remove(test_status['output_file'])
                    app.logger.info(f"Cleaned up old output file: {test_status['output_file']}")
            except OSError as e:
                app.logger.error(f"Error cleaning up file {test_status['output_file']}: {e}")
            finally:
                 test_status['output_file'] = None
                 test_status['final_output'] = ''
                 test_status['report_path'] = None

    # Redirect to role-specific dashboard
    if current_user.role == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif current_user.role == 'manager':
        return redirect(url_for('manager_dashboard'))
    elif current_user.role == 'tester':
        return redirect(url_for('tester_dashboard'))
    else: # Should not happen if roles are set
        flash("User role not recognized. Logging out.", "danger")
        logout_user()
        return redirect(url_for('login'))

# --- /run-test MODIFIED to include user and assignment context ---
@app.route('/run-test', methods=['POST'])
@login_required # Testers (and maybe Admins for ad-hoc) can run tests
def run_test():
    # Role check for who can run tests
    if not (current_user.role == 'tester' or current_user.role == 'admin'):
        app.logger.warning(f"Unauthorized test run attempt by user {current_user.username} (role: {current_user.role}).")
        return jsonify({'status': 'error', 'message': 'You are not authorized to run tests.'}), 403

    global test_status # Keep your global state for now

    with state_lock:
        if test_status['running']:
            return jsonify({'status': 'error', 'message': 'A test is already running.'}), 409 # Conflict

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_dir = 'reports/live_output'
        os.makedirs(output_dir, exist_ok=True)
        output_file_path = os.path.join(output_dir, f'test_output_{timestamp}.txt')

        test_status['running'] = True
        test_status['output_file'] = output_file_path
        test_status['final_output'] = ''
        test_status['report_path'] = None
        test_status['process'] = None
        test_status['thread'] = None

    data = request.get_json()
    device_id = data.get('device_id', '').strip()
    android_ver = data.get('android_version', '').strip()
    password = data.get('password', '').strip()
    testcase_id = data.get('test_case') # This is TestCaseID from the form
    assignment_id = data.get('assignment_id') # NEW: Passed from run_assigned_test.html form

    if not testcase_id:
        with state_lock: test_status['running'] = False # Reset state
        return jsonify({'status': 'error', 'message': 'Test Case ID is missing.'}), 400

    # If it's an assigned test, update assignment status to 'IN_PROGRESS'
    if assignment_id:
        conn_assign_update = None
        try:
            conn_assign_update = get_db_conn_from_models()
            cursor_assign_update = conn_assign_update.cursor()
            # Ensure the assignment belongs to this user and is in a runnable state
            update_query = """
                UPDATE test_assignments
                SET Status = 'IN_PROGRESS'
                WHERE AssignmentID = %s AND AssignedToUserID = %s AND Status = 'PENDING'
            """
            result = cursor_assign_update.execute(update_query, (assignment_id, current_user.id))
            conn_assign_update.commit()
            if cursor_assign_update.rowcount == 0:
                app.logger.warning(f"Failed to update assignment {assignment_id} to IN_PROGRESS for user {current_user.id}. May not exist, not belong to user, or not PENDING.")
                # Optional: Decide if this should halt the test run
            else:
                 app.logger.info(f"Assignment {assignment_id} status updated to IN_PROGRESS for user {current_user.id}.")
        except mysql.connector.Error as err:
            if conn_assign_update: conn_assign_update.rollback()
            app.logger.error(f"DB error updating assignment {assignment_id} status: {err}", exc_info=True)
            # Optional: Decide if this should halt the test run
        finally:
            if conn_assign_update and conn_assign_update.is_connected():
                cursor_assign_update.close()
                conn_assign_update.close()

    runner_path = os.path.join(os.path.dirname(__file__), 'generic_runner.py')
    cmd = [
        sys.executable, runner_path,
        device_id,
        android_ver,
        str(testcase_id),
        str(current_user.id) # Pass current user's ID to runner
    ]
    # Conditionally add password and assignment_id
    if password:
        cmd.append(password)
    else: # Add a placeholder if no password, so assignment_id index is consistent
        cmd.append("NO_PASSWORD_PLACEHOLDER")

    if assignment_id:
        cmd.append(str(assignment_id))
    else:
        cmd.append("NO_ASSIGNMENT_ID_PLACEHOLDER")


    dynamic_params = {
      k: v for k, v in data.items()
      if k not in ('device_id','android_version','password','test_case', 'assignment_id')
    }
    env = os.environ.copy()
    env['DYNAMIC_PARAMS'] = json.dumps(dynamic_params) # Your existing dynamic param handling

    app.logger.info(f"User {current_user.username} (ID: {current_user.id}) initiating test. Command: {' '.join(cmd)}")
    app.logger.info(f"Dynamic params for test: {dynamic_params}")

    thread = threading.Thread(target=run_test_subprocess, args=(cmd, output_file_path, env))
    with state_lock:
         test_status['thread'] = thread
    thread.start()

    return jsonify({'status': 'started', 'assignment_id': assignment_id, 'output_file': output_file_path})


# --- /get-progress (largely your existing code, ensure it's robust) ---
@app.route('/get-progress')
@login_required # Users polling for progress should be logged in
def get_progress():
    # Your existing get_progress logic, no direct user-specific changes needed here usually,
    # as it reports on the single global test_status.
    # If multiple tests ran concurrently, this would need major rework.
    global test_status
    output_content = ""
    is_running = False
    report_path = None
    error_message = None # For specific file reading errors

    with state_lock:
        is_running = test_status['running']
        current_output_file = test_status['output_file']
        report_path = test_status['report_path']

        if current_output_file and os.path.exists(current_output_file):
            try:
                with open(current_output_file, 'r', encoding='utf-8') as f:
                    output_content = f.read()
            except Exception as e:
                error_message = f"Error reading output file: {e}"
                app.logger.error(f"Error reading output file {current_output_file}: {e}", exc_info=True)
        elif current_output_file and not os.path.exists(current_output_file) and is_running:
            # File might not have been created yet if process is very quick to start/fail
             output_content = "Output file not yet available or path is incorrect..."
        
        if not is_running and test_status['final_output']:
             output_content = test_status['final_output']
             report_path = test_status['report_path']

    # Cleanup logic from your original code
    if not is_running and current_output_file: # Check current_output_file again, not test_status['output_file']
         with state_lock:
              # Double check state to prevent race conditions if another test started *just* now
              if test_status['output_file'] == current_output_file and not test_status['running']:
                   try:
                        if os.path.exists(current_output_file):
                            os.remove(current_output_file)
                            app.logger.info(f"Cleaned up final output file: {current_output_file}")
                   except OSError as e:
                        app.logger.error(f"Error cleaning up final file {current_output_file}: {e}")
                   finally:
                        test_status['output_file'] = None
                        test_status['final_output'] = ''
                        # Keep report_path as it's part of the final status of THIS request's polled test.
                        # It will be overwritten by the next test run.
    
    response_data = {
        'output': output_content,
        'running': is_running,
        'report_path': report_path
    }
    if error_message:
        response_data['error'] = error_message
        if not output_content: # If file read failed completely
            response_data['output'] = error_message

    return jsonify(response_data)


# --- /test-case/<tcid>/params (your existing, for dynamic param form population on older UIs if any) ---
@app.route('/test-case/<int:tcid>/params')
@login_required # Or open if needed by some non-authenticated part
def get_testcase_params(tcid):
    # This seems to be for your older UI. The new run_assigned_test_page uses
    # get_testcase_dynamic_params_from_db internally.
    # If this endpoint is still used by a UI, ensure it provides what's needed.
    params = get_testcase_dynamic_params_from_db(tcid) # Re-use the helper
    return jsonify(params)


# --- /api/steps/<testcase_id> (your existing) ---
@app.route('/api/steps/<int:testcase_id>')
@login_required # Assuming this needs login
def api_steps(testcase_id):
    # Your existing logic - make sure DB connection is handled well
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT StepOrder, Input, ExpectedResponse, ParamName, InputType
              FROM steps
             WHERE TestCaseID = %s
             ORDER BY StepOrder
        """, (testcase_id,))
        steps = cursor.fetchall()
        return jsonify(steps)
    except mysql.connector.Error as err:
        app.logger.error(f"API DB error in /api/steps/{testcase_id}: {err}", exc_info=True)
        return jsonify({"error": "Database query failed"}), 500
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

# --- /api/test-stats (your existing) ---
@app.route('/api/test-stats')
# @login_required # Decide if this needs login
def api_test_stats():
    # Your existing logic - make sure DB connection is handled well
    # Consider if stats should be filtered by user or role in the future.
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT
                COUNT(*) AS TotalExecutions,
                SUM(CASE WHEN OverallStatus = 'PASS' THEN 1 ELSE 0 END) AS PassedCount,
                SUM(CASE WHEN OverallStatus = 'FAIL' THEN 1 ELSE 0 END) AS FailedCount
            FROM testexecutions;
        """
        cursor.execute(query)
        stats_data = cursor.fetchone()

        if not stats_data or stats_data['TotalExecutions'] is None: # Handle case where table is empty
            return jsonify({
                "total_executions": 0, "passed_tests": 0, "failed_tests": 0,
                "passed_percentage": "0.0%", "failed_percentage": "0.0%"
            })

        total_executions = stats_data.get('TotalExecutions', 0)
        passed_count = stats_data.get('PassedCount', 0) if stats_data.get('PassedCount') is not None else 0
        failed_count = stats_data.get('FailedCount', 0) if stats_data.get('FailedCount') is not None else 0

        passed_percentage = (passed_count / total_executions * 100) if total_executions > 0 else 0
        failed_percentage = (failed_count / total_executions * 100) if total_executions > 0 else 0
        
        return jsonify({
            "total_executions": total_executions,
            "passed_tests": passed_count,
            "failed_tests": failed_count,
            "passed_percentage": f"{passed_percentage:.1f}%",
            "failed_percentage": f"{failed_percentage:.1f}%"
        })
    except mysql.connector.Error as err:
        app.logger.error(f"API DB error in /api/test-stats: {err}", exc_info=True)
        return jsonify({"error": "Database query failed"}), 500
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()


# --- /api/test-cases (your existing) ---
@app.route('/api/test-cases')
@login_required # Assuming this needs login
def api_test_cases_list():
    # Your existing logic - make sure DB connection is handled well
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        test_cases_query = """
            SELECT
                tc.TestCaseID AS id,
                tc.Code AS code,
                tc.Name AS name,
                COALESCE(tc.Module, 'Uncategorized') AS module,
                COUNT(s.StepID) AS step_count
            FROM testcases tc
            LEFT JOIN steps s ON tc.TestCaseID = s.TestCaseID
            GROUP BY tc.TestCaseID, tc.Code, tc.Name, tc.Module
            ORDER BY tc.Module, tc.Name;
        """
        cursor.execute(test_cases_query)
        test_cases = cursor.fetchall()

        
        modules_query = """
            SELECT DISTINCT COALESCE(Module, 'Uncategorized') AS module
            FROM testcases
            ORDER BY module;
        """
        cursor.execute(modules_query)
        modules_result = cursor.fetchall()
        modules = [row['module'] for row in modules_result]
        return jsonify({"test_cases": test_cases, "modules": modules})
    except mysql.connector.Error as err:
        app.logger.error(f"API DB error in /api/test-cases: {err}", exc_info=True)
        return jsonify({"error": "Database query failed"}), 500
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

# --- Test Results Views MODIFIED for role-based filtering ---
@app.route('/test-results')
@login_required
def test_results_overview():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Base query from your original code, slightly adjusted for clarity
        base_query_fields = """
            SELECT
                te.ExecutionID, te.ExecutionTime, te.OverallStatus,
                te.Parameters, te.LogMessage AS ExecutionLog,
                tc.TestCaseID, tc.Code AS TestCaseCode, tc.Name AS TestCaseName,
                COALESCE(tc.Module, 'Uncategorized') AS Module,
                d.SerialNumber AS DeviceSerial, d.Name AS DeviceName,
                u.Username AS ExecutedByUsername
                -- ts.Name AS SuiteName -- Suite info seems less relevant now with direct assignments
        """
        base_query_from_join = """
            FROM testexecutions te
            JOIN testcases tc ON te.TestCaseID = tc.TestCaseID
            LEFT JOIN devices d ON te.DeviceID = d.DeviceID /* Use LEFT JOIN if device can be optional */
            JOIN users u ON te.ExecutedBy = u.UserID
            -- LEFT JOIN testsuites ts ON te.SuiteID = ts.SuiteID /* If you still use suites */
        """
        
        filter_conditions = []
        query_params = []

        if current_user.role == 'tester':
            filter_conditions.append("te.ExecutedBy = %s")
            query_params.append(current_user.id)
        
        # Managers and Admins see all results by default based on your requirements
        # If Managers should only see results from their 'team' or tests they assigned,
        # this would require more complex logic (e.g., linking executions back to assignments they made).

        full_query = base_query_fields + base_query_from_join
        if filter_conditions:
            full_query += " WHERE " + " AND ".join(filter_conditions)
        full_query += " ORDER BY Module, te.ExecutionTime DESC;"
        
        cursor.execute(full_query, tuple(query_params))
        executions = cursor.fetchall()

        # Grouping logic (your existing)
        grouped_executions = defaultdict(list)
        for exec_item in executions:
            grouped_executions[exec_item['Module']].append(exec_item)
        
        sorted_grouped_executions = {}
        # Ensure 'Uncategorized' is handled consistently in sort if present
        module_keys = sorted(grouped_executions.keys(), key=lambda m: (m == 'Uncategorized', str(m).lower()))
        for key in module_keys:
            sorted_grouped_executions[key] = grouped_executions[key]

        return render_template('results/test_result_overview.html', # Renamed template path for clarity
                               grouped_executions=sorted_grouped_executions,
                               user_role=current_user.role)
    except mysql.connector.Error as err:
        app.logger.error(f"DB error in test_results_overview for user {current_user.username}: {err}", exc_info=True)
        flash("Error fetching test results.", "danger")
        return render_template('results/test_result_overview.html', grouped_executions={}, user_role=current_user.role)
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()


@app.route('/test-results/execution/<int:execution_id>')
@login_required
def execution_detail(execution_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Authorization: Testers can only see their own execution details.
        # Managers/Admins can see all.
        if current_user.role == 'tester':
            cursor.execute("""
                SELECT te.ExecutionID FROM testexecutions te
                WHERE te.ExecutionID = %s AND te.ExecutedBy = %s
            """, (execution_id, current_user.id))
            if not cursor.fetchone():
                flash("You are not authorized to view this execution detail.", "danger")
                app.logger.warning(f"Tester {current_user.username} tried to access unauthorized execution detail {execution_id}.")
                return redirect(url_for('test_results_overview'))

        # Fetch execution summary (your existing query, ensure fields are correct)
        cursor.execute("""
            SELECT
                te.ExecutionID, te.ExecutionTime, te.OverallStatus,
                te.Parameters, te.LogMessage AS ExecutionLog,
                tc.Code AS TestCaseCode, tc.Name AS TestCaseName, tc.Module AS TestCaseModule, /* Alias Module */
                d.SerialNumber AS DeviceSerial, d.Name AS DeviceName,
                u.Username AS ExecutedByUsername
                -- ts.Name AS SuiteName /* If using suites */
            FROM testexecutions te
            JOIN testcases tc ON te.TestCaseID = tc.TestCaseID
            LEFT JOIN devices d ON te.DeviceID = d.DeviceID
            JOIN users u ON te.ExecutedBy = u.UserID
            -- LEFT JOIN testsuites ts ON te.SuiteID = ts.SuiteID
            WHERE te.ExecutionID = %s;
        """, (execution_id,))
        execution_summary = cursor.fetchone()

        if not execution_summary:
            flash(f"Execution ID {execution_id} not found.", "warning")
            return redirect(url_for('test_results_overview')) # Or a 404 page

        # Fetch step results (your existing query)
        cursor.execute("""
            SELECT
                sr.StepResultID, sr.ActualInput, sr.ActualOutput, sr.Status, sr.Screenshot,
                sr.StartTime, sr.EndTime, sr.Duration, sr.LogMessage AS StepLog,
                s.StepOrder, s.Input AS OriginalStepInput, s.ExpectedResponse AS OriginalExpectedResponse,
                s.ParamName, s.InputType
            FROM stepresults sr
            JOIN steps s ON sr.StepID = s.StepID
            WHERE sr.ExecutionID = %s
            ORDER BY s.StepOrder;
        """, (execution_id,))
        step_results = cursor.fetchall()

        return render_template('results/execution_detail.html', # Renamed template path
                               execution=execution_summary,
                               steps=step_results)
    except mysql.connector.Error as err:
        app.logger.error(f"DB error in execution_detail {execution_id} for user {current_user.username}: {err}", exc_info=True)
        flash("Error fetching execution details.", "danger")
        return redirect(url_for('test_results_overview'))
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()



@app.route('/get-modules')
def get_modules():

    app_type = request.args.get('appType', type=int)

    conn = mysql.connector.connect(
        host='localhost',
        user='root',
        password='',
        database='actual_db',
        autocommit=False
    )

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
        SELECT
           SuiteID,
           Name
        FROM testsuites
        WHERE AppType = %s
        """, (app_type,))
        modules = cursor.fetchall()
        cursor.close()
        conn.close()
        return jsonify(modules)
    
    except Exception as e:
        conn.rollback()
        raise   

    finally:
        cursor.close()
        conn.close()

@app.route("/get-applications")
def get_applications():

    connection = mysql.connector.connect(
        host='localhost',
        user='root',
        password='',
        database='actual_db',
        autocommit=False
    )

    try:
        cursor = connection.cursor()
        cursor.execute("""
        SELECT
           id,
           name
        FROM application
        """)
        data = [{"id": row[0], "name": row[1]} for row in cursor.fetchall()]
        cursor.close()
        connection.close()
        return jsonify(data)
    
    except Exception as e:
        connection.rollback()
        raise   

    finally:
        cursor.close()
        connection.close()



@app.route('/apptype')
def get_apptypes():
    try:
        conn = mysql.connector.connect(
            host='localhost',
            user='root',
            password='',
            database='actual_db',
            autocommit=False
        )
        cursor = conn.cursor(dictionary=True)
        cursor.execute(""" 
                       SELECT 
                       id, 
                       name 
                       FROM application """)
        
        applications = cursor.fetchall()
        return jsonify(applications)
    except Exception as e:
        print("Error fetching applications:", e)
        return jsonify([]), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/add-module', methods=['POST'])
def add_module():
    data = request.json
    now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    module_name = data.get('module_name')
    app_type = data.get('application_type')

    if not module_name or not app_type:
        return jsonify(success=False, error="Missing values"), 400

    try:
        conn = mysql.connector.connect(
            host='localhost',
            user='root',
            password='',
            database='actual_db',
            autocommit=False
        )
        
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO testsuites (Name, AppType, Description, CreatedAt, ModifiedAt)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            data['module_name'],
            data['application_type'],
            data.get('description_model'),
            now,
            now
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify(success=True)
    
    except Exception as e:
        conn.rollback()
        return jsonify(success=False, error=str(e)), 500

# --- Helper function to run subprocess in background (YOUR EXISTING CODE) ---
# Make sure this function `run_test_subprocess` is defined as in your original code.
# No direct changes are needed in this function itself for user management,
# as the necessary user/assignment IDs are passed in the `cmd` argument.
def run_test_subprocess(cmd, output_file_path, env):
    global test_status
    full_output = ""
    final_report_path = None # Initialize
    error_occurred = False # Initialize

    app.logger.info(f"Starting subprocess: {' '.join(cmd)} with env DYNAMIC_PARAMS: {env.get('DYNAMIC_PARAMS')}")

    try:
        proc = subprocess.Popen(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, # Capture stderr separately
                                text=True,
                                bufsize=1, # Line buffered
                                universal_newlines=True, # Ensures text mode works across platforms
                                env=env)
        with state_lock:
             test_status['process'] = proc

        # Correct way to read stdout and stderr line by line and interleave them (somewhat)
        # For perfect interleaving, select() or more complex threading is needed,
        # but this is often good enough.
        
        # Open file in append mode, ensure it's UTF-8
        with open(output_file_path, 'w', encoding='utf-8') as f_out: # Start with 'w' to truncate
            f_out.seek(0)
            f_out.truncate() 
            # Read stdout line by line
            for line in proc.stdout: # iter(proc.stdout.readline, ''):
                line_strip = line.strip()
                app.logger.debug(f"RUNNER_STDOUT: {line_strip}") # Log to Flask console
                f_out.write(line)
                full_output += line
                if 'report saved at:' in line_strip.lower():
                    final_report_path = line_strip.split('report saved at:', 1)[-1].strip()
                f_out.flush() # Ensure data is written to disk immediately

            # After stdout is exhausted, read all of stderr
            stderr_output = proc.stderr.read()
            if stderr_output:
                stderr_strip = stderr_output.strip()
                app.logger.error(f"RUNNER_STDERR: {stderr_strip}")
                f_out.write("\n--- STDERR ---\n")
                f_out.write(stderr_output)
                full_output += "\n--- STDERR ---\n" + stderr_output
                error_occurred = True # Assume any stderr output is an error indicator
                f_out.flush()

        proc.wait() # Wait for the process to complete
        app.logger.info(f"Subprocess finished with exit code: {proc.returncode}")

        if proc.returncode != 0:
            error_occurred = True # Non-zero exit code also means error

        # Determine final_report_path more robustly
        if not final_report_path: # If not found in stdout
            if stderr_output and 'report saved at:' in stderr_output.lower():
                final_report_path = stderr_output.split('report saved at:', 1)[-1].strip()
            elif error_occurred:
                final_report_path = f"Test script encountered an error or failed (exit code {proc.returncode}). No report path explicitly found."
            else:
                # This case might mean success but no "report saved at" line.
                # Could default to the output file itself or a specific known report location if your script has one.
                final_report_path = f"Test completed (exit code {proc.returncode}). Report path marker not found in output. Check logs."

    except FileNotFoundError:
        msg = "Error: Python executable or runner script not found. Check paths."
        app.logger.error(msg, exc_info=True)
        full_output += f"\n{msg}\n"
        error_occurred = True
        final_report_path = "Execution failed: Script not found."
    except Exception as e:
        msg = f"An unexpected error occurred while running the test subprocess: {e}"
        app.logger.error(msg, exc_info=True)
        full_output += f"\n{msg}\n"
        error_occurred = True
        final_report_path = f"Execution failed: {e}"
    finally:
        with state_lock:
            test_status['running'] = False
            test_status['final_output'] = full_output
            test_status['report_path'] = final_report_path
            test_status['process'] = None
            test_status['thread'] = None
        app.logger.info(f"Test subprocess processing complete. Final report path: {final_report_path}")

# new helper functions for the manager dashboard
# Add these new helper functions somewhere in your app.py

def get_all_applications_for_dashboard():
    conn = None
    try:
        conn = get_db_conn_from_models() # Use your consistent DB connection
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, name FROM application ORDER BY name")
        apps = cursor.fetchall()
        return apps
    except mysql.connector.Error as err:
        app.logger.error(f"DB error in get_all_applications_for_dashboard: {err}", exc_info=True)
        return []
    finally:
        if conn and conn.is_connected():
            if 'cursor' in locals() and cursor:
                cursor.close()
            conn.close()

def get_application_by_id_for_dashboard(app_id):
    conn = None
    try:
        conn = get_db_conn_from_models()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, name FROM application WHERE id = %s", (app_id,))
        app_obj = cursor.fetchone()
        return app_obj
    except mysql.connector.Error as err:
        app.logger.error(f"DB error in get_application_by_id_for_dashboard for app_id {app_id}: {err}", exc_info=True)
        return None
    finally:
        if conn and conn.is_connected():
            if 'cursor' in locals() and cursor:
                cursor.close()
            conn.close()

def get_suites_for_application_for_dashboard(app_id):
    conn = None
    try:
        conn = get_db_conn_from_models()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT SuiteID, Name FROM testsuites WHERE AppType = %s ORDER BY Name", (app_id,))
        suites = cursor.fetchall()
        return suites
    except mysql.connector.Error as err:
        app.logger.error(f"DB error in get_suites_for_application_for_dashboard for app_id {app_id}: {err}", exc_info=True)
        return []
    finally:
        if conn and conn.is_connected():
            if 'cursor' in locals() and cursor:
                cursor.close()
            conn.close()

def get_suite_by_id_for_dashboard(suite_id):
    conn = None
    try:
        conn = get_db_conn_from_models()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT SuiteID, Name, AppType FROM testsuites WHERE SuiteID = %s", (suite_id,))
        suite_obj = cursor.fetchone()
        return suite_obj
    except mysql.connector.Error as err:
        app.logger.error(f"DB error in get_suite_by_id_for_dashboard for suite_id {suite_id}: {err}", exc_info=True)
        return None
    finally:
        if conn and conn.is_connected():
            if 'cursor' in locals() and cursor:
                cursor.close()
            conn.close()

def get_test_cases_for_suite_for_dashboard(suite_id):
    """
    Fetches test cases for a specific suite, including pending assignment info.
    This adapts the logic from your existing `get_all_test_cases_for_assignment`.
    """
    conn = None
    try:
        conn = get_db_conn_from_models()
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT
                tc.TestCaseID, tc.Code, tc.Name, tc.Module,
                GROUP_CONCAT(DISTINCT IF(ta.Status = 'PENDING', u_assigned.Username, NULL) SEPARATOR ', ') AS pending_assigned_to_usernames,
                (SELECT COUNT(*) FROM test_assignments ta_count
                 WHERE ta_count.TestCaseID = tc.TestCaseID AND ta_count.Status = 'PENDING') AS pending_assignments_count
            FROM testcases tc
            JOIN suitetestcases stc ON tc.TestCaseID = stc.TestCaseID
            LEFT JOIN test_assignments ta ON tc.TestCaseID = ta.TestCaseID AND ta.Status = 'PENDING'
            LEFT JOIN users u_assigned ON ta.AssignedToUserID = u_assigned.UserID
            WHERE stc.SuiteID = %s
            GROUP BY tc.TestCaseID, tc.Code, tc.Name, tc.Module
            ORDER BY tc.Code;
        """
        cursor.execute(query, (suite_id,))
        test_cases = cursor.fetchall()
        return test_cases
    except mysql.connector.Error as err:
        app.logger.error(f"DB error in get_test_cases_for_suite_for_dashboard for suite_id {suite_id}: {err}", exc_info=True)
        return []
    finally:
        if conn and conn.is_connected():
            if 'cursor' in locals() and cursor:
                cursor.close()
            conn.close()


# --- Main Execution ---
if __name__ == '__main__':
    # Ensure the reports directory exists (your existing code)
    os.makedirs('reports', exist_ok=True)
    os.makedirs('reports/live_output', exist_ok=True)

    # Clean up any old live output files (your existing code)
    live_output_dir = 'reports/live_output'
    if os.path.exists(live_output_dir):
         for filename in os.listdir(live_output_dir):
              if filename.startswith('test_output_') and filename.endswith('.txt'):
                filepath = os.path.join(live_output_dir, filename)
                if os.path.isfile(filepath):
                    try:
                        os.remove(filepath)
                        app.logger.info(f"Cleaned up old live output file: {filepath}")
                    except OSError as e:
                        app.logger.error(f"Error cleaning up old file {filepath}: {e}")

    app.run(host='0.0.0.0', debug=True, port=5000) # Default Flask port is 5000
    # appium --allow-insecure=adb_shell