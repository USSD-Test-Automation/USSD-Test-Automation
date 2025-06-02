# models.py
from flask_login import UserMixin # UserMixin is not strictly used by your User class but often is in Flask-Login setups
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector
from functools import wraps # For decorators
import datetime # For default timestamps
from collections import defaultdict

# --- Database Configuration ---
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'actual_db',
    'autocommit': True # Important: This affects transaction handling
}

def get_db_connection():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as err:
        print(f"Database connection error in models: {err}")
        return None

class User:
    def __init__(self, id, username, password_hash, role, is_active=True):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.role = role
        self._is_active = is_active

    @property
    def is_active(self):
        return self._is_active

    def get_id(self):
        return str(self.id)

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    @staticmethod
    def get(user_id):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return None
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT UserID, Username, Password, Role, IsActive FROM users WHERE UserID = %s", (user_id,))
            user_data = cursor.fetchone()
            if user_data:
                return User(
                    id=user_data['UserID'],
                    username=user_data['Username'],
                    password_hash=user_data['Password'],
                    role=user_data['Role'],
                    is_active=user_data['IsActive']
                )
            return None
        except mysql.connector.Error as err:
            print(f"DB error in User.get({user_id}): {err}")
            return None
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def find_by_username(username):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return None
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT UserID, Username, Password, Role, IsActive FROM users WHERE Username = %s", (username,))
            user_data = cursor.fetchone()
            if user_data:
                return User(
                    id=user_data['UserID'],
                    username=user_data['Username'],
                    password_hash=user_data['Password'],
                    role=user_data['Role'],
                    is_active=user_data['IsActive']
                )
            return None
        except mysql.connector.Error as err:
            print(f"DB error in User.find_by_username({username}): {err}")
            return None
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @staticmethod
    def create(username, password, role):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return False, "Database connection failed."
            cursor = conn.cursor()
            hashed_password = generate_password_hash(password)
            cursor.execute(
                "INSERT INTO users (Username, Password, Role) VALUES (%s, %s, %s)",
                (username, hashed_password, role)
            )
            # conn.commit() # Not needed if autocommit=True and no further operations in this transaction
            return True, cursor.lastrowid
        except mysql.connector.Error as db_err:
            # if conn and not DB_CONFIG.get('autocommit', False): conn.rollback() # Only if explicit transaction
            print(f"DB error creating user {username}: {db_err}")
            if db_err.errno == 1062:
                return False, "Username already exists."
            return False, f"Database error: {db_err}"
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def get_all_users():
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return []
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT UserID, Username, Role, IsActive FROM users ORDER BY Username")
            users_data = cursor.fetchall()
            return [User(id=u['UserID'], username=u['Username'], password_hash=None, role=u['Role'], is_active=u['IsActive']) for u in users_data]
        except mysql.connector.Error as err:
            print(f"DB error in User.get_all_users: {err}")
            return []
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()
    
    def count_all():
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        conn.close()
        return count

    @staticmethod
    def update(user_id, username, role, new_password=None):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return False, "Database connection failed."
            cursor = conn.cursor()

            cursor.execute("SELECT UserID FROM users WHERE Username = %s AND UserID != %s", (username, user_id))
            if cursor.fetchone():
                return False, "Username already exists for another user."

            sql_parts = ["Username = %s", "Role = %s"]
            params = [username, role]

            if new_password:
                hashed_password = generate_password_hash(new_password)
                sql_parts.append("Password = %s")
                params.append(hashed_password)

            params.append(user_id)
            sql_query = f"UPDATE users SET {', '.join(sql_parts)} WHERE UserID = %s"
            cursor.execute(sql_query, tuple(params))
            # conn.commit()
            return True, "User updated successfully."
        except mysql.connector.Error as db_err:
            # if conn and not DB_CONFIG.get('autocommit', False): conn.rollback()
            print(f"DB error updating user {user_id}: {db_err}")
            return False, f"Database error: {db_err}"
        except Exception as e:
            print(f"Unexpected error updating user {user_id}: {e}")
            return False, f"An unexpected error occurred: {e}"
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def _set_active_status(user_id, status: bool, current_admin_id: int):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return False, "Database connection failed."
            cursor = conn.cursor(dictionary=True)

            if int(user_id) == int(current_admin_id) and not status:
                cursor.execute("SELECT COUNT(*) AS admin_count FROM users WHERE Role = 'admin' AND IsActive = TRUE")
                admin_count_info = cursor.fetchone()
                if admin_count_info and admin_count_info['admin_count'] <= 1:
                    return False, "Cannot deactivate the last active admin account (yourself)."

            if not status:
                user_to_modify = User.get(user_id)
                if user_to_modify and user_to_modify.role == 'admin':
                    cursor.execute("SELECT COUNT(*) AS admin_count FROM users WHERE Role = 'admin' AND IsActive = TRUE")
                    admin_count_info = cursor.fetchone()
                    if admin_count_info and admin_count_info['admin_count'] <= 1 and int(user_to_modify.id) == int(user_id):
                        return False, "Cannot deactivate the last active admin account."

            cursor.execute("UPDATE users SET IsActive = %s WHERE UserID = %s", (status, user_id))
            # conn.commit()
            if cursor.rowcount == 0:
                return False, "User not found or status already set."
            action = "activated" if status else "deactivated"
            return True, f"User successfully {action}."
        except mysql.connector.Error as db_err:
            # if conn and not DB_CONFIG.get('autocommit', False): conn.rollback()
            print(f"DB error setting active status for user {user_id}: {db_err}")
            return False, f"Database error: {db_err}"
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def deactivate(user_id_to_deactivate: int, current_admin_id: int):
        return User._set_active_status(user_id_to_deactivate, False, current_admin_id)

    @staticmethod
    def activate(user_id_to_activate: int, current_admin_id: int):
        return User._set_active_status(user_id_to_activate, True, current_admin_id)

    @staticmethod
    def get_testers():
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return []
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT UserID, Username FROM users WHERE Role = 'tester' AND IsActive = TRUE ORDER BY Username")
            testers = cursor.fetchall()
            return testers
        except mysql.connector.Error as err:
            print(f"DB error in User.get_testers: {err}")
            return []
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def delete(user_id_to_delete, current_admin_id):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return False, "Database connection failed."
            cursor = conn.cursor(dictionary=True)

            if int(user_id_to_delete) == int(current_admin_id):
                return False, "Cannot delete your own account through this method."

            user_to_delete_obj = User.get(user_id_to_delete)
            if not user_to_delete_obj:
                return False, "User not found."

            if user_to_delete_obj.role == 'admin' and user_to_delete_obj.is_active:
                cursor.execute("SELECT COUNT(*) AS admin_count FROM users WHERE Role = 'admin' AND IsActive = TRUE")
                admin_count_result = cursor.fetchone()
                if admin_count_result and admin_count_result['admin_count'] <= 1:
                    return False, "Cannot delete the last active admin user. Deactivate or promote another user first."

            cursor.execute("SELECT COUNT(*) as count FROM testexecutions WHERE ExecutedBy = %s", (user_id_to_delete,))
            if cursor.fetchone()['count'] > 0:
                return False, "Cannot delete user: This user has existing test execution records. Deactivate the user instead."

            # Check test_assignments (AssignedToUserID and AssignedByUserID)
            cursor.execute("SELECT COUNT(*) as count FROM test_assignments WHERE AssignedToUserID = %s OR AssignedByUserID = %s", (user_id_to_delete, user_id_to_delete))
            if cursor.fetchone()['count'] > 0:
                return False, "Cannot delete user: This user has existing test assignment records. Deactivate the user instead."

            # Check batch_test_assignments (AssignedToUserID and AssignedByUserID)
            cursor.execute("SELECT COUNT(*) as count FROM batch_test_assignments WHERE AssignedToUserID = %s OR AssignedByUserID = %s", (user_id_to_delete, user_id_to_delete))
            if cursor.fetchone()['count'] > 0:
                return False, "Cannot delete user: This user has existing batch assignment records. Deactivate the user instead."

            # Check custom_test_groups (CreatedByUserID)
            cursor.execute("SELECT COUNT(*) as count FROM custom_test_groups WHERE CreatedByUserID = %s", (user_id_to_delete,))
            if cursor.fetchone()['count'] > 0:
                return False, "Cannot delete user: This user has created custom test groups. Deactivate the user or reassign groups."

            cursor.execute("DELETE FROM users WHERE UserID = %s", (user_id_to_delete,))
            # conn.commit()
            if cursor.rowcount == 0:
                 return False, "User not found or could not be deleted."
            return True, "User deleted successfully."

        except mysql.connector.Error as db_err:
            # if conn and not DB_CONFIG.get('autocommit', False): conn.rollback()
            if 'foreign key constraint fails' in str(db_err).lower(): # This check might be too generic. Specific FK checks are better.
                 return False, f"Cannot delete user: User is referenced in other tables (e.g., testcases createdby). Deactivate instead. Error: {db_err}"
            return False, f"Database error: {db_err}"
        except Exception as e:
            return False, f"An unexpected error occurred: {e}"
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

# --- NEW MODEL CLASSES ---


class TestExecution:
    @staticmethod
    def count_all():
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM testexecutions")
        count = cursor.fetchone()[0]
        conn.close()
        return count

class BatchTestAssignment:
    def __init__(self, BatchAssignmentID, AssignedToUserID, AssignedByUserID, # CHANGED PARAMETER NAMES
                 AssignmentType, ReferenceID, ReferenceName=None, AssignmentDate=None,
                 Priority='MEDIUM', Status='PENDING', Notes=None,
                 TotalTestCases=0, CompletedTestCases=0, PassedTestCases=0,
                 AssignedByUsername=None): # Added to handle the join from get_for_tester
        self.BatchAssignmentID = BatchAssignmentID
        self.AssignedToUserID = AssignedToUserID
        self.AssignedByUserID = AssignedByUserID
        self.AssignmentType = AssignmentType
        self.ReferenceID = ReferenceID
        self.ReferenceName = ReferenceName
        # Ensure AssignmentDate is datetime object if it's a string from DB
        if isinstance(AssignmentDate, str):
            try:
                # Attempt to parse common datetime string formats from DB
                # Adjust format if your DB returns something different
                self.AssignmentDate = datetime.datetime.fromisoformat(AssignmentDate)
            except ValueError:
                # Fallback or raise error if format is unexpected
                self.AssignmentDate = datetime.datetime.now() # Or handle error
        elif AssignmentDate is None:
            self.AssignmentDate = datetime.datetime.now()
        else:
            self.AssignmentDate = AssignmentDate

        self.Priority = Priority
        self.Status = Status
        self.Notes = Notes
        self.TotalTestCases = TotalTestCases
        self.CompletedTestCases = CompletedTestCases
        self.PassedTestCases = PassedTestCases
        self.AssignedByUsername = AssignedByUsername # Store the joined username

    @staticmethod
    def create(assigned_to_user_id, assigned_by_user_id, assignment_type, reference_id,
               reference_name, total_test_cases, priority='MEDIUM', notes=None):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return None
            cursor = conn.cursor()
            sql = """
                INSERT INTO batch_test_assignments
                (AssignedToUserID, AssignedByUserID, AssignmentType, ReferenceID, ReferenceName,
                 Priority, Notes, TotalTestCases, Status, AssignmentDate)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'PENDING', NOW())
            """
            cursor.execute(sql, (assigned_to_user_id, assigned_by_user_id, assignment_type,
                                  reference_id, reference_name, priority, notes, total_test_cases))
            # conn.commit()
            return cursor.lastrowid
        except mysql.connector.Error as err:
            print(f"DB error creating BatchTestAssignment: {err}")
            return None
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def get(batch_assignment_id):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return None
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM batch_test_assignments WHERE BatchAssignmentID = %s", (batch_assignment_id,))
            data = cursor.fetchone()
            if data:
                return BatchTestAssignment(**data) # Assumes column names match constructor args
            return None
        except mysql.connector.Error as err:
            print(f"DB error in BatchTestAssignment.get({batch_assignment_id}): {err}")
            return None
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def get_for_tester(user_id, status_list=('PENDING', 'IN_PROGRESS')):
        conn = None
        cursor = None
        assignments = []
        try:
            conn = get_db_connection()
            if not conn: return assignments
            cursor = conn.cursor(dictionary=True)
            placeholders = ','.join(['%s'] * len(status_list))
            query = f"""
                SELECT bta.*, u_assigner.Username AS AssignedByUsername
                FROM batch_test_assignments bta
                JOIN users u_assigner ON bta.AssignedByUserID = u_assigner.UserID
                WHERE bta.AssignedToUserID = %s AND bta.Status IN ({placeholders})
                ORDER BY FIELD(bta.Priority, 'HIGH', 'MEDIUM', 'LOW'), bta.AssignmentDate ASC
            """
            params = [user_id] + list(status_list)
            cursor.execute(query, tuple(params))
            for row in cursor.fetchall():
                # The **row will now work because __init__ parameters match DB column names
                # and the added AssignedByUsername
                assignments.append(BatchTestAssignment(**row))
            return assignments
        except mysql.connector.Error as err:
            print(f"DB error in BatchTestAssignment.get_for_tester({user_id}): {err}")
            return assignments
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def update_status(batch_assignment_id, status):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return False
            cursor = conn.cursor()
            cursor.execute("UPDATE batch_test_assignments SET Status = %s WHERE BatchAssignmentID = %s",
                           (status, batch_assignment_id))
            # conn.commit()
            return cursor.rowcount > 0
        except mysql.connector.Error as err:
            print(f"DB error updating BatchTestAssignment status: {err}")
            return False
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def update_progress(batch_assignment_id, completed_increment=0, passed_increment=0):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return False
            cursor = conn.cursor()
            # Ensure atomicity if possible, or handle potential race conditions if critical
            sql = """
                UPDATE batch_test_assignments
                SET CompletedTestCases = CompletedTestCases + %s,
                    PassedTestCases = PassedTestCases + %s
                WHERE BatchAssignmentID = %s
            """
            cursor.execute(sql, (completed_increment, passed_increment, batch_assignment_id))
            # conn.commit()
            return cursor.rowcount > 0
        except mysql.connector.Error as err:
            print(f"DB error updating BatchTestAssignment progress: {err}")
            return False
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()


class CustomTestGroup:
    def __init__(self, CustomGroupID, Name, Description=None, CreatedByUserID=None, CreatedAt=None, **kwargs): # Match DB columns
        self.CustomGroupID = CustomGroupID
        self.Name = Name
        self.Description = Description
        self.CreatedByUserID = CreatedByUserID
        self.CreatedAt = CreatedAt if CreatedAt is not None else datetime.datetime.now()

    @staticmethod
    def create(name, created_by_user_id, description=None):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return None
            cursor = conn.cursor()
            cursor.execute("INSERT INTO custom_test_groups (Name, Description, CreatedByUserID, CreatedAt) VALUES (%s, %s, %s, NOW())",
                           (name, description, created_by_user_id))
            # conn.commit()
            group_id = cursor.lastrowid
            # Now add items if provided (logic for adding items would be separate or passed in)
            return group_id
        except mysql.connector.Error as err:
            print(f"DB error creating CustomTestGroup: {err}")
            return None
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def add_item(custom_group_id, test_case_id, order_in_group=None): # order_in_group is now optional
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return False
            cursor = conn.cursor()

            if order_in_group is None:
                # Calculate next order: find max current order for this group and add 1
                cursor.execute("SELECT COALESCE(MAX(OrderInGroup), -1) + 1 AS NextOrder FROM custom_test_group_items WHERE CustomGroupID = %s", (custom_group_id,))
                result = cursor.fetchone()
                order_in_group = result[0] if result else 0 # Default to 0 if no items yet

            cursor.execute(
                "INSERT INTO custom_test_group_items (CustomGroupID, TestCaseID, OrderInGroup) VALUES (%s, %s, %s)",
                (custom_group_id, test_case_id, order_in_group)
            )
            return True
        except mysql.connector.Error as err:
            # Handle potential duplicate entry if (CustomGroupID, TestCaseID) is PK and already exists
            if err.errno == 1062: # Duplicate entry
                print(f"Item (Group: {custom_group_id}, TC: {test_case_id}) already exists.")
                # Optionally, update the order if it already exists? Or just return False/True.
                # For now, let's assume we don't add duplicates.
                return False # Or True if "already exists" is considered success
            print(f"DB error adding item to CustomTestGroup: {err}")
            return False
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def remove_item(custom_group_id, test_case_id):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return False
            cursor = conn.cursor()
            cursor.execute("DELETE FROM custom_test_group_items WHERE CustomGroupID = %s AND TestCaseID = %s",
                           (custom_group_id, test_case_id))
            # conn.commit()
            return cursor.rowcount > 0
        except mysql.connector.Error as err:
            print(f"DB error removing item from CustomTestGroup: {err}")
            return False
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()
            
    @staticmethod
    def get_items(custom_group_id):
        conn = None
        cursor = None
        items = []
        try:
            conn = get_db_connection()
            if not conn: return items
            cursor = conn.cursor(dictionary=True)
            # Optionally join with testcases table to get test case details
            cursor.execute("""
                SELECT ctgi.TestCaseID, tc.Code, tc.Name, ctgi.OrderInGroup
                FROM custom_test_group_items ctgi
                JOIN testcases tc ON ctgi.TestCaseID = tc.TestCaseID
                WHERE ctgi.CustomGroupID = %s
                ORDER BY ctgi.OrderInGroup, tc.Code
            """, (custom_group_id,))
            items = cursor.fetchall()
            return items
        except mysql.connector.Error as err:
            print(f"DB error fetching items for CustomTestGroup {custom_group_id}: {err}")
            return items
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def get_all_groups_by_user(user_id): # Also applies to .get() and similar methods
        conn = None
        cursor = None
        groups = []
        try:
            conn = get_db_connection()
            if not conn: return groups
            cursor = conn.cursor(dictionary=True)
            # Select only the columns your __init__ expects or handle extras
            cursor.execute("SELECT CustomGroupID, Name, Description, CreatedByUserID, CreatedAt FROM custom_test_groups WHERE CreatedByUserID = %s ORDER BY Name", (user_id,))
            for row in cursor.fetchall():
                groups.append(CustomTestGroup(**row)) # Now this should work
            return groups
        except mysql.connector.Error as err:
            print(f"DB error fetching custom groups for user {user_id}: {err}")
            return groups
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def get(group_id): # Apply same fix here
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return None
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT CustomGroupID, Name, Description, CreatedByUserID, CreatedAt FROM custom_test_groups WHERE CustomGroupID = %s", (group_id,))
            data = cursor.fetchone()
            if data:
                return CustomTestGroup(**data) # Now this should work
            return None
        except mysql.connector.Error as err:
            print(f"DB error in CustomTestGroup.get({group_id}): {err}")
            return None
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def update(group_id, name, description=None):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return False
            cursor = conn.cursor()
            cursor.execute("UPDATE custom_test_groups SET Name = %s, Description = %s WHERE CustomGroupID = %s",
                           (name, description, group_id))
            # conn.commit()
            return cursor.rowcount > 0
        except mysql.connector.Error as err:
            print(f"DB error updating CustomTestGroup {group_id}: {err}")
            return False
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def delete(group_id):
        conn = None
        cursor = None
        try:
            # Note: Deleting a group will also delete its items due to ON DELETE CASCADE on custom_test_group_items
            # Also, check if it's referenced in batch_test_assignments
            conn = get_db_connection()
            if not conn: return False, "Database connection failed."
            cursor = conn.cursor(dictionary=True)

            # Check if group is used in any batch assignments
            cursor.execute("SELECT COUNT(*) as count FROM batch_test_assignments WHERE AssignmentType = 'CUSTOM_GROUP' AND ReferenceID = %s", (group_id,))
            if cursor.fetchone()['count'] > 0:
                return False, "Cannot delete group: It is used in existing batch assignments. Please remove those assignments first."

            cursor.execute("DELETE FROM custom_test_groups WHERE CustomGroupID = %s", (group_id,))
            # conn.commit()
            if cursor.rowcount > 0:
                return True, "Custom group deleted successfully."
            else:
                return False, "Custom group not found or could not be deleted."
        except mysql.connector.Error as err:
            print(f"DB error deleting CustomTestGroup {group_id}: {err}")
            return False, f"Database error: {err}"
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

# --- Test Case related models (Placeholder, you might have these elsewhere or expand) ---
class TestCaseModel: # Renamed to avoid conflict with table name if used directly
    @staticmethod
    def get_test_cases_for_suite(suite_id):
        conn = None
        cursor = None
        test_cases = []
        try:
            conn = get_db_connection()
            if not conn: return test_cases
            cursor = conn.cursor(dictionary=True)
            # Fetches TestCaseID and other relevant details if needed
            cursor.execute("""
                SELECT tc.TestCaseID, tc.Code, tc.Name
                FROM testcases tc
                JOIN suitetestcases stc ON tc.TestCaseID = stc.TestCaseID
                WHERE stc.SuiteID = %s
                ORDER BY stc.CaseOrder, tc.Code
            """, (suite_id,))
            test_cases = cursor.fetchall() # Returns list of dicts
            return test_cases
        except mysql.connector.Error as err:
            print(f"DB error in TestCaseModel.get_test_cases_for_suite({suite_id}): {err}")
            return test_cases
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def get_test_cases_for_application(app_id):
        conn = None
        cursor = None
        test_cases = []
        try:
            conn = get_db_connection()
            if not conn: return test_cases
            cursor = conn.cursor(dictionary=True)
            # Fetches distinct TestCaseIDs across all suites for a given app
            cursor.execute("""
                SELECT DISTINCT tc.TestCaseID, tc.Code, tc.Name
                FROM testcases tc
                JOIN suitetestcases stc ON tc.TestCaseID = stc.TestCaseID
                JOIN testsuites ts ON stc.SuiteID = ts.SuiteID
                WHERE ts.AppType = %s
                ORDER BY tc.Code
            """, (app_id,))
            test_cases = cursor.fetchall() # Returns list of dicts
            return test_cases
        except mysql.connector.Error as err:
            print(f"DB error in TestCaseModel.get_test_cases_for_application({app_id}): {err}")
            return test_cases
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

# --- Individual Test Assignment Model (enhancements might be needed) ---
class TestAssignment: # Your existing single test assignment logic would go here or be enhanced
    @staticmethod
    def create_for_batch(test_case_id, assigned_to_user_id, assigned_by_user_id,
                         priority, notes, batch_assignment_id):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return None
            cursor = conn.cursor()
            # Ensure the status is PENDING for a new assignment within a batch
            sql = """
                INSERT INTO test_assignments
                (TestCaseID, AssignedToUserID, AssignedByUserID, Priority, Notes, BatchAssignmentID, Status, AssignmentDate)
                VALUES (%s, %s, %s, %s, %s, %s, 'PENDING', NOW())
            """
            cursor.execute(sql, (test_case_id, assigned_to_user_id, assigned_by_user_id,
                                  priority, notes, batch_assignment_id))
            # conn.commit()
            return cursor.lastrowid
        except mysql.connector.Error as err:
            print(f"DB error creating individual TestAssignment for batch: {err}")
            return None
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def get_pending_in_batch(batch_assignment_id):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return None
            cursor = conn.cursor(dictionary=True)
            # Find the next PENDING test case, ideally ordered
            # The ordering might depend on how suitetestcases.CaseOrder is structured
            # or simply by TestCaseID or AssignmentID
            query = """
                SELECT ta.*, tc.Code as TestCaseCode, tc.Name as TestCaseName
                FROM test_assignments ta
                JOIN testcases tc ON ta.TestCaseID = tc.TestCaseID
                WHERE ta.BatchAssignmentID = %s AND ta.Status = 'PENDING'
                ORDER BY ta.AssignmentID ASC  -- Or some other relevant order
                LIMIT 1
            """
            cursor.execute(query, (batch_assignment_id,))
            return cursor.fetchone() # Returns a dict or None
        except mysql.connector.Error as err:
            print(f"DB error in TestAssignment.get_pending_in_batch({batch_assignment_id}): {err}")
            return None
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()

    @staticmethod
    def update_status(assignment_id, status, execution_id=None):
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn: return False
            cursor = conn.cursor()
            sql = "UPDATE test_assignments SET Status = %s"
            params = [status]
            if execution_id:
                sql += ", ExecutionID = %s"
                params.append(execution_id)
            sql += " WHERE AssignmentID = %s"
            params.append(assignment_id)
            
            cursor.execute(sql, tuple(params))
            # conn.commit()
            return cursor.rowcount > 0
        except mysql.connector.Error as err:
            print(f"DB error updating TestAssignment status for ID {assignment_id}: {err}")
            return False
        finally:
            if cursor: cursor.close()
            if conn and conn.is_connected(): conn.close()
   
    def get_dynamic_params_for_batch(batch_assignment_id):
                conn = None
                cursor = None
                params_info = {'COMMON': {}, 'TEST_CASE_SPECIFIC': {}}
                unique_common_params = {}
                param_to_tcs = defaultdict(list)

                try:
                    conn = get_db_connection()
                    if not conn:
                        return params_info
                    cursor = conn.cursor(dictionary=True)

                    query = """
                        SELECT DISTINCT
                            s.ParamName,
                            s.InpType,
                            s.Input AS UserFacingParamName,
                            tc.TestCaseID,
                            tc.Code AS TestCaseCode,
                            tc.Name AS TestCaseName
                        FROM steps s
                        JOIN testcases tc ON s.TestCaseID = tc.TestCaseID
                        JOIN test_assignments ta ON tc.TestCaseID = ta.TestCaseID
                        WHERE ta.BatchAssignmentID = %s
                        AND s.InputType = 'dynamic'
                        AND s.ParamName IS NOT NULL
                        AND s.ParamName != ''
                        ORDER BY tc.TestCaseID, s.StepOrder
                    """
                    cursor.execute(query, (batch_assignment_id,))
                    all_dynamic_steps = cursor.fetchall()
                    if not all_dynamic_steps:
                        return params_info

                    param_counts = defaultdict(int)
                    tc_ids_in_batch = set()
                    for step in all_dynamic_steps:
                        tc_ids_in_batch.add(step['TestCaseID'])
                        param_counts[step['ParamName']] += 1
                        param_to_tcs[step['ParamName']].append({
                            'TestCaseID': step['TestCaseID'],
                            'TestCaseCode': step['TestCaseCode'],
                            'TestCaseName': step['TestCaseName']
                        })
                        if step['ParamName'] not in unique_common_params:
                            unique_common_params[step['ParamName']] = {
                                'label': step['UserFacingParamName'],
                                'InpType': step['InpType']
                            }

                    num_test_cases = len(tc_ids_in_batch)
                    for param_name, count in param_counts.items():
                        param_info = unique_common_params.get(param_name, {'label': param_name, 'InpType': 'text'})
                        if count == num_test_cases and num_test_cases > 1:
                            params_info['COMMON'][param_name] = param_info
                        else:
                            for tc in param_to_tcs[param_name]:
                                tc_key = f"TC_{tc['TestCaseID']}"
                                if tc_key not in params_info['TEST_CASE_SPECIFIC']:
                                    params_info['TEST_CASE_SPECIFIC'][tc_key] = {
                                        'id': tc['TestCaseID'],
                                        'code': tc['TestCaseCode'],
                                        'name': tc['TestCaseName'],
                                        'params': {}
                                    }
                                params_info['TEST_CASE_SPECIFIC'][tc_key]['params'][param_name] = param_info

                    if num_test_cases == 1:
                        single_tc_id = list(tc_ids_in_batch)[0]
                        for step in all_dynamic_steps:
                            if step['TestCaseID'] == single_tc_id:
                                params_info['TEST_CASE_SPECIFIC'][f'TC_{single_tc_id}'] = {
                                    'id': single_tc_id,
                                    'code': step['TestCaseCode'],
                                    'name': step['TestCaseName'],
                                    'params': unique_common_params
                                }
                                break
                        params_info['COMMON'] = {}

                    return params_info

                except mysql.connector.Error as err:
                    print(f"DB error: {err}")
                    return params_info
                finally:
                    if cursor: cursor.close()
                    if conn and conn.is_connected(): conn.close()

    def count_all():
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM test_assignments")
        count_assigned = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM batch_test_assignments")
        count_batch_assigned = cursor.fetchone()[0]
        count = count_assigned + count_batch_assigned
        conn.close()
        return count

    @staticmethod
    def count_completed():
        conn = get_db_connection()
        cursor = conn.cursor()
        query_assigned = """
            SELECT COUNT(*) FROM test_assignments 
            WHERE Status IN ('EXECUTED_PASS', 'EXECUTED_FAIL')
        """
        cursor.execute(query_assigned)
        completed_assigned = cursor.fetchone()[0]

        query_batch_assigned = """
            SELECT COUNT(*) FROM batch_test_assignments 
            WHERE Status IN ('COMPLETED_PASS', 'COMPLETED_FAIL')
        """

        cursor.execute(query_batch_assigned)
        completed_batch_assigned = cursor.fetchone()[0]

        completed = completed_assigned + completed_batch_assigned

        conn.close()
        return completed

    @staticmethod
    def count_pending():
        conn = get_db_connection()
        cursor = conn.cursor()

        query_pending = """
            SELECT COUNT(*) FROM test_assignments 
            WHERE Status = 'PENDING'
        """
        cursor.execute(query_pending)
        pending_assigned = cursor.fetchone()[0]

        query_batch_pending = """
            SELECT COUNT(*) FROM batch_test_assignments 
            WHERE Status = 'PENDING'
        """
        cursor.execute(query_batch_pending)
        pending_batch_assigned = cursor.fetchone()[0]

        pending = pending_assigned + pending_batch_assigned
        
        conn.close()
        return pending

    @staticmethod
    def count_pass():
        conn = get_db_connection()
        cursor = conn.cursor()
        
        query_pass = """
            SELECT COUNT(*) FROM test_assignments 
            WHERE Status = 'EXECUTED_PASS'
        """
        cursor.execute(query_pass)
        assigned_pass = cursor.fetchone()[0]

        query_batch_pass = """
            SELECT COUNT(*) FROM batch_test_assignments 
            WHERE Status = 'COMPLETED_PASS'
        """
        cursor.execute(query_batch_pass)
        completed_batch_pass = cursor.fetchone()[0]

        passtests = assigned_pass + completed_batch_pass
        
        conn.close()
        return passtests

    @staticmethod
    def count_fail():
        conn = get_db_connection()
        cursor = conn.cursor()
        
        query_fail = """
            SELECT COUNT(*) FROM test_assignments 
            WHERE Status = 'EXECUTED_FAIL'
        """
        cursor.execute(query_fail)
        assigned_fail = cursor.fetchone()[0]

        query_batch_fail = """
            SELECT COUNT(*) FROM batch_test_assignments 
            WHERE Status = 'COMPLETED_FAIL'
        """
        cursor.execute(query_batch_fail)
        completed_batch_fail = cursor.fetchone()[0]

        failtests = assigned_fail + completed_batch_fail
        
        conn.close()
        return failtests

    
    @staticmethod
    def count_inprogress():
        conn = get_db_connection()
        cursor = conn.cursor()
        
        query_inprogress = """
            SELECT COUNT(*) FROM test_assignments 
            WHERE Status = 'IN_PROGRESS'
        """
        cursor.execute(query_inprogress)
        assigned_inprogress = cursor.fetchone()[0]

        query_batch_inprogress = """
            SELECT COUNT(*) FROM batch_test_assignments 
            WHERE Status = 'IN_PROGRESS'
        """
        cursor.execute(query_batch_inprogress)
        completed_batch_inprogress = cursor.fetchone()[0]

        inprogresstests = assigned_inprogress + completed_batch_inprogress
        
        conn.close()
        return inprogresstests