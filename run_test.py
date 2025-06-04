# --- Existing Imports ---
from collections import defaultdict
from flask import (Flask, render_template, request, jsonify, Response,
                   redirect, url_for, flash, session, send_file)
import subprocess
import os
import math
import threading
import time
import json
import sys
import io
from flask_cors import CORS
import mysql.connector
import logging # For better logging
from datetime import datetime, timedelta
from flask import request
# --- New Imports ---
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
# from werkzeug.security import generate_password_hash, check_password_hash # Handled in models.py
from android_helper import get_android_version, get_connected_device

# --- MODEL IMPORTS ---
from models import (User, BatchTestAssignment, CustomTestGroup, TestCaseModel, TestAssignment,
                    get_db_connection as get_db_conn_from_models) # Use this for new DB interactions

# --- FORM IMPORTS ---
# Assuming you will create these new forms or adapt existing ones
from forms import (LoginForm, CreateUserForm, AssignTestCaseForm, EditUserForm,
                   AssignSuiteForm, AssignApplicationForm, AssignCustomGroupForm, # NEW Forms
                   CreateEditCustomGroupForm) # NEW Form

from decorators import admin_required, manager_required, tester_required, manager_or_admin_required
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.utils import formatdate
from email.utils import formataddr
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from io import BytesIO
import math

# --- App Setup ---
app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'your_very_strong_unguessable_secret_key_here_39#@!')
app.jinja_env.globals.update(cos=math.cos, sin=math.sin, pi=math.pi)
CORS(app, resources={r"/api/*": {"origins": "http://localhost:3000"}})


# In your Flask app setup
app.jinja_env.globals.update(
    math=math,  # To use math.cos, math.pi
    cos=math.cos,
    sin=math.sin,
    pi=math.pi
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app.logger.setLevel(logging.INFO)



batch_test_processes = {}
batch_state_lock = threading.Lock()


# --- Flask-Login Setup ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
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
    'current_assignment_id': None, # To track which individual assignment is running
    'current_batch_assignment_id': None # To track if it's part of a batch
}
state_lock = threading.Lock()

# --- Database Configuration (from your existing code or models.py) ---
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'actual_db'
}

def get_db_connection(): # Your existing function for older parts of code
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as err:
        app.logger.error(f"Original get_db_connection error: {err}")
        raise

# --- USER ACTIVATION/DEACTIVATION (Existing) ---
@app.route('/admin/users/deactivate/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def deactivate_user(user_id):

    if current_user.role.lower() != 'admin':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    if int(current_user.id) == int(user_id):
        user_to_deactivate = User.get(user_id)
        if user_to_deactivate and user_to_deactivate.role == 'admin':
            # Using get_db_conn_from_models for consistency with User model
            with get_db_conn_from_models() as conn_check:
                with conn_check.cursor(dictionary=True) as cursor_check:
                    cursor_check.execute("SELECT COUNT(*) AS admin_count FROM users WHERE Role = 'admin' AND IsActive = TRUE")
                    admin_count = cursor_check.fetchone()['admin_count']
            if admin_count <= 1:
                flash('You cannot deactivate your own account as the last active admin.', 'danger')
                app.logger.warning(f"Admin {current_user.username} attempted to self-deactivate as last admin.")
                return redirect(url_for('list_users'))

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

    if current_user.role.lower() != 'admin':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    success, message = User.activate(user_id_to_activate=user_id, current_admin_id=current_user.id)
    if success:
        flash(message, 'success')
    else:
        flash(message, 'danger')
    return redirect(url_for('list_users'))

# ******************************************************************************
# * AUTHENTICATION ROUTES (EXISTING)                                           *
# ******************************************************************************
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.find_by_username(form.username.data)
        if user and user.check_password(form.password.data):
            if not user.is_active:
                flash('Your account is deactivated. Please contact an administrator.', 'warning')
                app.logger.warning(f"Deactivated user login attempt: {form.username.data}")
                return render_template('auth/login.html', title='Login', form=form)

            login_user(user, remember=form.remember_me.data)
            # flash('Logged in successfully!', 'success')
            app.logger.info(f"User {user.username} logged in successfully.")
            next_page = request.args.get('next')
            if user.role == 'admin':
                return redirect(next_page or url_for('admin_dashboard'))
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
# * DASHBOARD ROUTES (EXISTING & MODIFIED)                                     *
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

    if current_user.role.lower() != 'admin':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    user_name = current_user.username
    total_users = User.count_all()  # Count all users in the table
    total_tests = TestAssignment.count_all()
    completed_tests = TestAssignment.count_completed()
    pass_tests = TestAssignment.count_pass()
    fail_tests = TestAssignment.count_fail()
    inprogress_tests = TestAssignment.count_inprogress()
    pending_tests = TestAssignment.count_pending()

    completed_percent = 0
    if total_tests > 0:
        completed_percent = round((completed_tests / total_tests) * 100)

    stats = {}

    try:
        query_total_tests = "SELECT COUNT(TestCaseID) as count FROM testcases;"
        result_total_tests = execute_sql_query(query_total_tests, fetch_one=True)
        stats['total_tests'] = result_total_tests['count'] if result_total_tests and result_total_tests['count'] is not None else 0

        # 2. Total Executions
        query_adhoc_executions = "SELECT COUNT(ExecutionID) as count FROM testexecutions;"
        result_adhoc = execute_sql_query(query_adhoc_executions, fetch_one=True)
        adhoc_exec_count = result_adhoc['count'] if result_adhoc and result_adhoc['count'] is not None else 0
        adhoc_exec_count = int(adhoc_exec_count) if adhoc_exec_count is not None else 0

        query_batch_tc_executions = "SELECT SUM(CompletedTestCases) as sum_completed FROM batch_test_assignments WHERE Status LIKE 'COMPLETED_%';"
        result_batch_tc = execute_sql_query(query_batch_tc_executions, fetch_one=True)
        batch_tc_exec_count = result_batch_tc['sum_completed'] if result_batch_tc and result_batch_tc['sum_completed'] is not None else 0
        batch_tc_exec_count = int(batch_tc_exec_count) if batch_tc_exec_count is not None else 0
        stats['total_executions'] = adhoc_exec_count + batch_tc_exec_count

        # 3. Passed Executions
        query_adhoc_passed = "SELECT COUNT(ExecutionID) as count FROM testexecutions WHERE OverallStatus = 'PASS';"
        result_adhoc_passed = execute_sql_query(query_adhoc_passed, fetch_one=True)
        adhoc_passed_count = result_adhoc_passed['count'] if result_adhoc_passed and result_adhoc_passed['count'] is not None else 0
        adhoc_passed_count = int(adhoc_passed_count) if adhoc_passed_count is not None else 0

        query_batch_passed_tc = "SELECT SUM(PassedTestCases) as sum_passed FROM batch_test_assignments WHERE Status LIKE 'COMPLETED_%';"
        result_batch_passed = execute_sql_query(query_batch_passed_tc, fetch_one=True)
        batch_passed_count = result_batch_passed['sum_passed'] if result_batch_passed and result_batch_passed['sum_passed'] is not None else 0
        batch_passed_count = int(batch_passed_count) if batch_passed_count is not None else 0
        stats['passed_executions'] = adhoc_passed_count + batch_passed_count

        # 4. Failed Executions
        total_exec = stats.get('total_executions', 0)
        passed_exec = stats.get('passed_executions', 0)
        stats['failed_executions'] = total_exec - passed_exec

        # 5. Active Testers
        query_active_testers = "SELECT COUNT(UserID) as count FROM users WHERE Role = 'tester' AND IsActive = 1;"
        result_active_testers = execute_sql_query(query_active_testers, fetch_one=True)
        stats['active_testers'] = result_active_testers['count'] if result_active_testers and result_active_testers['count'] is not None else 0


        # --- Data for Charts (Manager/Admin) ---
        if current_user.role in ['admin', 'manager']:
            # Chart 1: Executions by Batch
            query_batch_summary = """
                SELECT BatchAssignmentID, ReferenceName, PassedTestCases, (CompletedTestCases - PassedTestCases) as FailedTestCases
                FROM batch_test_assignments
                WHERE Status LIKE 'COMPLETED_%'
                ORDER BY AssignmentDate DESC
                LIMIT 5;
            """
            batch_execution_data = execute_sql_query(query_batch_summary)
            stats['batch_chart_labels'] = [b['ReferenceName'] for b in batch_execution_data] if batch_execution_data else []
            stats['batch_chart_pass_data'] = [int(b['PassedTestCases']) if b['PassedTestCases'] is not None else 0 for b in batch_execution_data] if batch_execution_data else []
            stats['batch_chart_fail_data'] = [int(b['FailedTestCases']) if b['FailedTestCases'] is not None else 0 for b in batch_execution_data] if batch_execution_data else []

            # Chart 2: Executions by Application (Pass/Fail from adhoc)
            query_app_summary = """
                SELECT
                    app.name as ApplicationName,
                    SUM(CASE WHEN te.OverallStatus = 'PASS' THEN 1 ELSE 0 END) as PassedCount,
                    SUM(CASE WHEN te.OverallStatus = 'FAIL' THEN 1 ELSE 0 END) as FailedCount
                FROM testexecutions te
                JOIN testcases tc ON te.TestCaseID = tc.TestCaseID
                JOIN testsuites ts ON tc.Module_id = ts.SuiteID
                JOIN application app ON ts.AppType = app.id
                GROUP BY app.name
                ORDER BY app.name;
            """
            app_execution_data = execute_sql_query(query_app_summary)
            stats['app_chart_labels'] = []
            stats['app_chart_pass_data'] = []
            stats['app_chart_fail_data'] = []
            if app_execution_data:
                for app_data in app_execution_data:
                    stats['app_chart_labels'].append(app_data['ApplicationName'])
                    stats['app_chart_pass_data'].append(int(app_data['PassedCount']) if app_data['PassedCount'] is not None else 0)
                    stats['app_chart_fail_data'].append(int(app_data['FailedCount']) if app_data['FailedCount'] is not None else 0)

            # MODIFIED: Chart for ALL Test Assignments by Priority
            # MODIFIED: Chart for ALL EXECUTED Test Assignments by Priority
            stats['priority_pie_labels'] = ['HIGH', 'MEDIUM', 'LOW'] # Match ENUM values case
            # Updated Colors: High: Green (#28a745), Medium: Blue (#007bff), Low: Red (#dc3545)
            stats['priority_pie_colors'] = ['#28a745', '#007bff', '#dc3545'] 
            
            # MODIFIED QUERY: Count all executed (passed or failed) assignments by priority
            query_executed_by_priority = """
                SELECT
                    Priority, 
                    COUNT(*) as count
                FROM test_assignments  
                WHERE Status IN ('EXECUTED_PASS', 'EXECUTED_FAIL') 
                GROUP BY Priority;
            """
            priority_results = execute_sql_query(query_executed_by_priority)
            
            priority_counts_from_db = {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0} 
            if priority_results:
                for row in priority_results:
                    priority_key = row.get('Priority') 
                    if priority_key in priority_counts_from_db: 
                        priority_counts_from_db[priority_key] = int(row['count']) if row['count'] is not None else 0
            
            stats['priority_pie_data'] = [
                priority_counts_from_db.get('HIGH', 0), 
                priority_counts_from_db.get('MEDIUM', 0),
                priority_counts_from_db.get('LOW', 0)
            ]


            execution_trend_dates = []
            execution_trend_pass = []
            execution_trend_fail = []
            for i in range(6, -1, -1): 
                day_date = datetime.now() - timedelta(days=i)
                day_str = day_date.strftime('%Y-%m-%d')
                execution_trend_dates.append(day_date.strftime('%b %d'))

                query_trend_pass = """
                    SELECT COUNT(ExecutionID) as count
                    FROM testexecutions
                    WHERE OverallStatus = 'PASS' AND DATE(ExecutionTime) = %s;
                """
                pass_count_result = execute_sql_query(query_trend_pass, params=(day_str,), fetch_one=True)
                execution_trend_pass.append(int(pass_count_result['count']) if pass_count_result and pass_count_result['count'] is not None else 0)

                query_trend_fail = """
                    SELECT COUNT(ExecutionID) as count
                    FROM testexecutions
                    WHERE OverallStatus = 'FAIL' AND DATE(ExecutionTime) = %s;
                """
                fail_count_result = execute_sql_query(query_trend_fail, params=(day_str,), fetch_one=True)
                execution_trend_fail.append(int(fail_count_result['count']) if fail_count_result and fail_count_result['count'] is not None else 0)

            stats['execution_trend_dates'] = execution_trend_dates
            stats['execution_trend_pass_data'] = execution_trend_pass
            stats['execution_trend_fail_data'] = execution_trend_fail

    except Exception as e:
        print(f"Error in analytics_dashboard: {e}") # Good for server-side logging
        flash(f"Error fetching analytics data: {str(e)}. Please try again later or contact support.", "danger")
        # Initialize with defaults to prevent template errors
        stats.setdefault('total_tests', 'Error')
        stats.setdefault('total_executions', 'Error')
        stats.setdefault('passed_executions', 'Error')
        stats.setdefault('failed_executions', 'Error')
        stats.setdefault('active_testers', 'Error')
        stats.setdefault('batch_chart_labels', [])
        stats.setdefault('batch_chart_pass_data', [])
        stats.setdefault('batch_chart_fail_data', [])
        stats.setdefault('app_chart_labels', [])
        stats.setdefault('app_chart_pass_data', [])
        stats.setdefault('app_chart_fail_data', [])
        stats.setdefault('priority_pie_labels', ['HIGH', 'MEDIUM', 'LOW'])
        stats.setdefault('priority_pie_data', [0,0,0])
        stats.setdefault('priority_pie_colors', ['#0d6efd', '#6ea8fe', '#dc3545'])
        stats.setdefault('execution_trend_dates', [])
        stats.setdefault('execution_trend_pass_data', [])
        stats.setdefault('execution_trend_fail_data', [])

    return render_template(
        'admin/index.html', 
        total_users=total_users, 
        total_tests=total_tests, 
        completed_percent=completed_percent,
        pass_tests=pass_tests,
        fail_tests=fail_tests,
        inprogress_tests=inprogress_tests,
        pending_tests=pending_tests,
        user_name=user_name, 
        title='Admin Dashboard',
        stats=stats
        )

@app.route('/manager/dashboard')
@login_required
@manager_or_admin_required # Assuming admin can also view manager dashboard
def manager_dashboard():

    if current_user.role.lower() != 'manager':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    selected_app_id_str = request.args.get('app_id')
    selected_suite_id_str = request.args.get('suite_id')

    selected_app_id = int(selected_app_id_str) if selected_app_id_str and selected_app_id_str.isdigit() else None
    selected_suite_id = int(selected_suite_id_str) if selected_suite_id_str and selected_suite_id_str.isdigit() else None

    applications_list = get_all_applications_for_dashboard()
    selected_app_details = None
    test_suites_list = []
    selected_suite_details = None
    test_cases_list = []

    if selected_app_id:
        selected_app_details = get_application_by_id_for_dashboard(selected_app_id)
        if not selected_app_details:
            flash(f"Application with ID {selected_app_id} not found.", "warning")
            return redirect(url_for('manager_dashboard'))
        test_suites_list = get_suites_for_application_for_dashboard(selected_app_id)

        if selected_suite_id:
            selected_suite_details = get_suite_by_id_for_dashboard(selected_suite_id)
            if not selected_suite_details or selected_suite_details['AppType'] != selected_app_id:
                flash(f"Test Suite with ID {selected_suite_id} not found or doesn't belong to the selected application.", "warning")
                return redirect(url_for('manager_dashboard', app_id=selected_app_id))
            test_cases_list = get_test_cases_for_suite_for_dashboard(selected_suite_id)

    return render_template('manager/dashboard.html',
                           title='Manager Dashboard',
                           applications=applications_list,
                           selected_app_id=selected_app_id,
                           selected_app=selected_app_details,
                           test_suites=test_suites_list,
                           selected_suite_id=selected_suite_id,
                           selected_suite=selected_suite_details,
                           test_cases=test_cases_list)

@app.route('/tester/dashboard')
@login_required
@tester_required
def tester_dashboard():

    if current_user.role.lower() != 'tester':
        flash("Unauthorized access. You have been logged out.", "danger")
        logout_user()
        return redirect(url_for("login"))

    individual_assigned_tests = []
    batch_assigned_tests = []
    conn = None
    try:
        conn = get_db_conn_from_models()
        cursor = conn.cursor(dictionary=True)

        search_query = request.args.get('search', '').strip()

        like_pattern = f"%{search_query}%"

        # --- Individual test cases ---
        if search_query:
            ind_query = """
                SELECT
                    ta.AssignmentID, tc.Code, tc.Name, tc.Module,
                    u_assigner.Username AS AssignedBy, ta.AssignmentDate, ta.Status, ta.Notes, ta.Priority
                FROM test_assignments ta
                JOIN testcases tc ON ta.TestCaseID = tc.TestCaseID
                JOIN users u_assigner ON ta.AssignedByUserID = u_assigner.UserID
                WHERE ta.AssignedToUserID = %s AND ta.BatchAssignmentID IS NULL
                    AND ta.Status IN ('PENDING', 'IN_PROGRESS')
                    AND (
                        tc.Code LIKE %s OR
                        tc.Name LIKE %s OR
                        tc.Description LIKE %s OR
                        u_assigner.Username LIKE %s OR
                        ta.Priority LIKE %s OR
                        tc.Module LIKE %s
                    )
                ORDER BY FIELD(ta.Priority, 'HIGH', 'MEDIUM', 'LOW'), ta.AssignmentDate ASC
            """
            cursor.execute(ind_query, (current_user.id, like_pattern, like_pattern, like_pattern, like_pattern, like_pattern, like_pattern))
        else:
            ind_query = """
                SELECT
                    ta.AssignmentID, tc.Code, tc.Name, tc.Module,
                    u_assigner.Username AS AssignedBy, ta.AssignmentDate, ta.Status, ta.Notes, ta.Priority
                FROM test_assignments ta
                JOIN testcases tc ON ta.TestCaseID = tc.TestCaseID
                JOIN users u_assigner ON ta.AssignedByUserID = u_assigner.UserID
                WHERE ta.AssignedToUserID = %s AND ta.BatchAssignmentID IS NULL
                    AND ta.Status IN ('PENDING', 'IN_PROGRESS')
                ORDER BY FIELD(ta.Priority, 'HIGH', 'MEDIUM', 'LOW'), ta.AssignmentDate ASC
            """
            cursor.execute(ind_query, (current_user.id,))
        
        individual_assigned_tests = cursor.fetchall()

        # --- Batch test assignments ---
        if search_query:
            batch_query = """
                SELECT bta.*, u_assigner.Username AS AssignedByUsername
                FROM batch_test_assignments bta
                JOIN users u_assigner ON bta.AssignedByUserID = u_assigner.UserID
                WHERE bta.AssignedToUserID = %s AND bta.Status IN ('PENDING', 'IN_PROGRESS')
                    AND (
                        bta.ReferenceName LIKE %s OR
                        bta.Notes LIKE %s OR
                        u_assigner.Username LIKE %s OR
                        bta.Status LIKE %s OR
                        bta.Priority LIKE %s
                    )
                ORDER BY FIELD(bta.Priority, 'HIGH', 'MEDIUM', 'LOW'), bta.AssignmentDate ASC
            """
            cursor.execute(batch_query, (current_user.id, like_pattern, like_pattern, like_pattern, like_pattern, like_pattern))
        else:
            batch_query = """
                SELECT bta.*, u_assigner.Username AS AssignedByUsername
                FROM batch_test_assignments bta
                JOIN users u_assigner ON bta.AssignedByUserID = u_assigner.UserID
                WHERE bta.AssignedToUserID = %s AND bta.Status IN ('PENDING', 'IN_PROGRESS')
                ORDER BY FIELD(bta.Priority, 'HIGH', 'MEDIUM', 'LOW'), bta.AssignmentDate ASC
            """
            cursor.execute(batch_query, (current_user.id,))
        
        batch_assigned_tests = cursor.fetchall()

    except mysql.connector.Error as err:
        app.logger.error(f"DB error fetching assigned tests for tester {current_user.id}: {err}", exc_info=True)
        flash('Error fetching your assigned tests.', 'danger')
    finally:
        if conn and conn.is_connected():
            if 'cursor' in locals() and cursor:
                cursor.close()
            conn.close()

    high_priority_individual_count = sum(1 for test in individual_assigned_tests if test.get('Priority') == 'HIGH')

    # Count high-priority batch tests
    high_priority_batch_count = sum(1 for test in batch_assigned_tests if test.get('Priority') == 'HIGH')
    return render_template('tester/dashboard.html',
                           title='Tester Dashboard',
                           individual_assigned_tests=individual_assigned_tests,
                           batch_assigned_tests=batch_assigned_tests,
                           high_priority_individual_count=high_priority_individual_count,
                           high_priority_batch_count=high_priority_batch_count)



# ******************************************************************************
# * USER MANAGEMENT (ADMIN - EXISTING)                                         *
# ******************************************************************************
@app.route('/admin/users')
@login_required
@admin_required
def list_users():

    if current_user.role.lower() != 'admin':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    users = User.get_all_users()
    user_name = current_user.username
    return render_template('admin/list_users.html', title='Manage Users', users=users, user_name=user_name)

@app.route('/admin/users/create', methods=['GET', 'POST'])
@login_required
@admin_required
def create_user():

    if current_user.role.lower() != 'admin':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    form = CreateUserForm()
    if form.validate_on_submit():
        success, message_or_id = User.create(
            username=form.username.data,
            password=form.password.data,
            role=form.role.data
        )
        if success:
            flash(f'User "{form.username.data}" created successfully (ID: {message_or_id}).', 'success')
            return redirect(url_for('list_users'))
        else:
            flash(f'Error creating user: {message_or_id}', 'danger')
    return render_template('admin/create_user.html', title='Create New User', form=form)

@app.route('/admin/users/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):

    if current_user.role.lower() != 'admin':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    user_to_edit = User.get(user_id)
    if not user_to_edit:
        flash('User not found.', 'danger')
        return redirect(url_for('list_users'))

    form = EditUserForm(original_username=user_to_edit.username)
    if form.validate_on_submit():
        new_password = form.password.data if form.password.data.strip() else None
        is_last_admin = False
        if user_to_edit.role == 'admin':
            with get_db_conn_from_models() as conn_check: # Ensure connection is closed
                with conn_check.cursor(dictionary=True) as cursor_check:
                    cursor_check.execute("SELECT COUNT(*) AS admin_count FROM users WHERE Role = 'admin' AND IsActive = TRUE") # Check active admins
                    admin_count = cursor_check.fetchone()['admin_count']
            if admin_count <= 1:
                is_last_admin = True

        if is_last_admin and form.role.data != 'admin':
            flash('Cannot change the role of the last active admin.', 'danger')
            return render_template('admin/edit_user.html', title='Edit User', form=form, user=user_to_edit)
        if int(current_user.id) == int(user_id) and is_last_admin and form.role.data != 'admin':
             flash('As the last active admin, you cannot change your own role from Admin.', 'danger')
             return render_template('admin/edit_user.html', title='Edit User', form=form, user=user_to_edit)

        success, message = User.update(user_id=user_id, username=form.username.data, role=form.role.data, new_password=new_password)
        if success:
            flash(f'User "{form.username.data}" updated successfully.', 'success')
            return redirect(url_for('list_users'))
        else:
            flash(f'Error updating user: {message}', 'danger')
    elif request.method == 'GET':
        form.username.data = user_to_edit.username
        form.role.data = user_to_edit.role
    return render_template('admin/edit_user.html', title='Edit User', form=form, user=user_to_edit)

@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):

    if current_user.role.lower() != 'admin':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    user_to_delete = User.get(user_id)
    if not user_to_delete:
        flash("User not found.", "danger")
        return redirect(url_for('list_users'))
    if int(current_user.id) == int(user_id):
        flash("You cannot delete your own account.", "warning")
        return redirect(url_for('list_users'))
    success, message = User.delete(user_id_to_delete=user_id, current_admin_id=current_user.id)
    if success:
        flash(f'User (ID: {user_id}, Username: {user_to_delete.username}) deleted successfully.', 'success')
    else:
        flash(f'Error deleting user: {message}', 'danger')
    return redirect(url_for('list_users'))

# ******************************************************************************
# * TEST CASE CREATION (EXISTING)                                              *
# ******************************************************************************


@app.route('/admin/test_cases')
@login_required
@admin_required
def test_cases():

    if current_user.role.lower() != 'admin':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)  

    search_query = request.args.get('search', '').strip()

    if search_query:
        query = """
            SELECT 
                tc.TestCaseID, 
                tc.Code, 
                tc.Name, 
                tc.Module, 
                tc.Description,
                tc.CreatedAt,
                tc.ModifiedAt,
                u_created.Username AS CreatedByUsername,
                u_modified.Username AS ModifiedByUsername
            FROM testcases tc
            LEFT JOIN users u_created ON tc.CreatedBy = u_created.UserID
            LEFT JOIN users u_modified ON tc.ModifiedBy = u_modified.UserID
            WHERE tc.Code LIKE %s OR tc.Name LIKE %s OR tc.Description LIKE %s
            ORDER BY tc.CreatedAt DESC;
        """
        like_pattern = f"%{search_query}%"
        cursor.execute(query, (like_pattern, like_pattern, like_pattern))

    else:
        query = """
            SELECT 
                tc.TestCaseID, 
                tc.Code, 
                tc.Name, 
                tc.Module, 
                tc.Description,
                tc.CreatedAt,
                tc.ModifiedAt,
                u_created.Username AS CreatedByUsername,
                u_modified.Username AS ModifiedByUsername
            FROM testcases tc
            LEFT JOIN users u_created ON tc.CreatedBy = u_created.UserID
            LEFT JOIN users u_modified ON tc.ModifiedBy = u_modified.UserID
            ORDER BY tc.CreatedAt DESC;
        """
        cursor.execute(query)

    test_cases = cursor.fetchall()

    # print(test_cases[0].keys())

    return render_template('admin/test_cases.html', test_cases=test_cases)


@app.route('/admin/delete_test_case/<int:testcase_id>')
@login_required
@admin_required
def delete_test_case(testcase_id):
    if current_user.role.lower() != 'admin':
        flash("Unauthorized access. You have been logged out.", "danger")
        logout_user()
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Delete dependent records first
        cursor.execute("DELETE FROM testexecutions WHERE TestCaseID = %s", (testcase_id,))
        cursor.execute("DELETE FROM steps WHERE TestCaseID = %s", (testcase_id,))
        cursor.execute("DELETE FROM suitetestcases WHERE TestCaseID = %s", (testcase_id,))
        cursor.execute("DELETE FROM testcases WHERE TestCaseID = %s", (testcase_id,))
        cursor.execute("DELETE FROM test_assignments WHERE TestCaseID = %s", (testcase_id,))
        conn.commit()
        flash("Test case and all related data were deleted.", "success")
    except mysql.connector.Error as err:
        conn.rollback()
        flash(f"Failed to delete test case: {err}", "danger")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('test_cases'))



@app.route('/admin/test_cases/edit/<int:testcase_id>', methods=['GET', 'POST']) 
@login_required
@admin_required
def edit_test_case(testcase_id):

    if current_user.role.lower() != 'admin':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    app_type_id = None

    if request.method == 'POST':
        code = request.form['code']
        name = request.form['name']
        module_id = request.form['module']
        description = request.form['description']
        modified_by = current_user.id

        try:
            # Fetch the module name from testsuites using SuiteID
            cursor.execute("SELECT Name FROM testsuites WHERE SuiteID = %s", (module_id,))
            module_row = cursor.fetchone()
            module_name = module_row['Name'] if module_row else None
             
            print(module_name)

            print(modified_by)

            if not module_name:
                flash("Invalid module selected.", "danger")
                return redirect(request.url)

            # Update both Module (name) and Module_id (SuiteID)
            cursor.execute("""
                UPDATE testcases
                SET Code=%s, Name=%s, Module=%s, Module_id=%s, Description=%s, ModifiedBy=%s, ModifiedAt=NOW()
                WHERE TestCaseID=%s
            """, (code, name, module_name, module_id, description, modified_by, testcase_id))
            conn.commit()
            flash("Test case updated successfully.", "success")
            # return redirect(url_for('edit_test_case'))

        except Exception as e:
            conn.rollback()
            flash(f"Error updating test case: {e}", "danger")

    cursor.execute("SELECT * FROM testcases WHERE TestCaseID = %s", (testcase_id,))
    testcase = cursor.fetchone()
    print(testcase)
    print("....................")
    current_apptype = None
    if testcase and testcase.get('Module_id'):
        # Step 1: Get AppType from testsuites
        cursor.execute("SELECT AppType FROM testsuites WHERE SuiteID = %s", (testcase['Module_id'],))
        mod_info = cursor.fetchone()
        if mod_info:
            app_type_id = mod_info['AppType']
            print(app_type_id)
            # Step 2: Get Application name from application table
            cursor.execute("SELECT id, name FROM application WHERE id = %s", (app_type_id,))
            app_info = cursor.fetchone()
            if app_info:
                current_apptype = app_info['id'] 

    cursor.close()
    conn.close()


    if not testcase:
        abort(404)

    return render_template('admin/edit_test_case.html', testcase=testcase, app_type_id=app_type_id, current_apptype=current_apptype)


@app.route('/admin/create-testcase', methods=['GET'])
@login_required
@admin_required
def create_testcase_form():

    if current_user.role.lower() != 'admin':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    testcase_id = request.args.get('testcase_id', type=int)
    testcase_data = None
    app_type_id = None
    module_name = None
    app_name = None
    
    if testcase_id:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM testcases WHERE TestCaseID = %s", (testcase_id,))
        testcase_data = cursor.fetchone()
        module_id = testcase_data['Module_id']
        cursor.execute("SELECT * FROM testsuites WHERE SuiteID = %s", (module_id,))
        testsuites_data = cursor.fetchone()
        app_type_id = testsuites_data['AppType']
        cursor.execute("SELECT * FROM application WHERE id = %s", (app_type_id,))
        appname_data = cursor.fetchone()
        app_name = appname_data['name']
        module_name = testsuites_data['Name']
        print(appname_data['name'])
        cursor.close()
        conn.close()

        if not testcase_data:
            flash("Test case not found.", "danger")
            return redirect(url_for('test_cases'))

    return render_template('admin/create_testcase.html', testcase_data=testcase_data, app_type_id=app_type_id, app_name=app_name, module_name=module_name, title="Create New Test Case")

@app.route('/admin/create-testcase', methods=['POST'])
@login_required
@admin_required
def create_testcase():

    if current_user.role.lower() != 'admin':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))


    payload = request.get_json()
    try:
        payload['created_by_user_id'] = current_user.id
        selected_suite_id = payload.get('selected_suite_id')
        if not selected_suite_id:
            app.logger.error(f"Error in create_testcase: selected_suite_id missing.")
            return jsonify({'success': False, 'error': 'Module (Test Suite) ID is required.'}), 400
        new_id = create_testcase_in_db_with_user(payload)
        app.logger.info(f"Admin {current_user.username} created test case '{payload.get('name')}' with ID {new_id} for Suite ID {selected_suite_id}.")
        return jsonify({'success':True, 'testcase_id': new_id})
    except Exception as e:
        app.logger.error(f"Error in create_testcase: {e}", exc_info=True)
        return jsonify({'success':False, 'error': str(e)}), 500

def create_testcase_in_db_with_user(payload):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # First, get the module information
        module = payload.get('module')
        cursor.execute("SELECT * FROM testsuites WHERE Name = %s", (module,))
        testsuites_data = cursor.fetchone()
        cursor.fetchall()  # Consume any remaining results

        if not testsuites_data:
            raise ValueError(f"No module found with name: {module}")

        module_id = testsuites_data['SuiteID']
        now = datetime.now()

        # Insert the test case
        cursor.execute("""
            INSERT INTO testcases
              (Code, Name, Module, Module_id, Description, CreatedBy, CreatedAt, ModifiedBy, ModifiedAt)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            payload['code'], payload['name'], module, module_id,
            payload.get('description'), payload['created_by_user_id'],
            now, payload['created_by_user_id'], now
        ))
        cursor.fetchall()  # Consume any remaining results

        testcase_id = cursor.lastrowid

        # Insert the steps
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
            cursor.fetchall()  # Consume any remaining results

        # Handle suite test case linking
        selected_suite_id = payload.get('selected_suite_id')
        if testcase_id and selected_suite_id:
            cursor.execute("SELECT COALESCE(MAX(CaseOrder), 0) + 1 AS NextOrder FROM suitetestcases WHERE SuiteID = %s", (selected_suite_id,))
            case_order = cursor.fetchone()['NextOrder']
            cursor.fetchall()  # Consume any remaining results

            cursor.execute("""
                INSERT INTO suitetestcases (SuiteID, TestCaseID, CaseOrder)
                VALUES (%s, %s, %s)
            """, (selected_suite_id, testcase_id, case_order))
            cursor.fetchall()  # Consume any remaining results

        if not DB_CONFIG.get('autocommit', False):
            conn.commit()

        return testcase_id

    except Exception as e:
        if conn and not DB_CONFIG.get('autocommit', False):
            conn.rollback()
        app.logger.error(f"DB error in create_testcase_in_db_with_user: {e}", exc_info=True)
        raise ValueError(f"Error creating test case: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


@app.route('/admin/testcase_steps/<int:testcase_id>')
@login_required
@admin_required

def testcase_steps(testcase_id):

    if current_user.role.lower() != 'admin':
        flash("Unauthorized access. You have been logged out.", "danger")
        logout_user()
        return redirect(url_for("login"))

    conn = get_db_connection() 
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM testcases WHERE TestCaseID = %s", (testcase_id,))
    testcase = cursor.fetchone()
    if not testcase:
       return "Test case not found", 404
       flash("Unauthorized access. You have been logged out.", "danger")

    # Get steps (may return empty list)
    cursor.execute("""
        SELECT StepID, TestCaseID, StepOrder, Input, ExpectedResponse, ParamName, InputType, InpType
        FROM steps
        WHERE TestCaseID = %s
        ORDER BY StepOrder ASC
    """, (testcase_id,))
    steps = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("admin/testcase_steps.html", steps=steps, testcase=testcase)

@app.route('/admin/edit-step/<int:step_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_testcase_step(step_id):

    if current_user.role.lower() != 'admin':
        flash("Unauthorized access. You have been logged out.", "danger")
        logout_user()
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == 'POST':
        input_value = request.form.get('input')
        expected_response = request.form.get('expected_response')
        param_name = request.form.get('param_name') or None
        input_type = request.form.get('input_type')
        inp_type = request.form.get('inp_type')

        update_query = """
            UPDATE steps
            SET Input = %s, ExpectedResponse = %s, ParamName = %s, InputType = %s, InpType = %s
            WHERE StepID = %s
        """
        cursor.execute(update_query, (input_value, expected_response, param_name, input_type, inp_type, step_id))
        conn.commit()
        cursor.close()
        conn.close()

        flash("Step updated successfully", "success")
        return redirect(url_for('edit_testcase_step', step_id=step_id))  # Stay on the same page to show updated data

    # GET: fetch step info
    cursor.execute("SELECT * FROM steps WHERE StepID = %s", (step_id,))
    step = cursor.fetchone()
    cursor.close()
    conn.close()

    if not step:
        flash("Step not found", "danger")
        return redirect(url_for('test_cases'))

    return render_template('admin/edit_testcase_steps.html', step=step)


# ******************************************************************************
# * TEST ASSIGNMENT (MANAGER - EXISTING & NEW ROUTES)                          *
# ******************************************************************************
@app.route('/manager/assign/<int:testcase_id>', methods=['GET', 'POST'])
@login_required
@manager_required
def assign_test_case(testcase_id):

    if current_user.role.lower() != 'manager':
        flash("Unauthorized access. You have been logged out.", "danger")
        logout_user()
        return redirect(url_for("login"))

    test_case = None
    with get_db_conn_from_models() as conn_tc: # Using context manager for connection
        with conn_tc.cursor(dictionary=True) as cursor_tc:
            cursor_tc.execute("SELECT TestCaseID, Code, Name FROM testcases WHERE TestCaseID = %s", (testcase_id,))
            test_case = cursor_tc.fetchone()

    if not test_case:
        flash('Test Case not found.', 'danger')
        return redirect(url_for('manager_dashboard'))

    form = AssignTestCaseForm() # This form is for SINGLE test case assignment
    testers = User.get_testers()
    form.tester_id.choices = [(t['UserID'], t['Username']) for t in testers] if testers else []

    if form.validate_on_submit():
        assigned_to_user_id = form.tester_id.data
        assigned_by_user_id = current_user.id
        notes_from_form = form.notes.data.strip() if form.notes.data else None
        selected_priority = form.priority.data

        try:
            with get_db_conn_from_models() as conn_assign:
                with conn_assign.cursor() as cursor_assign:
                    cursor_assign.execute("""
                        SELECT AssignmentID FROM test_assignments
                        WHERE TestCaseID = %s AND AssignedToUserID = %s AND Status = 'PENDING' AND BatchAssignmentID IS NULL
                    """, (testcase_id, assigned_to_user_id))
                    if cursor_assign.fetchone():
                        flash(f'Test Case "{test_case["Code"]}" is already PENDING assignment (individually) for the selected tester.', 'warning')
                    else:
                        sql_insert = """
                            INSERT INTO test_assignments
                            (TestCaseID, AssignedToUserID, AssignedByUserID, Status, Notes, Priority, BatchAssignmentID)
                            VALUES (%s, %s, %s, 'PENDING', %s, %s, NULL)
                        """
                        cursor_assign.execute(sql_insert, (testcase_id, assigned_to_user_id, assigned_by_user_id, notes_from_form, selected_priority))
                        # conn_assign.commit() # autocommit handles this
                        flash(f'Test Case "{test_case["Code"]}" assigned successfully.', 'success')
                        app.logger.info(f"Manager {current_user.username} assigned TC {testcase_id} to user {assigned_to_user_id}.")
            return redirect(url_for('assign_test_case', testcase_id=testcase_id)) # Or back to the suite/app view
        except mysql.connector.Error as err:
            app.logger.error(f"DB error assigning TC {testcase_id}: {err}", exc_info=True)
            flash(f'Database error assigning test case: {err}', 'danger')
        except Exception as e:
            app.logger.error(f"Unexpected error assigning TC {testcase_id}: {e}", exc_info=True)
            flash(f'An unexpected error occurred: {e}', 'danger')

    return render_template('manager/assign_test_case.html',
                           title=f'Assign Test Case {test_case["Code"]}',
                           form=form, test_case=test_case)

# --- NEW: Assign Suite Route ---
@app.route('/manager/assign_suite/<int:suite_id>', methods=['GET', 'POST'])
@login_required
@manager_required
def assign_suite(suite_id):

    if current_user.role.lower() != 'manager':
        flash("Unauthorized access. You have been logged out.", "danger")
        logout_user()
        return redirect(url_for("login"))

    suite_details = get_suite_by_id_for_dashboard(suite_id)
    if not suite_details:
        flash('Test Suite not found.', 'danger')
        return redirect(url_for('assign_suite'))

    form = AssignSuiteForm() # You'll need to create this form in forms.py
    # Populate tester choices similar to assign_test_case
    testers = User.get_testers()
    form.tester_id.choices = [(t['UserID'], t['Username']) for t in testers] if testers else []

    if form.validate_on_submit():
        assigned_to_user_id = form.tester_id.data
        priority = form.priority.data
        notes = form.notes.data.strip() if form.notes.data else None

        test_cases_in_suite = TestCaseModel.get_test_cases_for_suite(suite_id)
        if not test_cases_in_suite:
            flash(f'No test cases found in suite "{suite_details["Name"]}". Cannot assign.', 'warning')
            return redirect(url_for('assign_suite', suite_id=suite_id))

        try:
            batch_id = BatchTestAssignment.create(
                assigned_to_user_id=assigned_to_user_id,
                assigned_by_user_id=current_user.id,
                assignment_type='SUITE',
                reference_id=suite_id,
                reference_name=suite_details['Name'],
                total_test_cases=len(test_cases_in_suite),
                priority=priority,
                notes=notes
            )
            if not batch_id:
                raise Exception("Failed to create batch assignment record.")

            for tc in test_cases_in_suite:
                ind_assign_id = TestAssignment.create_for_batch(
                    test_case_id=tc['TestCaseID'],
                    assigned_to_user_id=assigned_to_user_id,
                    assigned_by_user_id=current_user.id,
                    priority=priority,
                    notes=notes,
                    batch_assignment_id=batch_id
                )
                if not ind_assign_id:
                     # Log this, decide if to rollback batch or continue
                    app.logger.error(f"Failed to create individual assignment for TC {tc['TestCaseID']} in batch {batch_id}")

            flash(f'Suite "{suite_details["Name"]}" assigned successfully.', 'success')
            app.logger.info(f"Manager {current_user.username} assigned SUITE {suite_id} to user {assigned_to_user_id}.")
        except Exception as e:
            app.logger.error(f"Error assigning suite {suite_id}: {e}", exc_info=True)
            flash(f'Error assigning suite: {e}', 'danger')
        return redirect(url_for('assign_suite', app_id=suite_details['AppType'], suite_id=suite_id))


    return render_template('manager/assign_suite.html', # Create this template
                           title=f'Assign Suite: {suite_details["Name"]}',
                           form=form,
                           suite=suite_details)


# --- NEW: Assign Application Route ---
@app.route('/manager/assign_application/<int:app_id>', methods=['GET', 'POST'])
@login_required
@manager_required
# @manager_or_admin_required
def assign_application(app_id):

    if current_user.role.lower() != 'manager':
        flash("Unauthorized access. You have been logged out.", "danger")
        logout_user()
        return redirect(url_for("login"))

    app_details = get_application_by_id_for_dashboard(app_id)
    if not app_details:
        flash('Application not found.', 'danger')
        return redirect(url_for('manager_dashboard'))

    form = AssignApplicationForm() # You'll need to create this form in forms.py
    testers = User.get_testers()
    form.tester_id.choices = [(t['UserID'], t['Username']) for t in testers] if testers else []

    if form.validate_on_submit():
        assigned_to_user_id = form.tester_id.data
        priority = form.priority.data
        notes = form.notes.data.strip() if form.notes.data else None

        test_cases_in_app = TestCaseModel.get_test_cases_for_application(app_id) # Get all unique TCs
        if not test_cases_in_app:
            flash(f'No test cases found for application "{app_details["name"]}". Cannot assign.', 'warning')
            return redirect(url_for('manager_dashboard'))

        try:
            batch_id = BatchTestAssignment.create(
                assigned_to_user_id=assigned_to_user_id,
                assigned_by_user_id=current_user.id,
                assignment_type='APPLICATION',
                reference_id=app_id,
                reference_name=app_details['name'],
                total_test_cases=len(test_cases_in_app),
                priority=priority,
                notes=notes
            )
            if not batch_id:
                raise Exception("Failed to create batch assignment record for application.")

            for tc in test_cases_in_app:
                TestAssignment.create_for_batch(
                    test_case_id=tc['TestCaseID'],
                    assigned_to_user_id=assigned_to_user_id,
                    assigned_by_user_id=current_user.id,
                    priority=priority,
                    notes=notes,
                    batch_assignment_id=batch_id
                )
            flash(f'All test cases for application "{app_details["name"]}" assigned successfully.', 'success')
            app.logger.info(f"Manager {current_user.username} assigned APPLICATION {app_id} to user {assigned_to_user_id}.")
        except Exception as e:
            app.logger.error(f"Error assigning application {app_id}: {e}", exc_info=True)
            flash(f'Error assigning application: {e}', 'danger')
        return redirect(url_for('assign_application', app_id=app_id))

    return render_template('manager/assign_application.html', # Create this template
                           title=f'Assign All Tests for Application: {app_details["name"]}',
                           form=form,
                           application=app_details)

# --- NEW: Custom Group Routes (Skeleton) ---
@app.route('/manager/custom_groups', methods=['GET'])
@login_required
@manager_required
def list_custom_groups():

    if current_user.role.lower() != 'manager':
        flash("Unauthorized access. You have been logged out.", "danger")
        logout_user()
        return redirect(url_for("login"))

    groups = CustomTestGroup.get_all_groups_by_user(current_user.id) # Or all groups if admin
    return render_template('manager/list_custom_groups.html', title="Manage Custom Test Groups", groups=groups)

@app.route('/manager/custom_groups/create', methods=['GET', 'POST'])
@login_required
@manager_required
def create_custom_group():

    if current_user.role.lower() != 'manager':
        flash("Unauthorized access. You have been logged out.", "danger")
        logout_user()
        return redirect(url_for("login"))

    form = CreateEditCustomGroupForm()
    
    applications_with_suites, uncategorized_tcs = get_test_cases_for_custom_group_selection()
    all_tc_choices = []
    # ... (code to populate all_tc_choices as before) ...
    for app_data in applications_with_suites:
        for suite_data in app_data['suites']:
            for tc in suite_data['test_cases']:
                all_tc_choices.append((tc['TestCaseID'], f"{app_data['name']} > {suite_data['name']} > {tc['Code']} - {tc['Name']}"))
    for tc in uncategorized_tcs:
        all_tc_choices.append((tc['TestCaseID'], f"Uncategorized > {tc['Code']} - {tc['Name']}"))
    form.test_cases.choices = all_tc_choices

    # Ensure form.test_cases.data is an empty list if it's None (for initial GET)
    if form.test_cases.data is None:
        form.test_cases.data = []


    if form.validate_on_submit():
        group_name = form.name.data
        description = form.description.data
        selected_tc_ids = form.test_cases.data # Should be a list of integers

        if not selected_tc_ids:
            flash('Please select at least one test case for the group.', 'warning')
            # Ensure data is iterable for re-render
            if form.test_cases.data is None: form.test_cases.data = []
            return render_template('manager/create_edit_custom_group.html',
                                   title="Create Custom Test Group",
                                   form=form, mode='create',
                                   applications_with_suites=applications_with_suites,
                                   uncategorized_tcs=uncategorized_tcs)

        group_id = CustomTestGroup.create(name=group_name, created_by_user_id=current_user.id, description=description)
        if group_id:
            for tc_id in selected_tc_ids:
                CustomTestGroup.add_item(group_id, tc_id)
            flash(f'Custom group "{group_name}" created successfully.', 'success')
            return redirect(url_for('list_custom_groups'))
        else:
            flash('Error creating custom group. Please try again.', 'danger')
            # Ensure data is iterable for re-render
            if form.test_cases.data is None: form.test_cases.data = []
    
    # For GET request or if validation fails on POST
    # Ensure form.test_cases.data is iterable before rendering
    if form.test_cases.data is None:
        form.test_cases.data = []
        
    return render_template('manager/create_edit_custom_group.html',
                           title="Create Custom Test Group",
                           form=form, mode='create',
                           applications_with_suites=applications_with_suites,
                           uncategorized_tcs=uncategorized_tcs)


@app.route('/manager/custom_groups/edit/<int:group_id>', methods=['GET', 'POST'])
@login_required
@manager_required
def edit_custom_group(group_id):

    if current_user.role.lower() != 'manager':
        flash("Unauthorized access. You have been logged out.", "danger")
        logout_user()
        return redirect(url_for("login"))

    group = CustomTestGroup.get(group_id)
    if not group:
        flash('Custom group not found.', 'danger')
        return redirect(url_for('list_custom_groups'))
    
    if group.CreatedByUserID != current_user.id and current_user.role != 'admin':
         flash('You are not authorized to edit this custom group.', 'danger')
         return redirect(url_for('list_custom_groups'))

    form = CreateEditCustomGroupForm(obj=group) # Pre-populate name and description
    
    applications_with_suites, uncategorized_tcs = get_test_cases_for_custom_group_selection()
    all_tc_choices = []
    # ... (code to populate all_tc_choices as before) ...
    for app_data in applications_with_suites:
        for suite_data in app_data['suites']:
            for tc in suite_data['test_cases']:
                all_tc_choices.append((tc['TestCaseID'], f"{app_data['name']} > {suite_data['name']} > {tc['Code']} - {tc['Name']}"))
    for tc in uncategorized_tcs:
        all_tc_choices.append((tc['TestCaseID'], f"Uncategorized > {tc['Code']} - {tc['Name']}"))
    form.test_cases.choices = all_tc_choices
    
    # Ensure form.test_cases.data is an empty list if it's None
    # This is important for the initial GET of edit page if no data was pre-selected by obj=group for this field
    if form.test_cases.data is None:
        form.test_cases.data = []

    if request.method == 'GET':
        current_items = CustomTestGroup.get_items(group_id)
        form.test_cases.data = [item['TestCaseID'] for item in current_items] # Pre-select
    # Note: If validation fails on POST, WTForms should automatically keep form.test_cases.data
    # populated with the submitted (and potentially invalid if another field failed) data.
    # The check 'if form.test_cases.data is None:' above should handle the initial GET scenario.

    if form.validate_on_submit():
        if CustomTestGroup.update(group_id, form.name.data, form.description.data):
            current_db_item_ids = {item['TestCaseID'] for item in CustomTestGroup.get_items(group_id)}
            new_selected_tc_ids = set(form.test_cases.data if form.test_cases.data is not None else []) # Ensure it's iterable

            ids_to_add = new_selected_tc_ids - current_db_item_ids
            ids_to_remove = current_db_item_ids - new_selected_tc_ids

            for tc_id in ids_to_add:
                CustomTestGroup.add_item(group_id, tc_id)
            for tc_id in ids_to_remove:
                CustomTestGroup.remove_item(group_id, tc_id)
            
            flash(f'Custom group "{form.name.data}" updated successfully.', 'success')
            return redirect(url_for('list_custom_groups'))
        else:
            flash('Error updating custom group. Please try again.', 'danger')
            # Ensure data is iterable for re-render if update itself fails but validation passed
            if form.test_cases.data is None: form.test_cases.data = []
            
    # For GET request or if validation fails on POST
    # Ensure form.test_cases.data is iterable before rendering
    if form.test_cases.data is None:
        form.test_cases.data = []

    return render_template('manager/create_edit_custom_group.html',
                           title="Edit Custom Test Group",
                           form=form, mode='edit', group=group,
                           applications_with_suites=applications_with_suites,
                           uncategorized_tcs=uncategorized_tcs)

@app.route('/manager/custom_groups/delete/<int:group_id>', methods=['POST'])
@login_required
@manager_required
def delete_custom_group(group_id):

    if current_user.role.lower() != 'manager':
        flash("Unauthorized access. You have been logged out.", "danger")
        logout_user()
        return redirect(url_for("login"))

    group = CustomTestGroup.get(group_id)
    if not group or group.CreatedByUserID != current_user.id: # Basic ownership check
        flash('Custom group not found or you are not authorized to delete it.', 'danger')
        return redirect(url_for('list_custom_groups'))
    
    success, message = CustomTestGroup.delete(group_id)
    if success:
        flash(message, 'success')
    else:
        flash(message, 'danger')
    return redirect(url_for('list_custom_groups'))

@app.route('/manager/assign_custom_group/<int:group_id>', methods=['GET', 'POST'])
@login_required
@manager_required
def assign_custom_group(group_id):

    if current_user.role.lower() != 'manager':
        flash("Unauthorized access. You have been logged out.", "danger")
        logout_user()
        return redirect(url_for("login"))

    custom_group = CustomTestGroup.get(group_id)
    if not custom_group:
        flash('Custom Test Group not found.', 'danger')
        return redirect(url_for('list_custom_groups'))

    form = AssignCustomGroupForm() # You'll need this form
    testers = User.get_testers()
    form.tester_id.choices = [(t['UserID'], t['Username']) for t in testers] if testers else []

    if form.validate_on_submit():
        assigned_to_user_id = form.tester_id.data
        priority = form.priority.data
        notes = form.notes.data.strip() if form.notes.data else None

        test_cases_in_group = CustomTestGroup.get_items(group_id) # Returns list of dicts with TestCaseID
        if not test_cases_in_group:
            flash(f'No test cases found in custom group "{custom_group.Name}". Cannot assign.', 'warning')
            return redirect(url_for('list_custom_groups'))

        try:
            batch_id = BatchTestAssignment.create(
                assigned_to_user_id=assigned_to_user_id,
                assigned_by_user_id=current_user.id,
                assignment_type='CUSTOM_GROUP',
                reference_id=group_id,
                reference_name=custom_group.Name,
                total_test_cases=len(test_cases_in_group),
                priority=priority,
                notes=notes
            )
            if not batch_id:
                raise Exception("Failed to create batch assignment record for custom group.")

            for tc_item in test_cases_in_group:
                TestAssignment.create_for_batch(
                    test_case_id=tc_item['TestCaseID'],
                    assigned_to_user_id=assigned_to_user_id,
                    assigned_by_user_id=current_user.id,
                    priority=priority,
                    notes=notes,
                    batch_assignment_id=batch_id
                )
            flash(f'Custom group "{custom_group.Name}" assigned successfully.', 'success')
            app.logger.info(f"Manager {current_user.username} assigned CUSTOM_GROUP {group_id} to user {assigned_to_user_id}.")
        except Exception as e:
            app.logger.error(f"Error assigning custom group {group_id}: {e}", exc_info=True)
            flash(f'Error assigning custom group: {e}', 'danger')
        return redirect(url_for('list_custom_groups'))

    return render_template('manager/assign_custom_group.html', # Create this template
                           title=f'Assign Custom Group: {custom_group.Name}',
                           form=form,
                           custom_group=custom_group)

def generate_test_report_pdf(batch_assignment_id=None, start_date=None, end_date=None, test_case_id=None, application_id=None):
    """Generate a PDF report for test results based on filters."""
    try:
        # Create PDF document
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        styles = getSampleStyleSheet()
        elements = []

        # Define custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=28,
            spaceAfter=30,
            textColor=colors.HexColor('#2c3e50'),
            alignment=1  # Center alignment
        )

        subtitle_style = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Heading2'],
            fontSize=16,
            spaceAfter=20,
            textColor=colors.HexColor('#34495e'),
            alignment=1
        )

        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            spaceAfter=12,
            textColor=colors.HexColor('#2c3e50')
        )

        # Add header with logo and title
        elements.append(Paragraph("Test Execution Report", title_style))
        elements.append(Spacer(1, 20))

        # Get test results data
        with get_db_conn_from_models() as conn:
            with conn.cursor(dictionary=True) as cursor:
                # Base query - Fixed joins and application filtering
                query = """
                    SELECT 
                        te.ExecutionID,
                        te.ExecutionTime,
                        te.OverallStatus,
                        tc.Code AS TestCaseCode,
                        tc.Name AS TestCaseName,
                        u.Username AS ExecutedBy,
                        bta.ReferenceName AS BatchName,
                        bta.Status AS BatchStatus,
                        app.name AS ApplicationName
                    FROM testexecutions te
                    JOIN testcases tc ON te.TestCaseID = tc.TestCaseID
                    JOIN users u ON te.ExecutedBy = u.UserID
                    LEFT JOIN test_assignments ta ON te.ExecutionID = ta.ExecutionID
                    LEFT JOIN batch_test_assignments bta ON ta.BatchAssignmentID = bta.BatchAssignmentID
                    JOIN suitetestcases stc ON tc.TestCaseID = stc.TestCaseID
                    JOIN testsuites ts ON stc.SuiteID = ts.SuiteID
                    JOIN application app ON ts.AppType = app.id
                    WHERE 1=1
                """
                params = []

                # Add filters
                if batch_assignment_id:
                    query += " AND bta.BatchAssignmentID = %s"
                    params.append(batch_assignment_id)
                
                # Enhanced date filtering
                if start_date:
                    try:
                        start_datetime = f"{start_date} 00:00:00"
                        query += " AND te.ExecutionTime >= %s"
                        params.append(start_datetime)
                    except Exception as e:
                        flash(f'Invalid start date format: {start_date}', 'danger')
                        app.logger.error(f"Invalid start date format: {start_date}")
                
                if end_date:
                    try:
                        end_datetime = f"{end_date} 23:59:59"
                        query += " AND te.ExecutionTime <= %s"
                        params.append(end_datetime)
                    except Exception as e:
                        flash(f'Invalid start date format: {start_date}', 'danger')
                        app.logger.error(f"Invalid end date format: {end_date}")

                if test_case_id:
                    query += " AND tc.TestCaseID = %s"
                    params.append(test_case_id)
                if application_id:
                    query += " AND app.id = %s"
                    params.append(application_id)

                query += " ORDER BY te.ExecutionTime DESC"
                cursor.execute(query, tuple(params))
                results = cursor.fetchall()

                if not results:
                    elements.append(Paragraph("No test results found for the selected criteria.", normal_style))
                    doc.build(elements)
                    return buffer.getvalue()

                # Add report type and filter information
                filter_info = []
                if application_id:
                    cursor.execute("SELECT name FROM application WHERE id = %s", (application_id,))
                    app_name = cursor.fetchone()['name']
                    filter_info.append(f"Application: {app_name}")
                if start_date:
                    filter_info.append(f"Start Date: {start_date}")
                if end_date:
                    filter_info.append(f"End Date: {end_date}")
                
                if filter_info:
                    elements.append(Paragraph("Report Filters:", subtitle_style))
                    for info in filter_info:
                        elements.append(Paragraph(info, normal_style))
                    elements.append(Spacer(1, 20))

                # Add summary statistics
                total_tests = len(results)
                passed_tests = sum(1 for r in results if r['OverallStatus'] == 'PASS')
                failed_tests = total_tests - passed_tests
                pass_rate = (passed_tests / total_tests * 100) if total_tests > 0 else 0

                # Create summary table with enhanced styling
                summary_data = [
                    ['Total Tests', str(total_tests)],
                    ['Passed Tests', str(passed_tests)],
                    ['Failed Tests', str(failed_tests)],
                    ['Pass Rate', f"{pass_rate:.1f}%"]
                ]
                summary_table = Table(summary_data, colWidths=[2*inch, 2*inch])
                summary_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8f9fa')),
                    ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#2c3e50')),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 12),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
                    ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dee2e6')),
                    ('ROUNDEDCORNERS', [10, 10, 10, 10]),
                    ('BOX', (0, 0), (-1, -1), 2, colors.HexColor('#2c3e50')),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ]))
                elements.append(summary_table)
                elements.append(Spacer(1, 30))

                # Create results table with enhanced styling
                table_data = [['Test Case', 'Status', 'Executed By', 'Execution Time', 'Batch', 'Application']]
                for result in results:
                    status_color = colors.HexColor('#28a745') if result['OverallStatus'] == 'PASS' else colors.HexColor('#dc3545')
                    table_data.append([
                        f"{result['TestCaseCode']} - {result['TestCaseName']}",
                        result['OverallStatus'],
                        result['ExecutedBy'],
                        result['ExecutionTime'].strftime('%Y-%m-%d %H:%M:%S'),
                        result['BatchName'] or 'N/A',
                        result['ApplicationName'] or 'N/A'
                    ])

                results_table = Table(table_data, colWidths=[2*inch, 1*inch, 1.5*inch, 1.5*inch, 1.5*inch, 1.5*inch])
                
                # Enhanced table styling
                table_style = [
                    # Header styling
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 12),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                    ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dee2e6')),
                    ('BOX', (0, 0), (-1, -1), 2, colors.HexColor('#2c3e50')),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ]

                # Add alternating row colors and status-based styling
                for i in range(1, len(table_data)):
                    if i % 2 == 0:
                        table_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#f8f9fa')))
                    
                    # Color the status cell based on PASS/FAIL
                    status = table_data[i][1]
                    status_color = colors.HexColor('#28a745') if status == 'PASS' else colors.HexColor('#dc3545')
                    table_style.append(('TEXTCOLOR', (1, i), (1, i), status_color))
                    table_style.append(('FONTNAME', (1, i), (1, i), 'Helvetica-Bold'))

                results_table.setStyle(TableStyle(table_style))
                elements.append(results_table)

                # Add footer with generation timestamp
                elements.append(Spacer(1, 30))
                footer_style = ParagraphStyle(
                    'Footer',
                    parent=styles['Normal'],
                    fontSize=8,
                    textColor=colors.HexColor('#6c757d'),
                    alignment=1
                )
                elements.append(Paragraph(f"Report generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", footer_style))

        # Build PDF
        doc.build(elements)
        return buffer.getvalue()

    except Exception as e:
        print("error.........")
        print(e)
        flash(f'Error generating PDF report: {e}', 'danger')
        app.logger.error(f"Error generating PDF report: {e}", exc_info=True)
        raise

def send_test_report_email(recipient_emails, pdf_data, subject="Test Execution Report"):
    """Send test report via email to multiple recipients."""
    try:
        # Email configuration
        smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.environ.get('SMTP_PORT', '587'))
        smtp_username = os.environ.get('SMTP_USERNAME', 'mannathy5@gmail.com')  # Changed to use SMTP_USERNAME
        smtp_password = os.environ.get('SMTP_PASSWORD', 'eznocknlsbfejlsa')    # Changed to use SMTP_PASSWORD
        sender_email = os.environ.get('SENDER_EMAIL', smtp_username)

        if not all([smtp_username, smtp_password]):
            raise ValueError("SMTP credentials not configured")

        # Create message
        msg = MIMEMultipart()
        msg['From'] = formataddr(("BOA", sender_email)) 
        msg['To'] = ', '.join(recipient_emails)  # Join multiple recipients with commas
        msg['Subject'] = subject
        msg['Date'] = formatdate(localtime=True)

        # Add body
        body = "Please find attached the test execution report."
        msg.attach(MIMEText(body, 'plain'))

        # Attach PDF
        pdf_attachment = MIMEApplication(pdf_data, _subtype='pdf')
        pdf_attachment.add_header('Content-Disposition', 'attachment', 
                                filename=f'test_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf')
        msg.attach(pdf_attachment)

        # Send email
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)

        return True

    except Exception as e:
        flash(f'Error sending email', 'danger')
        app.logger.error(f"Error sending email: {e}", exc_info=True)
        raise

@app.route('/manager/generate-report', methods=['GET', 'POST'])
@login_required
@manager_or_admin_required
def generate_report():
    
    if current_user.role.lower() != 'manager':
        flash("Unauthorized access. You have been logged out.", "danger")
        logout_user()
        return redirect(url_for("login"))

    if request.method == 'POST':
        try:
            # Get form data
            report_type = request.form.get('report_type')
            batch_id = request.form.get('batch_id')
            start_date = request.form.get('start_date')
            end_date = request.form.get('end_date')
            test_case_id = request.form.get('test_case_id')
            application_id = request.form.get('application_id')
            send_email = request.form.get('send_email') == 'true'
            
            # Get multiple email addresses
            email_addresses = request.form.getlist('email_addresses[]')
            email_addresses = [email.strip() for email in email_addresses if email.strip()]

            # Generate PDF report
            pdf_data = generate_test_report_pdf(
                batch_assignment_id=batch_id if batch_id else None,
                start_date=start_date if start_date else None,
                end_date=end_date if end_date else None,
                test_case_id=test_case_id if test_case_id else None,
                application_id=application_id if application_id else None
            )

            # Send email if requested
            if send_email and email_addresses:
                try:
                    send_test_report_email(email_addresses, pdf_data)
                    flash(f'Report has been sent to {len(email_addresses)} recipient(s).', 'success')
                    return redirect(url_for("generate_report"))
                except Exception as e:
                    app.logger.error(f"Error sending email: {e}", exc_info=True)
                    flash('Error sending email. The report will be downloaded instead.', 'warning')
                    return send_file(
                        BytesIO(pdf_data),
                        mimetype='application/pdf',
                        as_attachment=True,
                        download_name=f'test_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'
                    )
            else:
                flash('Report generated successfully.', 'success')
                # return redirect(url_for("generate_report"))

            # Return PDF data for download
            return send_file(
                BytesIO(pdf_data),
                mimetype='application/pdf',
                as_attachment=True,
                download_name=f'test_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'
            )

        except Exception as e:
            print("..............")
            print(e)
            app.logger.error(f"Error generating report: {e}", exc_info=True)
            flash('Error generating report. Please try again.', 'error')
            return redirect(url_for('generate_report'))

    # GET request - show form
    try:
        with get_db_conn_from_models() as conn:
            with conn.cursor(dictionary=True) as cursor:
                # Get active batches
                cursor.execute("""
                    SELECT BatchAssignmentID, ReferenceName, Status
                    FROM batch_test_assignments
                    WHERE Status LIKE 'COMPLETED_%' OR Status = 'CANCELLED'
                    ORDER BY AssignmentDate DESC
                """)
                batches = cursor.fetchall()

                # Get test cases
                cursor.execute("""
                    SELECT TestCaseID, Code, Name
                    FROM testcases
                    ORDER BY Code
                """)
                test_cases = cursor.fetchall()

                # Get applications
                cursor.execute("""
                    SELECT id, name
                    FROM application
                    ORDER BY name
                """)
                applications = cursor.fetchall()

        return render_template(
            'manager/generate_report.html',
            batches=batches,
            test_cases=test_cases,
            applications=applications,
            report_types=[
                {'id': 'application', 'name': 'Application Report'},
                {'id': 'test_case', 'name': 'Test Case Report'},
                {'id': 'batch', 'name': 'Batch Report'}
            ]
        )

    except Exception as e:
        app.logger.error(f"Error loading report generation page: {e}", exc_info=True)
        flash('Error loading page. Please try again.', 'error')
        return redirect(url_for('index'))


# ******************************************************************************
# * TESTER ROUTES (EXISTING & NEW)                                             *
# ******************************************************************************
@app.route('/tester/run_assigned/<int:assignment_id>') # For individual assignments
@login_required
@tester_required
def run_assigned_test_page(assignment_id):

    if current_user.role.lower() != 'tester':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    assignment = None
    # Ensure this assignment_id is for an INDIVIDUAL assignment not part of a batch directly run this way,
    # OR that it's an individual assignment that happens to be part of a batch currently being stepped through.
    with get_db_conn_from_models()as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("""
                SELECT ta.AssignmentID, ta.TestCaseID, ta.BatchAssignmentID,
                       tc.Code, tc.Name, tc.Module, ta.Status, tc.Description
                FROM test_assignments ta
                JOIN testcases tc ON ta.TestCaseID = tc.TestCaseID
                WHERE ta.AssignmentID = %s AND ta.AssignedToUserID = %s
            """, (assignment_id, current_user.id))
            assignment = cursor.fetchone()

    if not assignment:
        flash('Assignment not found or you are not authorized for it.', 'danger')
        return redirect(url_for('tester_dashboard'))

    if assignment['Status'] not in ['PENDING', 'IN_PROGRESS']:
        flash(f'This test assignment (Status: {assignment["Status"]}) cannot be run at this time.', 'warning')
        return redirect(url_for('tester_dashboard'))

    try:
        device_id = get_connected_device()
        android_ver = get_android_version(device_id)
    except Exception as e:
        app.logger.warning(f"Error getting device info for run_assigned_test_page: {e}")
        device_id, android_ver = '', ''

    params_for_tc = get_testcase_dynamic_params_from_db(assignment['TestCaseID'])

    return render_template('tester/run_assigned_test.html',
                           assignment=assignment, # This now includes BatchAssignmentID
                           device_id=device_id,
                           android_version=android_ver,
                           test_case_id=assignment['TestCaseID'],
                           params_for_tc=params_for_tc,
                           title=f"Run Assigned Test: {assignment['Code']}")

@app.route('/tester/run_batch_assignment/<int:batch_assignment_id>')
@login_required
@tester_required
def run_batch_assignment_page(batch_assignment_id):

    if current_user.role.lower() != 'tester':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    batch_assignment = BatchTestAssignment.get(batch_assignment_id)
    
    # Initialize device_id and android_ver before try block
    # Use different variable names internally if 'device_id' might be a route parameter elsewhere
    # For clarity, I'll use device_id_val and android_ver_val
    device_id_val = ''
    android_ver_val = ''

    if not batch_assignment or batch_assignment.AssignedToUserID != current_user.id:
        flash('Batch assignment not found or you are not authorized for it.', 'danger')
        return redirect(url_for('tester_dashboard'))

    dynamic_params_info = TestAssignment.get_dynamic_params_for_batch(batch_assignment_id)
    app.logger.info(f"Dynamic params for batch {batch_assignment_id} for template: {dynamic_params_info}") # Corrected log

    # Fetch individual test cases within this batch
    individual_tests_in_batch = []
    # Using get_db_conn_from_models for consistency
    with get_db_conn_from_models() as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("""
                SELECT ta.AssignmentID, tc.Code, tc.Name, ta.Status, ta.Priority, tc.TestCaseID
                FROM test_assignments ta
                JOIN testcases tc ON ta.TestCaseID = tc.TestCaseID
                WHERE ta.BatchAssignmentID = %s AND ta.AssignedToUserID = %s
                ORDER BY ta.AssignmentID ASC
            """, (batch_assignment_id, current_user.id))
            individual_tests_in_batch = cursor.fetchall()

    # CORRECTLY FETCH AND DEFINE device_id and android_ver HERE
    try:
        device_id_val = get_connected_device() # from android_helper
        if device_id_val: # Only get version if device_id was found
             android_ver_val = get_android_version(device_id_val) # from android_helper
        else:
            app.logger.warning(f"No connected device found for batch run page {batch_assignment_id}.")
            # device_id_val and android_ver_val remain ''
    except NameError as ne: 
        app.logger.error(f"android_helper function not found or not imported: {ne}. Device info for batch page will be empty.", exc_info=True)
        # device_id_val and android_ver_val remain ''
    except Exception as e: 
        app.logger.warning(f"Could not auto-detect device info for batch run page {batch_assignment_id}: {e}", exc_info=True)
        # device_id_val and android_ver_val remain ''

    return render_template('tester/run_batch_assignment.html',
                           title=f"Execute Batch: {batch_assignment.ReferenceName}", # Ensure batch_assignment is not None
                           batch_assignment=batch_assignment,
                           individual_tests=individual_tests_in_batch,
                           dynamic_params_info=dynamic_params_info,
                           device_id=device_id_val,         # Pass the defined variable
                           android_version=android_ver_val) # Pass the defined variable


@app.route('/tester/get_batch_progress/<int:batch_assignment_id>') # Endpoint name is 'get_batch_progress'
@login_required
@tester_required
def get_batch_progress(batch_assignment_id):
    # ... (Implementation from previous response) ...

    if current_user.role.lower() != 'tester':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    global batch_test_processes, batch_state_lock
    batch_db_assignment = BatchTestAssignment.get(batch_assignment_id) # Get fresh status from DB
    if not batch_db_assignment or batch_db_assignment.AssignedToUserID != current_user.id:
        return jsonify({'status': 'error', 'message': 'Batch not found or unauthorized.'}), 403

    is_running_from_state = False
    live_log_output = "Awaiting batch runner output..."
    output_file_path = None # Corrected from output_file

    with batch_state_lock:
        batch_proc_info_state = batch_test_processes.get(batch_assignment_id)
        if batch_proc_info_state:
            process_obj = batch_proc_info_state.get('process')
            output_file_path = batch_proc_info_state.get('output_file') # Use the correct variable
            if process_obj and process_obj.poll() is None:
                is_running_from_state = True
    
    if output_file_path and os.path.exists(output_file_path):
        try:
            with open(output_file_path, 'r', encoding='utf-8') as f:
                live_log_output = f.read()
        except Exception as e:
            live_log_output = f"Error reading batch output: {e}"
    elif is_running_from_state:
        live_log_output = "Batch runner active, log file pending..."
    
    individual_statuses = {}
    with get_db_conn_from_models() as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT AssignmentID, Status FROM test_assignments WHERE BatchAssignmentID = %s", (batch_assignment_id,))
            for row in cursor.fetchall(): # Corrected from r to row
                individual_statuses[row['AssignmentID']] = row['Status']
            
    return jsonify({
        'batch_assignment_id': batch_assignment_id, # Corrected variable name
        'is_running': is_running_from_state and batch_db_assignment.Status == 'IN_PROGRESS',
        'overall_status': batch_db_assignment.Status,
        'total_test_cases': batch_db_assignment.TotalTestCases,
        'completed_test_cases': batch_db_assignment.CompletedTestCases,
        'passed_test_cases': batch_db_assignment.PassedTestCases,
        'live_output': live_log_output,
        'individual_tc_statuses': individual_statuses
        # 'report_path_summary': None # batch_runner.py needs to communicate this if it creates one
    })

# --- NEW: Endpoint to start/resume a batch run ---
@app.route('/tester/start_batch_run', methods=['POST'])
@login_required
@tester_required
def start_batch_run():

    if current_user.role.lower() != 'tester':
            flash("Unauthorized access. You have been logged out.", "danger")
            logout_user()
            return redirect(url_for("login"))

    data = request.get_json()
    batch_assignment_id = data.get('batch_assignment_id')

    if not batch_assignment_id:
        return jsonify({'status': 'error', 'message': 'Batch Assignment ID is missing.'}), 400

    batch_assignment = BatchTestAssignment.get(batch_assignment_id)
    if not batch_assignment or batch_assignment.AssignedToUserID != current_user.id:
        return jsonify({'status': 'error', 'message': 'Batch not found or unauthorized.'}), 403

    if batch_assignment.Status == 'PENDING':
        BatchTestAssignment.update_status(batch_assignment_id, 'IN_PROGRESS')
        app.logger.info(f"Tester {current_user.username} started batch {batch_assignment_id}.")


    next_individual_assignment = TestAssignment.get_pending_in_batch(batch_assignment_id)

    if next_individual_assignment:
        app.logger.info(f"Next test in batch {batch_assignment_id} is AssignmentID {next_individual_assignment['AssignmentID']} (TC: {next_individual_assignment['TestCaseCode']}).")
        # The client-side JS will use this to redirect
        return jsonify({
            'status': 'next_test',
            'next_assignment_id': next_individual_assignment['AssignmentID'],
            'next_test_page_url': url_for('run_assigned_test_page', assignment_id=next_individual_assignment['AssignmentID'])
        })
    else:
        # All tests in batch are done (or none were pending)
        # Determine final batch status based on individual test outcomes
        # This is a simplified check; more robust would be to count pass/fail from test_assignments
        # For now, assume if no pending, it's completed.
        # The actual pass/fail counting should happen after each generic_runner finishes.
        completed_status = 'COMPLETED_PASS' # Placeholder, logic needed to determine this
        
        # Example logic to determine final batch status (can be refined)
        with get_db_conn_from_models() as conn:
            with conn.cursor(dictionary=True) as cursor:
                cursor.execute("""
                    SELECT COUNT(*) as total,
                           SUM(CASE WHEN Status LIKE 'EXECUTED_FAIL%%' THEN 1 ELSE 0 END) as failed_count
                    FROM test_assignments
                    WHERE BatchAssignmentID = %s AND Status LIKE 'EXECUTED%%'
                """, (batch_assignment_id,))
                counts = cursor.fetchone()
                if counts and counts['total'] > 0:
                    if counts['failed_count'] > 0:
                        completed_status = 'COMPLETED_FAIL'
                    else:
                        completed_status = 'COMPLETED_PASS'
                elif batch_assignment.Status == 'IN_PROGRESS': # No tests executed yet but batch was started
                    completed_status = 'IN_PROGRESS' # Or could be an error state
                else: # No tests, or not started
                     completed_status = batch_assignment.Status # Keep current if not changed

        BatchTestAssignment.update_status(batch_assignment_id, completed_status)
        app.logger.info(f"Batch {batch_assignment_id} completed or no more pending tests. Final status: {completed_status}.")
        return jsonify({'status': 'batch_complete', 'message': f'All tests in batch {batch_assignment.ReferenceName} are processed.', 'final_status': completed_status})

# In run_test.py (your main app file)

# ... (other imports and Flask app setup) ...
# ... (global batch_test_processes, batch_state_lock definitions) ...
# ... (other routes) ...


# --- Endpoint to LAUNCH a Batch Execution ---
@app.route('/tester/execute_batch', methods=['POST'])
@login_required
@tester_required
def execute_batch():

    if current_user.role.lower() != 'tester':
        flash("Unauthorized access. You have been logged out.", "danger")
        logout_user()
        return redirect(url_for("login"))

    global batch_test_processes, batch_state_lock # Ensure access to global state

    data = request.get_json()
    app.logger.debug(f"Received data for /execute_batch: {data}") # Log received data

    batch_assignment_id_str = data.get('batch_assignment_id')
    device_id_arg = data.get('device_id', '').strip()
    android_ver_arg = data.get('android_version', '').strip()
    password_arg = data.get('password', '').strip()
    all_dynamic_inputs_arg = data.get('dynamic_inputs', {})

    if not batch_assignment_id_str: # Check if the string itself is missing or empty
        app.logger.error("Missing batch_assignment_id in execute_batch request.")
        return jsonify({'status': 'error', 'message': 'Batch Assignment ID is missing.'}), 400
    
    try:
        batch_assignment_id = int(batch_assignment_id_str)
    except ValueError:
        flash(f"Invalid batch_assignment_id format: {batch_assignment_id_str}", "danger")
        app.logger.error(f"Invalid batch_assignment_id format: {batch_assignment_id_str}")
        return jsonify({'status': 'error', 'message': 'Invalid Batch Assignment ID format.'}), 400

    if not all([device_id_arg, android_ver_arg]):
        flash(f"Missing device_id or android_version for batch {batch_assignment_id}", "danger")
        app.logger.error(f"Missing device_id or android_version for batch {batch_assignment_id}.")
        return jsonify({'status': 'error', 'message': 'Device ID and Android version are required.'}), 400

    batch_assignment = BatchTestAssignment.get(batch_assignment_id)
    if not batch_assignment:
        flash(f"BatchAssignmentID {batch_assignment_id} not found in DB.", "danger")
        app.logger.error(f"BatchAssignmentID {batch_assignment_id} not found in DB.")
        return jsonify({'status': 'error', 'message': 'Batch assignment not found.'}), 404
    if batch_assignment.AssignedToUserID != current_user.id:
        app.logger.warning(f"User {current_user.id} unauthorized for BatchAssignmentID {batch_assignment_id}.")
        return jsonify({'status': 'error', 'message': 'Unauthorized for this batch assignment.'}), 403
    
    if batch_assignment.Status not in ['PENDING', 'IN_PROGRESS', 'COMPLETED_FAIL']: # Allow re-run on fail
        app.logger.warning(f"Attempt to run batch {batch_assignment_id} with invalid status: {batch_assignment.Status}")
        return jsonify({'status': 'error', 'message': f'Batch cannot be run in its current state: {batch_assignment.Status}.'}), 400

    with batch_state_lock:
        current_process_info = batch_test_processes.get(batch_assignment.BatchAssignmentID)
        if current_process_info and current_process_info.get('process') and \
           current_process_info['process'].poll() is None:
            app.logger.info(f"Batch {batch_assignment.BatchAssignmentID} is already running.")
            return jsonify({'status': 'error', 'message': f'Batch {batch_assignment.BatchAssignmentID} is already running.'}), 409
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.join(app.static_folder, 'reports', 'batch_live_output')
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f'batch_{batch_assignment.BatchAssignmentID}_out_{timestamp}.txt')
        
        # Initialize or update the entry for this batch
        batch_test_processes[batch_assignment.BatchAssignmentID] = {
            'process': None, 
            'output_file': output_file, 
            'thread': None,
            'final_log': '', # Reset final log for a new run
            'report_path_summary': None # Reset summary report path
        }
        app.logger.info(f"Prepared state for batch {batch_assignment.BatchAssignmentID}, output file: {output_file}")


    # Update batch status to IN_PROGRESS (models.py method handles DB)
    # Reset progress if re-running a failed batch
    if batch_assignment.Status == 'COMPLETED_FAIL':
        if BatchTestAssignment.update_progress(batch_assignment.BatchAssignmentID): # Ensure this model method exists
             app.logger.info(f"Resetting progress for re-run of failed batch {batch_assignment.BatchAssignmentID}")
        else:
            app.logger.error(f"Failed to reset progress for batch {batch_assignment.BatchAssignmentID}")
            # Decide if this is critical enough to stop execution
    
    if not BatchTestAssignment.update_status(batch_assignment.BatchAssignmentID, 'IN_PROGRESS'):
        app.logger.error(f"Failed to update batch {batch_assignment.BatchAssignmentID} status to IN_PROGRESS in DB.")
        # Potentially revert state_lock changes or handle error more gracefully
        return jsonify({'status': 'error', 'message': 'Failed to update batch status in database.'}), 500


    batch_runner_script_path = os.path.join(os.path.dirname(__file__), 'batch_runner.py')
    cmd_for_batch_runner = [
        sys.executable, batch_runner_script_path,
        str(batch_assignment.BatchAssignmentID),
        str(current_user.id),
        device_id_arg,
        android_ver_arg,
        password_arg if password_arg else "NO_PASSWORD_PLACEHOLDER",
        json.dumps(all_dynamic_inputs_arg)
    ]

    app.logger.info(f"Tester {current_user.username} initiating BATCH execution for BatchAssignmentID {batch_assignment.BatchAssignmentID}.")
    app.logger.info(f"Batch Runner Command: {' '.join(cmd_for_batch_runner)}")
    # Avoid logging all dynamic inputs if they can be very large or sensitive
    # app.logger.debug(f"Batch Runner Dynamic Inputs: {all_dynamic_inputs_arg}")


    def run_batch_runner_subprocess_thread(command, current_batch_id_for_thread, output_file_for_thread):
        global batch_test_processes, batch_state_lock # Thread needs access to globals
        process = None
        try:
            app.logger.info(f"Batch runner thread for {current_batch_id_for_thread} using output file: {output_file_for_thread}")
            with open(output_file_for_thread, 'w', encoding='utf-8') as f_out:
                creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                process = subprocess.Popen(command, stdout=f_out, stderr=subprocess.STDOUT, text=True, bufsize=1, creationflags=creation_flags)
                
                with batch_state_lock:
                    if current_batch_id_for_thread in batch_test_processes: # Check if entry still exists
                         batch_test_processes[current_batch_id_for_thread]['process'] = process
                    else: # Batch might have been cancelled or state cleared
                        app.logger.warning(f"State for batch {current_batch_id_for_thread} was cleared before process could be stored. Terminating process.")
                        if process: process.terminate() # Terminate if we can't track it
                        return


                process.wait() # Wait for batch_runner.py to complete
                
                app.logger.info(f"Batch runner (PID {process.pid if process else 'N/A'}) for BatchID {current_batch_id_for_thread} completed with exit code {process.returncode if process else 'N/A'}.")
                # The batch_runner.py script is responsible for updating the final DB status of the batch.

        except Exception as e_thread:
            app.logger.error(f"Exception in batch runner thread for BatchID {current_batch_id_for_thread}: {e_thread}", exc_info=True)
            # Attempt to mark batch as failed if thread itself crashes before/during Popen
            BatchTestAssignment.update_status(current_batch_id_for_thread, 'COMPLETED_FAIL')
        finally:
            with batch_state_lock:
                if current_batch_id_for_thread in batch_test_processes:
                    batch_test_processes[current_batch_id_for_thread]['process'] = None
                    batch_test_processes[current_batch_id_for_thread]['thread'] = None
            app.logger.info(f"Batch runner thread for BatchID {current_batch_id_for_thread} finished.")

    thread = threading.Thread(target=run_batch_runner_subprocess_thread, args=(cmd_for_batch_runner, batch_assignment.BatchAssignmentID, output_file))
    with batch_state_lock:
        batch_test_processes[batch_assignment.BatchAssignmentID]['thread'] = thread
    thread.start()

    app.logger.info(f"Launched batch runner thread for BatchAssignmentID {batch_assignment.BatchAssignmentID}.")
    return jsonify({'status': 'batch_execution_started', 'message': f'Batch {batch_assignment.BatchAssignmentID} execution process initiated.'})
   

# ******************************************************************************
# * EXISTING ROUTES TO BE MODIFIED/PROTECTED                                   *
# ******************************************************************************
@app.route('/')
@login_required
def index():
    with state_lock: # Your existing cleanup
        if test_status['output_file'] and not test_status['running']:
            try:
                if os.path.exists(test_status['output_file']): os.remove(test_status['output_file'])
            except OSError as e: app.logger.error(f"Error cleaning up file {test_status['output_file']}: {e}")
            finally:
                 test_status['output_file'] = None
                 test_status['final_output'] = ''
                 test_status['report_path'] = None

    if current_user.role == 'admin': return redirect(url_for('admin_dashboard'))
    elif current_user.role == 'manager': return redirect(url_for('manager_dashboard'))
    elif current_user.role == 'tester': return redirect(url_for('tester_dashboard'))
    else:
        flash("User role not recognized. Logging out.", "danger")
        logout_user()
        return redirect(url_for('login'))

# --- /run-test MODIFIED ---
@app.route('/run-test', methods=['POST'])
@login_required
def run_test():
    if not (current_user.role == 'tester' or current_user.role == 'admin'):
        return jsonify({'status': 'error', 'message': 'You are not authorized to run tests.'}), 403

    global test_status
    with state_lock:
        if test_status['running']:
            return jsonify({'status': 'error', 'message': 'A test is already running.'}), 409

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_dir = os.path.join(app.static_folder, 'reports', 'live_output') # Ensure live_output is under static if served directly
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
    testcase_id = data.get('test_case')
    individual_assignment_id = data.get('assignment_id') # This is test_assignments.AssignmentID
    # batch_assignment_id_from_form = data.get('batch_assignment_id') # Passed from run_assigned_test.html if part of batch

    if not testcase_id:
        with state_lock: test_status['running'] = False
        return jsonify({'status': 'error', 'message': 'Test Case ID is missing.'}), 400

    # Store current assignment context globally for get_progress to use
    with state_lock:
        test_status['current_assignment_id'] = individual_assignment_id
        # Find BatchAssignmentID if individual_assignment_id is provided
        current_batch_id = None
        if individual_assignment_id:
            with get_db_conn_from_models() as conn:
                with conn.cursor(dictionary=True) as cursor:
                    cursor.execute("SELECT BatchAssignmentID FROM test_assignments WHERE AssignmentID = %s", (individual_assignment_id,))
                    res = cursor.fetchone()
                    if res: current_batch_id = res['BatchAssignmentID']
        test_status['current_batch_assignment_id'] = current_batch_id


    if individual_assignment_id:
        # Update individual assignment status to 'IN_PROGRESS'
        # Use TestAssignment.update_status from models.py
        if not TestAssignment.update_status(individual_assignment_id, 'IN_PROGRESS'):
            app.logger.warning(f"Failed to update assignment {individual_assignment_id} to IN_PROGRESS for user {current_user.id}.")
        else:
            app.logger.info(f"Assignment {individual_assignment_id} status updated to IN_PROGRESS.")
        # If it's part of a batch, ensure batch is also IN_PROGRESS
        if test_status['current_batch_assignment_id']:
            batch_assign = BatchTestAssignment.get(test_status['current_batch_assignment_id'])
            if batch_assign and batch_assign.Status == 'PENDING':
                BatchTestAssignment.update_status(test_status['current_batch_assignment_id'], 'IN_PROGRESS')


    runner_path = os.path.join(os.path.dirname(__file__), 'generic_runner.py')
    cmd = [
        sys.executable, runner_path,
        device_id, android_ver, str(testcase_id), str(current_user.id)
    ]
    cmd.append(password if password else "NO_PASSWORD_PLACEHOLDER")
    cmd.append(str(individual_assignment_id) if individual_assignment_id else "NO_ASSIGNMENT_ID_PLACEHOLDER")

    dynamic_params = {k: v for k, v in data.items() if k not in ('device_id','android_version','password','test_case', 'assignment_id')}
    env = os.environ.copy()
    env['DYNAMIC_PARAMS'] = json.dumps(dynamic_params)

    app.logger.info(f"User {current_user.username} initiating test. Command: {' '.join(cmd)}")
    thread = threading.Thread(target=run_test_subprocess, args=(cmd, output_file_path, env))
    with state_lock: test_status['thread'] = thread
    thread.start()

    return jsonify({'status': 'started', 'assignment_id': individual_assignment_id, 'output_file': output_file_path})

# --- /get-progress MODIFIED ---
@app.route('/get-progress')
@login_required
def get_progress():
    global test_status
    output_content = ""
    is_running = False
    report_path = None
    error_message = None
    current_assignment_id_from_state = None
    current_batch_assignment_id_from_state = None

    with state_lock:
        is_running = test_status['running']
        current_output_file = test_status['output_file']
        report_path_from_state = test_status['report_path']
        current_assignment_id_from_state = test_status.get('current_assignment_id')
        current_batch_assignment_id_from_state = test_status.get('current_batch_assignment_id')


        if current_output_file and os.path.exists(current_output_file):
            try:
                with open(current_output_file, 'r', encoding='utf-8') as f:
                    output_content = f.read()
            except Exception as e:
                error_message = f"Error reading output file: {e}"
                app.logger.error(f"Error reading output file {current_output_file}: {e}", exc_info=True)
        elif current_output_file and not os.path.exists(current_output_file) and is_running:
             output_content = "Output file not yet available..."

        if not is_running and test_status['final_output']:
             output_content = test_status['final_output']
             report_path = test_status['report_path'] # Use the one set by runner
        elif is_running: # If still running, report path is not final
            report_path = None
        else: # Not running, no final_output means it might have just finished or error
            report_path = report_path_from_state


    # Logic to handle post-test run updates, especially for batches
    if not is_running and current_assignment_id_from_state:
        # This means a generic_runner.py process just finished for current_assignment_id_from_state
        app.logger.info(f"Test for assignment {current_assignment_id_from_state} (part of batch {current_batch_assignment_id_from_state}) has finished.")

        # generic_runner.py should have updated test_assignments.Status and test_assignments.ExecutionID

        if current_batch_assignment_id_from_state:
            # Update batch progress
            # Determine if the last test was a pass or fail based on test_assignments status (which generic_runner updated)
            individual_assignment_status = None
            with get_db_conn_from_models() as conn:
                with conn.cursor(dictionary=True) as cursor:
                    cursor.execute("SELECT Status FROM test_assignments WHERE AssignmentID = %s", (current_assignment_id_from_state,))
                    res = cursor.fetchone()
                    if res: individual_assignment_status = res['Status']
            
            passed_increment = 1 if individual_assignment_status == 'EXECUTED_PASS' else 0
            BatchTestAssignment.update_progress(current_batch_assignment_id_from_state, completed_increment=1, passed_increment=passed_increment)
            app.logger.info(f"Updated progress for batch {current_batch_assignment_id_from_state}.")

            # Check if the batch is now fully complete
            batch_assign_obj = BatchTestAssignment.get(current_batch_assignment_id_from_state)
            if batch_assign_obj and batch_assign_obj.CompletedTestCases >= batch_assign_obj.TotalTestCases:
                final_batch_status = 'COMPLETED_FAIL' # Assume fail
                if batch_assign_obj.PassedTestCases == batch_assign_obj.TotalTestCases:
                    final_batch_status = 'COMPLETED_PASS'
                BatchTestAssignment.update_status(current_batch_assignment_id_from_state, final_batch_status)
                app.logger.info(f"Batch {current_batch_assignment_id_from_state} marked as {final_batch_status}.")


        # Reset current assignment tracking in global state AFTER processing
        with state_lock:
            test_status['current_assignment_id'] = None
            test_status['current_batch_assignment_id'] = None # We will get the next one via start_batch_run if needed

    # Cleanup of temporary output file
    if not is_running and current_output_file:
         with state_lock:
              if test_status['output_file'] == current_output_file and not test_status['running']:
                   try:
                        if os.path.exists(current_output_file): os.remove(current_output_file)
                   except OSError as e: app.logger.error(f"Error cleaning up final file {current_output_file}: {e}")
                   finally:
                        test_status['output_file'] = None
                        test_status['final_output'] = ''
                        # test_status['report_path'] is already set with the final path

    response_data = {
        'output': output_content,
        'running': is_running,
        'report_path': report_path,
        'current_assignment_id': current_assignment_id_from_state, # Send to client for context
        'current_batch_assignment_id': current_batch_assignment_id_from_state # Send to client
    }
    if error_message: response_data['error'] = error_message
    return jsonify(response_data)


# --- OTHER EXISTING API ROUTES (largely unchanged for this scope) ---
@app.route('/test-case/<int:tcid>/params')
@login_required
def get_testcase_params(tcid):
    params = get_testcase_dynamic_params_from_db(tcid)
    return jsonify(params)

@app.route('/api/steps/<int:testcase_id>')
@login_required
def api_steps(testcase_id):
    with get_db_connection() as conn: # Using original get_db_connection
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT StepOrder, Input, ExpectedResponse, ParamName, InputType FROM steps WHERE TestCaseID = %s ORDER BY StepOrder", (testcase_id,))
            return jsonify(cursor.fetchall())

@app.route('/api/test-stats')
# @login_required # Decide if this needs login
def api_test_stats():
    try:
        with get_db_connection() as conn: # Using original get_db_connection
            with conn.cursor(dictionary=True) as cursor:
                query = """
                    SELECT COUNT(*) AS TotalExecutions,
                           SUM(CASE WHEN OverallStatus = 'PASS' THEN 1 ELSE 0 END) AS PassedCount,
                           SUM(CASE WHEN OverallStatus = 'FAIL' THEN 1 ELSE 0 END) AS FailedCount
                    FROM testexecutions;
                """
                cursor.execute(query)
                stats = cursor.fetchone()
                total = stats.get('TotalExecutions', 0) or 0
                passed = stats.get('PassedCount', 0) or 0
                failed = stats.get('FailedCount', 0) or 0
                return jsonify({
                    "total_executions": total, "passed_tests": passed, "failed_tests": failed,
                    "passed_percentage": f"{(passed / total * 100) if total > 0 else 0:.1f}%",
                    "failed_percentage": f"{(failed / total * 100) if total > 0 else 0:.1f}%"
                })
    except Exception as e:
        app.logger.error(f"API DB error in /api/test-stats: {e}", exc_info=True)
        return jsonify({"error": "Database query failed"}), 500


@app.route('/api/test-cases')
@login_required
def api_test_cases_list():
    try:
        with get_db_connection() as conn: # Using original get_db_connection
            with conn.cursor(dictionary=True) as cursor:
                cursor.execute("""
                    SELECT tc.TestCaseID AS id, tc.Code AS code, tc.Name AS name,
                           COALESCE(tc.Module, 'Uncategorized') AS module, COUNT(s.StepID) AS step_count
                    FROM testcases tc LEFT JOIN steps s ON tc.TestCaseID = s.TestCaseID
                    GROUP BY tc.TestCaseID ORDER BY tc.Module, tc.Name;
                """)
                test_cases = cursor.fetchall()
                cursor.execute("SELECT DISTINCT COALESCE(Module, 'Uncategorized') AS module FROM testcases ORDER BY module;")
                modules = [row['module'] for row in cursor.fetchall()]
                return jsonify({"test_cases": test_cases, "modules": modules})
    except Exception as e:
        app.logger.error(f"API DB error in /api/test-cases: {e}", exc_info=True)
        return jsonify({"error": "Database query failed"}), 500


# --- Test Results Views (EXISTING) ---
def get_adhoc_executions_for_user(user_id, role, search_query=None):
    adhoc_executions = []
    with get_db_conn_from_models() as conn:
        with conn.cursor(dictionary=True) as cursor:
            query_fields = """
                SELECT
                    te.ExecutionID, te.ExecutionTime, te.OverallStatus,
                    tc.TestCaseID, tc.Code AS TestCaseCode, tc.Name AS TestCaseName,
                    COALESCE(tc.Module, 'Uncategorized') AS Module,
                    u_executor.Username AS ExecutedByUsername
            """
            query_from_join = """
                FROM testexecutions te
                JOIN testcases tc ON te.TestCaseID = tc.TestCaseID
                JOIN users u_executor ON te.ExecutedBy = u_executor.UserID
                LEFT JOIN test_assignments ta ON te.ExecutionID = ta.ExecutionID 
            """

            filter_conditions = ["(ta.BatchAssignmentID IS NULL OR ta.AssignmentID IS NULL)"]
            query_params = []

            if role == 'tester':
                filter_conditions.append("te.ExecutedBy = %s")
                query_params.append(user_id)

            if search_query:
                search_like = f"%{search_query.strip()}%"
                search_filter = """
                    (
                        tc.Module LIKE %s OR
                        te.OverallStatus LIKE %s OR
                        u_executor.Username LIKE %s OR
                        tc.Name LIKE %s OR
                        tc.Code LIKE %s
                    )
                """
                filter_conditions.append(search_filter)
                query_params.extend([search_like] * 5)

            full_query = query_fields + query_from_join
            if filter_conditions:
                full_query += " WHERE " + " AND ".join(filter_conditions)
            full_query += " ORDER BY te.ExecutionTime DESC LIMIT 200;"

            cursor.execute(full_query, tuple(query_params))
            adhoc_executions = cursor.fetchall()

    return adhoc_executions


@app.route('/test-results')
@login_required
def test_results_overview():
    completed_batch_assignments = []
    adhoc_test_executions = []
    conn = None  # Initialize conn for the finally block

    try:
        conn = get_db_conn_from_models()
        if not conn:
            flash("Database connection error.", "danger")
            app.logger.error("Failed to get DB connection in test_results_overview.")
            return render_template('results/test_result_overview.html',
                                   title="Test Execution Results - Error",
                                   batch_assignments=[],
                                   grouped_adhoc_executions={},
                                   user_role=current_user.role)

        cursor = conn.cursor(dictionary=True)

        search_query = request.args.get('search', '').strip()
        search_like = f"%{search_query}%" if search_query else None

        batch_query_base = """
            SELECT bta.*, 
                   u_assigner.Username AS AssignedByUsername, 
                   u_tester.Username AS AssignedToUsername
            FROM batch_test_assignments bta
            JOIN users u_assigner ON bta.AssignedByUserID = u_assigner.UserID
            JOIN users u_tester ON bta.AssignedToUserID = u_tester.UserID
        """
        batch_params = []

        status_filter_group = "(bta.Status LIKE 'COMPLETED_%%' OR bta.Status = 'CANCELLED')"
        all_dynamic_filters = [status_filter_group]

        if current_user.role == 'tester':
            all_dynamic_filters.append("bta.AssignedToUserID = %s")
            batch_params.append(current_user.id)
            app.logger.info(f"Tester filter will be applied for batches: AssignedToUserID = {current_user.id}")

        if search_query:
            search_filter = """
                (
                    u_tester.Username LIKE %s OR
                    u_assigner.Username LIKE %s OR
                    bta.AssignmentType LIKE %s OR
                    bta.Status LIKE %s OR
                    bta.ReferenceName LIKE %s OR
                    bta.Priority LIKE %s OR
                    DATE_FORMAT(bta.AssignmentDate, '%%Y-%%m-%%d') LIKE %s
                )
            """
            all_dynamic_filters.append(search_filter)
            batch_params.extend([search_like] * 7)

        batch_query = batch_query_base
        if all_dynamic_filters:
            batch_query += " WHERE " + " AND ".join(all_dynamic_filters)
        batch_query += " ORDER BY bta.AssignmentDate DESC LIMIT 50;"

        app.logger.info(f"Final Batch Query for Results: {batch_query}")
        app.logger.info(f"Batch Query Params: {tuple(batch_params)}")

        cursor.execute(batch_query, tuple(batch_params))
        completed_batch_assignments = cursor.fetchall()
        app.logger.info(f"Fetched {len(completed_batch_assignments)} batch assignments for results page.")
        cursor.close()

        # Fetch Ad-hoc executions with search
        adhoc_test_executions = get_adhoc_executions_for_user(current_user.id, current_user.role, search_query)

    except mysql.connector.Error as err:
        app.logger.error(f"DB error in test_results_overview for {current_user.username}: {err}", exc_info=True)
        flash("Error fetching test results.", "danger")
    except Exception as e:
        app.logger.error(f"Unexpected error in test_results_overview: {e}", exc_info=True)
        flash("An unexpected error occurred while fetching results.", "danger")
    finally:
        if conn and conn.is_connected():
            conn.close()
            app.logger.debug("Database connection closed in test_results_overview.")

    # Group and sort adhoc executions by module
    grouped_adhoc_executions = defaultdict(list)
    for exec_item in adhoc_test_executions:
        module_key = exec_item.get('Module', 'Uncategorized')
        grouped_adhoc_executions[module_key].append(exec_item)

    sorted_grouped_adhoc_executions = {}
    module_keys = sorted(grouped_adhoc_executions.keys(), key=lambda m: (m is None or m == 'Uncategorized', str(m).lower()))
    for key in module_keys:
        sorted_grouped_adhoc_executions[key] = grouped_adhoc_executions[key]

    return render_template('results/test_result_overview.html',
                           title="Test Execution Results",
                           batch_assignments=completed_batch_assignments,
                           grouped_adhoc_executions=sorted_grouped_adhoc_executions,
                           user_role=current_user.role)



# --- NEW Route for Batch Execution Detail ---
@app.route('/batch_execution_detail/<int:batch_assignment_id>')
@login_required
def batch_execution_detail(batch_assignment_id):
    batch_assignment_details = None
    individual_executions_in_batch = []

    # Fetch batch assignment details (ensure user is authorized)
    with get_db_conn_from_models() as conn:
        with conn.cursor(dictionary=True) as cursor:
            query_batch = """
                SELECT bta.*, u_assigner.Username AS AssignedByUsername, u_tester.Username AS AssignedToUsername
                FROM batch_test_assignments bta
                JOIN users u_assigner ON bta.AssignedByUserID = u_assigner.UserID
                JOIN users u_tester ON bta.AssignedToUserID = u_tester.UserID
                WHERE bta.BatchAssignmentID = %s
            """
            params_batch = [batch_assignment_id]
            if current_user.role == 'tester':
                query_batch += " AND bta.AssignedToUserID = %s"
                params_batch.append(current_user.id)
            
            cursor.execute(query_batch, tuple(params_batch))
            batch_assignment_details = cursor.fetchone()

    if not batch_assignment_details:
        flash("Batch assignment not found or you are not authorized to view it.", "danger")
        return redirect(url_for('test_results_overview'))

    # Fetch individual test executions linked to this batch assignment
    with get_db_conn_from_models() as conn:
        with conn.cursor(dictionary=True) as cursor:
            # This query links testexecutions through test_assignments to the batch
            query_executions = """
                SELECT te.ExecutionID, te.ExecutionTime, te.OverallStatus,
                       tc.TestCaseID, tc.Code AS TestCaseCode, tc.Name AS TestCaseName,
                       ta.AssignmentID as IndividualAssignmentID, ta.Status as IndividualAssignmentStatus
                       /* Add other fields like ExecutedByUsername if needed */
                FROM testexecutions te
                JOIN test_assignments ta ON te.ExecutionID = ta.ExecutionID
                JOIN testcases tc ON ta.TestCaseID = tc.TestCaseID
                WHERE ta.BatchAssignmentID = %s
                ORDER BY ta.AssignmentID ASC 
            """ 
            # Note: If a single test case can be executed multiple times within the same batch assignment instance,
            # this query might need adjustment or you'd rely on ExecutionTime to differentiate.
            # Typically, each individual assignment in a batch is executed once per batch run.
            cursor.execute(query_executions, (batch_assignment_id,))
            individual_executions_in_batch = cursor.fetchall()
            
    app.logger.debug(f"Batch details: {batch_assignment_details}")
    app.logger.debug(f"Individual executions in batch: {individual_executions_in_batch}")


    return render_template('results/batch_execution_detail.html',
                           title=f"Batch Results: {batch_assignment_details.get('ReferenceName', 'N/A')}",
                           batch=batch_assignment_details,
                           user_role=current_user.role,
                           executions=individual_executions_in_batch)


@app.route('/test-results/execution/<int:execution_id>')
@login_required
def execution_detail(execution_id):
    execution_summary, step_results = None, []
    batch_assignment_id_context = None # Initialize to None
    conn = None # Initialize conn to None for broader scope in finally block

    try:
        # Using your existing get_db_connection() for this route.
        conn = get_db_connection()
        if not conn:
            flash("Database connection error.", "danger")
            app.logger.error("Failed to get DB connection in execution_detail.")
            return redirect(url_for('test_results_overview'))

        with conn.cursor(dictionary=True) as cursor:
            # Authorization check for tester
            if current_user.role == 'tester':
                cursor.execute("SELECT te.ExecutionID FROM testexecutions te WHERE te.ExecutionID = %s AND te.ExecutedBy = %s", (execution_id, current_user.id))
                if not cursor.fetchone():
                    flash("You are not authorized to view this execution detail.", "danger")
                    # No conn.close() here, 'with' statement handles it if this was the only operation.
                    # However, since we have more ops, better to close explicitly at the end or error.
                    return redirect(url_for('test_results_overview')) # Connection will be closed in finally

            # Fetch main execution summary
            cursor.execute("""
                SELECT te.*, tc.Code AS TestCaseCode, tc.Name AS TestCaseName, tc.Module AS TestCaseModule,
                       u.Username AS ExecutedByUsername
                FROM testexecutions te
                JOIN testcases tc ON te.TestCaseID = tc.TestCaseID
                JOIN users u ON te.ExecutedBy = u.UserID
                WHERE te.ExecutionID = %s;
            """, (execution_id,))
            execution_summary_raw = cursor.fetchone() # Fetch as raw dictionary

            if not execution_summary_raw:
                flash(f"Execution ID {execution_id} not found.", "warning")
                return redirect(url_for('test_results_overview')) # Connection will be closed in finally

            # Create a mutable copy if needed, or work directly if cursor returns mutable dicts
            execution_summary = dict(execution_summary_raw) if execution_summary_raw else None


            # --- PARSE PARAMETERS ---
            if execution_summary and isinstance(execution_summary.get('Parameters'), str):
                try:
                    params_as_dict = json.loads(execution_summary['Parameters'])
                    execution_summary['Parameters_parsed'] = params_as_dict
                    execution_summary['Parameters_is_string'] = False
                except json.JSONDecodeError:
                    execution_summary['Parameters_parsed'] = None 
                    execution_summary['Parameters_is_string'] = True
            elif execution_summary: 
                execution_summary['Parameters_parsed'] = execution_summary.get('Parameters')
                execution_summary['Parameters_is_string'] = False



            # Fetch step results
            cursor.execute("""
                SELECT sr.*, s.StepOrder, s.Input AS OriginalStepInput, s.ExpectedResponse AS OriginalExpectedResponse
                FROM stepresults sr JOIN steps s ON sr.StepID = s.StepID
                WHERE sr.ExecutionID = %s ORDER BY s.StepOrder;
            """, (execution_id,))
            step_results = cursor.fetchall()

            # --- Fetch BatchAssignmentID if this execution is part of a batch ---
            # This assumes 'ExecutionID' in 'test_assignments' is updated by generic_runner.py
            cursor.execute("""
                SELECT ta.BatchAssignmentID
                FROM test_assignments ta
                WHERE ta.ExecutionID = %s AND ta.BatchAssignmentID IS NOT NULL
            """, (execution_id,))
            batch_context_row = cursor.fetchone()
            if batch_context_row:
                batch_assignment_id_context = batch_context_row['BatchAssignmentID']
            # --- END OF NEW ---

    except mysql.connector.Error as db_err:
        app.logger.error(f"DB error in execution_detail for {execution_id}: {db_err}", exc_info=True)
        flash("Error fetching execution details due to a database issue.", "danger")
        return redirect(url_for('test_results_overview'))
    except Exception as e:
        app.logger.error(f"Unexpected error in execution_detail for {execution_id}: {e}", exc_info=True)
        flash("An unexpected error occurred while fetching execution details.", "danger")
        return redirect(url_for('test_results_overview'))
    finally:
        if conn and conn.is_connected():
            conn.close()
            app.logger.debug("Database connection closed in execution_detail.")

    # If execution_summary is None at this point (e.g., initial fetch failed but didn't redirect early)
    if not execution_summary:
        # This case should ideally be caught earlier, but as a safeguard:
        flash(f"Execution ID {execution_id} details could not be fully loaded.", "warning")
        return redirect(url_for('test_results_overview'))

    return render_template(
        'results/execution_detail.html', # Ensure this path is correct
        title=f"Execution Detail: {execution_summary.get('TestCaseCode', 'Unknown')}",
        execution=execution_summary,
        steps=step_results,
        user_role=current_user.role,
        batch_assignment_id_context=batch_assignment_id_context
    )


# --- Existing Helper Functions (get_modules, get_applications, etc.) ---
# Ensure these use get_db_conn_from_models() if they interact with new models or for consistency
@app.route('/get-modules') # Used by create_testcase.html
def get_modules():
    app_type = request.args.get('appType', type=int)
    if not app_type: return jsonify([]), 400
    try:
        with get_db_conn_from_models() as conn:
            with conn.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT SuiteID, Name FROM testsuites WHERE AppType = %s ORDER BY Name", (app_type,))
                return jsonify(cursor.fetchall())
    except Exception as e:
        app.logger.error(f"Error in /get-modules: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/get-applications") # Used by create_testcase.html (as /apptype)
def get_applications(): # This seems to be the same as get_apptypes
    return get_apptypes()

@app.route('/apptype') # Used by create_testcase.html
def get_apptypes():
    try:
        with get_db_conn_from_models() as conn:
            with conn.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT id, name FROM application ORDER BY name")
                return jsonify(cursor.fetchall())
    except Exception as e:
        app.logger.error(f"Error in /apptype: {e}", exc_info=True)
        return jsonify([]), 500

@app.route('/add-module', methods=['POST']) # Used by create_testcase.html
@login_required # Should be admin or manager
@manager_or_admin_required # Or just admin_required
def add_module():
    data = request.json
    module_name = data.get('module_name')
    app_type = data.get('application_type')
    description = data.get('description_model') # from your create_testcase.html

    if not module_name or not app_type:
        return jsonify(success=False, error="Module name and application type are required."), 400

    try:
        with get_db_conn_from_models() as conn:
            with conn.cursor() as cursor:
                now = datetime.now()
                cursor.execute("""
                    INSERT INTO testsuites (Name, AppType, Description, CreatedAt, ModifiedAt, CreatedBy)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (module_name, app_type, description, now, now, current_user.id))
                # conn.commit() # autocommit
                return jsonify(success=True, module_id=cursor.lastrowid)
    except mysql.connector.Error as e:
        # conn.rollback() # if not autocommit
        app.logger.error(f"Error in /add-module: {e}", exc_info=True)
        if e.errno == 1062: # Duplicate entry
             return jsonify(success=False, error=f"A module with name '{module_name}' might already exist for this application type."), 409
        return jsonify(success=False, error=str(e)), 500

def execute_sql_query(query_string, params=None, fetch_one=False):
    try:
        with get_db_conn_from_models() as conn: # Use your context manager
            with conn.cursor(dictionary=True) as cursor: # dictionary=True is good for dict results
                cursor.execute(query_string, params or ())
                if fetch_one:
                    row = cursor.fetchone()
                    return row # Already a dict
                else:
                    rows = cursor.fetchall()
                    return rows # Already a list of dicts
    except mysql.connector.Error as err:
        # Log the error for debugging
        # You might have a global app logger: current_app.logger.error(f"DB Query Error: {err} in query: {query_string}")
        print(f"DB Query Error: {err} in query: {query_string} with params {params}") # Simple print for now
        return None if fetch_one else []
    except Exception as e: # Catch other potential errors from get_db_conn_from_models
        print(f"General Error during DB operation: {e}")
        return None if fetch_one else []


@app.route('/analytics')
@login_required
def analytics_dashboard():


    if current_user.role not in ['admin', 'manager']:
        flash("You do not have permission to view this page.", "danger")
        return redirect(url_for('index')) # Ensure 'index' route is defined

    stats = {}

    try:
        # 1. Total Tests Defined
        query_total_tests = "SELECT COUNT(TestCaseID) as count FROM testcases;"
        result_total_tests = execute_sql_query(query_total_tests, fetch_one=True)
        stats['total_tests'] = result_total_tests['count'] if result_total_tests and result_total_tests['count'] is not None else 0

        # 2. Total Executions
        query_adhoc_executions = "SELECT COUNT(ExecutionID) as count FROM testexecutions;"
        result_adhoc = execute_sql_query(query_adhoc_executions, fetch_one=True)
        adhoc_exec_count = result_adhoc['count'] if result_adhoc and result_adhoc['count'] is not None else 0
        adhoc_exec_count = int(adhoc_exec_count) if adhoc_exec_count is not None else 0

        query_batch_tc_executions = "SELECT SUM(CompletedTestCases) as sum_completed FROM batch_test_assignments WHERE Status LIKE 'COMPLETED_%';"
        result_batch_tc = execute_sql_query(query_batch_tc_executions, fetch_one=True)
        batch_tc_exec_count = result_batch_tc['sum_completed'] if result_batch_tc and result_batch_tc['sum_completed'] is not None else 0
        batch_tc_exec_count = int(batch_tc_exec_count) if batch_tc_exec_count is not None else 0
        stats['total_executions'] = adhoc_exec_count + batch_tc_exec_count

        # 3. Passed Executions
        query_adhoc_passed = "SELECT COUNT(ExecutionID) as count FROM testexecutions WHERE OverallStatus = 'PASS';"
        result_adhoc_passed = execute_sql_query(query_adhoc_passed, fetch_one=True)
        adhoc_passed_count = result_adhoc_passed['count'] if result_adhoc_passed and result_adhoc_passed['count'] is not None else 0
        adhoc_passed_count = int(adhoc_passed_count) if adhoc_passed_count is not None else 0

        query_batch_passed_tc = "SELECT SUM(PassedTestCases) as sum_passed FROM batch_test_assignments WHERE Status LIKE 'COMPLETED_%';"
        result_batch_passed = execute_sql_query(query_batch_passed_tc, fetch_one=True)
        batch_passed_count = result_batch_passed['sum_passed'] if result_batch_passed and result_batch_passed['sum_passed'] is not None else 0
        batch_passed_count = int(batch_passed_count) if batch_passed_count is not None else 0
        stats['passed_executions'] = adhoc_passed_count + batch_passed_count

        # 4. Failed Executions
        total_exec = stats.get('total_executions', 0)
        passed_exec = stats.get('passed_executions', 0)
        stats['failed_executions'] = total_exec - passed_exec

        # 5. Active Testers
        query_active_testers = "SELECT COUNT(UserID) as count FROM users WHERE Role = 'tester' AND IsActive = 1;"
        result_active_testers = execute_sql_query(query_active_testers, fetch_one=True)
        stats['active_testers'] = result_active_testers['count'] if result_active_testers and result_active_testers['count'] is not None else 0


        # --- Data for Charts (Manager/Admin) ---
        if current_user.role in ['admin', 'manager']:
            # Chart 1: Executions by Batch
            query_batch_summary = """
                SELECT BatchAssignmentID, ReferenceName, PassedTestCases, (CompletedTestCases - PassedTestCases) as FailedTestCases
                FROM batch_test_assignments
                WHERE Status LIKE 'COMPLETED_%'
                ORDER BY AssignmentDate DESC
                LIMIT 5;
            """
            batch_execution_data = execute_sql_query(query_batch_summary)
            stats['batch_chart_labels'] = [b['ReferenceName'] for b in batch_execution_data] if batch_execution_data else []
            stats['batch_chart_pass_data'] = [int(b['PassedTestCases']) if b['PassedTestCases'] is not None else 0 for b in batch_execution_data] if batch_execution_data else []
            stats['batch_chart_fail_data'] = [int(b['FailedTestCases']) if b['FailedTestCases'] is not None else 0 for b in batch_execution_data] if batch_execution_data else []

            # Chart 2: Executions by Application (Pass/Fail from adhoc)
            query_app_summary = """
                SELECT
                    app.name as ApplicationName,
                    SUM(CASE WHEN te.OverallStatus = 'PASS' THEN 1 ELSE 0 END) as PassedCount,
                    SUM(CASE WHEN te.OverallStatus = 'FAIL' THEN 1 ELSE 0 END) as FailedCount
                FROM testexecutions te
                JOIN testcases tc ON te.TestCaseID = tc.TestCaseID
                JOIN testsuites ts ON tc.Module_id = ts.SuiteID
                JOIN application app ON ts.AppType = app.id
                GROUP BY app.name
                ORDER BY app.name;
            """
            app_execution_data = execute_sql_query(query_app_summary)
            # print(app_execution_data)
            stats['app_chart_labels'] = []
            stats['app_chart_pass_data'] = []
            stats['app_chart_fail_data'] = []
            if app_execution_data:
                for app_data in app_execution_data:
                    stats['app_chart_labels'].append(app_data['ApplicationName'])
                    stats['app_chart_pass_data'].append(int(app_data['PassedCount']) if app_data['PassedCount'] is not None else 0)
                    stats['app_chart_fail_data'].append(int(app_data['FailedCount']) if app_data['FailedCount'] is not None else 0)
                
            print(stats)

            # MODIFIED: Chart for ALL Test Assignments by Priority
            # MODIFIED: Chart for ALL EXECUTED Test Assignments by Priority
            stats['priority_pie_labels'] = ['HIGH', 'MEDIUM', 'LOW'] # Match ENUM values case
            # Updated Colors: High: Green (#28a745), Medium: Blue (#007bff), Low: Red (#dc3545)
            stats['priority_pie_colors'] = ['#28a745', '#007bff', '#dc3545'] 
            
            # MODIFIED QUERY: Count all executed (passed or failed) assignments by priority
            query_executed_by_priority = """
                SELECT
                    Priority, 
                    COUNT(*) as count
                FROM test_assignments  
                WHERE Status IN ('EXECUTED_PASS', 'EXECUTED_FAIL') 
                GROUP BY Priority;
            """
            priority_results = execute_sql_query(query_executed_by_priority)
            
            priority_counts_from_db = {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0} 
            if priority_results:
                for row in priority_results:
                    priority_key = row.get('Priority') 
                    if priority_key in priority_counts_from_db: 
                        priority_counts_from_db[priority_key] = int(row['count']) if row['count'] is not None else 0
            
            stats['priority_pie_data'] = [
                priority_counts_from_db.get('HIGH', 0), 
                priority_counts_from_db.get('MEDIUM', 0),
                priority_counts_from_db.get('LOW', 0)
            ]

    # ... (rest of the code remains the same) ...
            # Chart 3: Execution Trend Over Time (adhoc)
            execution_trend_dates = []
            execution_trend_pass = []
            execution_trend_fail = []
            for i in range(6, -1, -1): 
                day_date = datetime.now() - timedelta(days=i)
                day_str = day_date.strftime('%Y-%m-%d')
                execution_trend_dates.append(day_date.strftime('%b %d'))

                query_trend_pass = """
                    SELECT COUNT(ExecutionID) as count
                    FROM testexecutions
                    WHERE OverallStatus = 'PASS' AND DATE(ExecutionTime) = %s;
                """
                pass_count_result = execute_sql_query(query_trend_pass, params=(day_str,), fetch_one=True)
                execution_trend_pass.append(int(pass_count_result['count']) if pass_count_result and pass_count_result['count'] is not None else 0)

                query_trend_fail = """
                    SELECT COUNT(ExecutionID) as count
                    FROM testexecutions
                    WHERE OverallStatus = 'FAIL' AND DATE(ExecutionTime) = %s;
                """
                fail_count_result = execute_sql_query(query_trend_fail, params=(day_str,), fetch_one=True)
                execution_trend_fail.append(int(fail_count_result['count']) if fail_count_result and fail_count_result['count'] is not None else 0)

            stats['execution_trend_dates'] = execution_trend_dates
            stats['execution_trend_pass_data'] = execution_trend_pass
            stats['execution_trend_fail_data'] = execution_trend_fail

    except Exception as e:
        print(f"Error in analytics_dashboard: {e}") # Good for server-side logging
        flash(f"Error fetching analytics data: {str(e)}. Please try again later or contact support.", "danger")
        # Initialize with defaults to prevent template errors
        stats.setdefault('total_tests', 'Error')
        stats.setdefault('total_executions', 'Error')
        stats.setdefault('passed_executions', 'Error')
        stats.setdefault('failed_executions', 'Error')
        stats.setdefault('active_testers', 'Error')
        stats.setdefault('batch_chart_labels', [])
        stats.setdefault('batch_chart_pass_data', [])
        stats.setdefault('batch_chart_fail_data', [])
        stats.setdefault('app_chart_labels', [])
        stats.setdefault('app_chart_pass_data', [])
        stats.setdefault('app_chart_fail_data', [])
        stats.setdefault('priority_pie_labels', ['HIGH', 'MEDIUM', 'LOW'])
        stats.setdefault('priority_pie_data', [0,0,0])
        stats.setdefault('priority_pie_colors', ['#0d6efd', '#6ea8fe', '#dc3545'])
        stats.setdefault('execution_trend_dates', [])
        stats.setdefault('execution_trend_pass_data', [])
        stats.setdefault('execution_trend_fail_data', [])
        # print(f"Stats on error: {stats}") # Be cautious with logging sensitive data

    return render_template('analytics/analytics_dashboard.html',  user_role=current_user.role, title="System Analytics", stats=stats)


# --- HELPER: run_test_subprocess (Your existing function, assumed to be defined below) ---
def run_test_subprocess(cmd, output_file_path, env):
    global test_status
    full_output = ""
    final_report_path = None
    error_occurred = False

    app.logger.info(f"Starting subprocess: {' '.join(cmd)} with env DYNAMIC_PARAMS: {env.get('DYNAMIC_PARAMS')}")
    proc = None # Define proc outside try block for access in finally

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1, universal_newlines=True, env=env)
        with state_lock: test_status['process'] = proc

        with open(output_file_path, 'w', encoding='utf-8') as f_out:
            f_out.seek(0)
            f_out.truncate()
            for line in proc.stdout:
                line_strip = line.strip()
                app.logger.debug(f"RUNNER_STDOUT: {line_strip}")
                f_out.write(line)
                full_output += line
                if 'report saved at:' in line_strip.lower():
                    final_report_path = line_strip.split('report saved at:', 1)[-1].strip()
                f_out.flush()

            stderr_output = proc.stderr.read()
            if stderr_output:
                app.logger.error(f"RUNNER_STDERR: {stderr_output.strip()}")
                f_out.write("\n--- STDERR ---\n" + stderr_output)
                full_output += "\n--- STDERR ---\n" + stderr_output
                error_occurred = True
                f_out.flush()

        proc.wait()
        app.logger.info(f"Subprocess finished with exit code: {proc.returncode}")
        if proc.returncode != 0: error_occurred = True

        if not final_report_path:
            if error_occurred:
                final_report_path = f"Test script error (exit code {proc.returncode}). No report path found."
            else:
                final_report_path = f"Test completed (exit code {proc.returncode}). Report path marker not found."

    except FileNotFoundError:
        msg = "Error: Python executable or runner script not found."
        app.logger.error(msg, exc_info=True)
        full_output += f"\n{msg}\n"; error_occurred = True
        final_report_path = "Execution failed: Script not found."
    except Exception as e:
        msg = f"Unexpected error running test subprocess: {e}"
        app.logger.error(msg, exc_info=True)
        full_output += f"\n{msg}\n"; error_occurred = True
        final_report_path = f"Execution failed: {e}"
    finally:
        if proc and proc.poll() is None: # If process is still running (e.g. main thread exited due to error)
            app.logger.warning(f"Terminating runaway subprocess PID {proc.pid}")
            proc.terminate()
            try:
                proc.wait(timeout=5) # Wait a bit for termination
            except subprocess.TimeoutExpired:
                app.logger.error(f"Subprocess PID {proc.pid} did not terminate gracefully, killing.")
                proc.kill()

        with state_lock:
            test_status['running'] = False
            test_status['final_output'] = full_output
            test_status['report_path'] = final_report_path
            test_status['process'] = None
            test_status['thread'] = None
            # current_assignment_id and current_batch_assignment_id are handled by get_progress
        app.logger.info(f"Test subprocess processing complete. Report: {final_report_path}")

@app.route('/forgot_password', methods=['GET'])
def forgot_password():
    return render_template('auth/forgot-password.html')




@app.route('/admin/check-testcase-code/<code>')
@login_required
@admin_required
def check_testcase_code(code):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT TestCaseID FROM testcases WHERE Code = %s", (code,))
        existing = cursor.fetchone()
        return jsonify({'exists': bool(existing)})
    except Exception as e:
        app.logger.error(f"Error checking test case code: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()
# --- HELPERS for Manager Dashboard (Your existing) ---


@app.route('/editprofile', methods=['GET', 'POST']) 
@login_required
def edit_profile():
    if current_user.role not in ['admin', 'manager', 'tester']:
        flash("You do not have permission to view this page.", "danger")
        logout_user()
        return redirect(url_for('login'))

    user_id = current_user.id
    user_to_edit = User.get(user_id)
    if not user_to_edit:
        flash('User not found.', 'danger')
        return redirect(url_for('dashboard'))  # Safer than list_users

    form = EditUserForm(original_username=user_to_edit.username)

    if form.validate_on_submit():
        new_password = form.password.data.strip() or None
        is_last_admin = False

        if user_to_edit.role == 'admin':
            with get_db_conn_from_models() as conn:
                with conn.cursor(dictionary=True) as cursor:
                    cursor.execute("SELECT COUNT(*) AS admin_count FROM users WHERE Role = 'admin' AND IsActive = TRUE")
                    if cursor.fetchone()['admin_count'] <= 1:
                        is_last_admin = True

        if is_last_admin and form.role.data != 'admin':
            if current_user.id == user_id:
                flash('As the last active admin, you cannot change your own role from Admin.', 'danger')
            else:
                flash('Cannot change the role of the last active admin.', 'danger')
            return render_template('edit_profile.html', title='Edit Profile', form=form, user=user_to_edit)

        success, message = User.update(
            user_id=user_id,
            username=form.username.data,
            role=form.role.data,
            new_password=new_password
        )

        if success:
            flash(f'User "{form.username.data}" updated successfully.', 'success')
            return redirect(url_for('edit_profile'))
        else:
            flash(f'Error updating user: {message}', 'danger')

    elif request.method == 'GET':
        form.username.data = user_to_edit.username
        form.role.data = user_to_edit.role

    return render_template('edit_profile.html', title='Edit Profile', form=form, user=user_to_edit)



def get_all_applications_for_dashboard():
    with get_db_conn_from_models() as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT id, name FROM application ORDER BY name")
            return cursor.fetchall()
def get_application_by_id_for_dashboard(app_id):
    with get_db_conn_from_models() as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT id, name FROM application WHERE id = %s", (app_id,))
            return cursor.fetchone()
def get_suites_for_application_for_dashboard(app_id):
    with get_db_conn_from_models() as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT SuiteID, Name, AppType FROM testsuites WHERE AppType = %s ORDER BY Name", (app_id,))
            return cursor.fetchall()
def get_suite_by_id_for_dashboard(suite_id):
     with get_db_conn_from_models() as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT SuiteID, Name, AppType FROM testsuites WHERE SuiteID = %s", (suite_id,))
            return cursor.fetchone()
def get_test_cases_for_suite_for_dashboard(suite_id):
    with get_db_conn_from_models() as conn:
        with conn.cursor(dictionary=True) as cursor:
            query = """
                SELECT tc.TestCaseID, tc.Code, tc.Name, tc.Module,
                       GROUP_CONCAT(DISTINCT IF(ta.Status = 'PENDING', u_assigned.Username, NULL) SEPARATOR ', ') AS pending_assigned_to_usernames,
                       (SELECT COUNT(*) FROM test_assignments ta_count
                        WHERE ta_count.TestCaseID = tc.TestCaseID AND ta_count.Status = 'PENDING') AS pending_assignments_count
                FROM testcases tc
                JOIN suitetestcases stc ON tc.TestCaseID = stc.TestCaseID
                LEFT JOIN test_assignments ta ON tc.TestCaseID = ta.TestCaseID AND ta.Status = 'PENDING'
                LEFT JOIN users u_assigned ON ta.AssignedToUserID = u_assigned.UserID
                WHERE stc.SuiteID = %s
                GROUP BY tc.TestCaseID ORDER BY tc.Code;
            """
            cursor.execute(query, (suite_id,))
            return cursor.fetchall()

def get_testcase_dynamic_params_from_db(tcid): # Your existing helper
    with get_db_connection() as conn: # Original connection
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(""" SELECT ParamName, InputType, InpType, StepOrder FROM steps WHERE TestCaseID = %s AND InputType = 'dynamic' AND ParamName IS NOT NULL ORDER BY StepOrder """, (tcid,))
            return cursor.fetchall()

def get_all_test_cases_for_dashboard(): # Helper for custom group form
    with get_db_conn_from_models() as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT TestCaseID, Code, Name, Module FROM testcases ORDER BY Module, Code")
            return cursor.fetchall()

def get_test_cases_for_custom_group_selection():
    """
    Fetches all test cases, attempting to group them by application and suite.
    Also identifies test cases not linked to any suite.
    """
    applications_data = []
    uncategorized_test_cases = [] # Test cases with no module or not in any suite

    conn = None
    try:
        conn = get_db_conn_from_models()
        with conn.cursor(dictionary=True) as cursor:
            # 1. Get all applications
            cursor.execute("SELECT id, name FROM application ORDER BY name")
            apps = cursor.fetchall()

            for app_row in apps:
                app_detail = {'id': app_row['id'], 'name': app_row['name'], 'suites': []}
                
                # 2. Get suites for this application
                cursor.execute("SELECT SuiteID, Name FROM testsuites WHERE AppType = %s ORDER BY Name", (app_row['id'],))
                suites = cursor.fetchall()
                
                suite_tc_ids_in_app = set()

                for suite_row in suites:
                    suite_detail = {'id': suite_row['SuiteID'], 'name': suite_row['Name'], 'test_cases': []}
                    
                    # 3. Get test cases for this suite
                    cursor.execute("""
                        SELECT tc.TestCaseID, tc.Code, tc.Name, tc.Module as TestCaseModule
                        FROM testcases tc
                        JOIN suitetestcases stc ON tc.TestCaseID = stc.TestCaseID
                        WHERE stc.SuiteID = %s
                        ORDER BY tc.Code
                    """, (suite_row['SuiteID'],))
                    test_cases_for_suite = cursor.fetchall()
                    for tc in test_cases_for_suite:
                        suite_detail['test_cases'].append(tc)
                        suite_tc_ids_in_app.add(tc['TestCaseID'])
                    
                    if suite_detail['test_cases']: # Only add suite if it has test cases
                        app_detail['suites'].append(suite_detail)
                
                # 4. Get test cases directly under this app but not in any of its defined suites (based on testcases.Module matching app name, or if Module_id links to app)
                # This logic can be complex. For simplicity, we'll rely on suite linkage first.
                # Test cases belonging to this app type but not in any of the suites above
                # This part is tricky if 'testcases.Module' text doesn't directly map or if Module_id isn't used for app.
                # For now, let's focus on suite-linked TCs per app. We'll get truly uncategorized later.

                if app_detail['suites']: # Only add app if it has suites with test cases
                    applications_data.append(app_detail)

            # 5. Get all test case IDs that are part of *any* suite to exclude them from "truly uncategorized"
            cursor.execute("SELECT DISTINCT TestCaseID FROM suitetestcases")
            all_suite_linked_tc_ids = {row['TestCaseID'] for row in cursor.fetchall()}

            # 6. Get all test cases and then find the ones not linked to any suite
            cursor.execute("SELECT TestCaseID, Code, Name, Module as TestCaseModule FROM testcases ORDER BY Code")
            all_tcs_raw = cursor.fetchall()
            for tc_raw in all_tcs_raw:
                if tc_raw['TestCaseID'] not in all_suite_linked_tc_ids:
                    uncategorized_test_cases.append(tc_raw)

    except mysql.connector.Error as e:
        app.logger.error(f"Error fetching test cases for custom group selection: {e}", exc_info=True)
    finally:
        if conn and conn.is_connected():
            conn.close()
    
    return applications_data, uncategorized_test_cases




# --- Main Execution ---
if __name__ == '__main__':
    os.makedirs(os.path.join(app.static_folder, 'reports', 'live_output'), exist_ok=True)
    # Cleanup old live output files
    live_output_dir = os.path.join(app.static_folder, 'reports', 'live_output')
    for filename in os.listdir(live_output_dir):
        if filename.startswith('test_output_') and filename.endswith('.txt'):
            try: os.remove(os.path.join(live_output_dir, filename))
            except OSError as e: app.logger.error(f"Error cleaning old file {filename}: {e}")

    app.run(host='0.0.0.0', debug=True, port=5000)


    # An invalid form control with name='param_name' is not focusable. <input type=​"text" name=​"param_name" placeholder=​"e.g. pin_param (no spaces)​" required class=​"mt-1 w-full border rounded px-2 py-1 text-sm">​

    # <input type="text" name="param_name" placeholder="e.g. pin_param (no spaces)"
    # class="mt-1 w-full border rounded px-2 py-1 text-sm"/>