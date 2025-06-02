#!/usr/bin/env python

import sys
import os
import json
import time
import mysql.connector
from datetime import datetime
import subprocess # To call generic_runner.py

# --- Configuration ---
DB_CONFIG_BATCH_RUNNER = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'actual_db',
    'autocommit': False # Manage transactions explicitly for batch operations for safety
}

# --- Global Variables (for this script's context) ---
batch_db_conn = None
batch_db_cursor = None

# --- Helper Functions ---
def log_to_batch_stdout(message_type, message_content):
    """
    Logs messages with a type prefix for easier parsing by the Flask app if needed.
    Output is flushed to ensure it's seen by the parent process.
    Format: BATCH_RUNNER_TYPE: TIMESTAMP - MESSAGE
    Example: BATCH_RUNNER_INFO: 2023-10-27T10:00:00 - Starting test case X
    """
    timestamp = datetime.now().isoformat()
    print(f"BATCH_RUNNER_{message_type.upper()}: {timestamp} - {message_content}", flush=True)

def get_batch_runner_db_connection():
    try:
        conn = mysql.connector.connect(**DB_CONFIG_BATCH_RUNNER)
        return conn
    except mysql.connector.Error as err:
        log_to_batch_stdout("error", f"Database connection failed: {err}")
        return None

def main_batch_runner():
    global batch_db_conn, batch_db_cursor

    log_to_batch_stdout("info", "Batch runner process started.")

    if len(sys.argv) < 7:
        log_to_batch_stdout("error", "Insufficient arguments. "
                                     "Expected: batch_assignment_id user_id device_id android_ver password_placeholder all_dynamic_inputs_json")
        sys.exit(1)

    batch_assignment_id_arg = sys.argv[1]
    executed_by_user_id_arg = sys.argv[2]
    device_id_arg = sys.argv[3]
    android_version_arg = sys.argv[4]
    password_arg_from_caller = sys.argv[5]
    all_dynamic_inputs_json_arg = sys.argv[6]

    try:
        batch_assignment_id = int(batch_assignment_id_arg)
        executed_by_user_id = int(executed_by_user_id_arg)
        all_dynamic_inputs = json.loads(all_dynamic_inputs_json_arg)
        password_to_use = None if password_arg_from_caller == "NO_PASSWORD_PLACEHOLDER" else password_arg_from_caller
    except ValueError as e:
        log_to_batch_stdout("error", f"Invalid argument format: {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log_to_batch_stdout("error", f"Invalid JSON for dynamic inputs: {e}")
        sys.exit(1)

    log_to_batch_stdout("info", f"PARAMS: BatchAssignmentID={batch_assignment_id}, UserID={executed_by_user_id}, "
                                f"DeviceID='{device_id_arg}', AndroidVersion='{android_version_arg}', "
                                f"PasswordProvided={'Yes' if password_to_use else 'No'}")
    # Log dynamic inputs carefully, they might be large or sensitive. Maybe just keys or a summary.
    log_to_batch_stdout("debug", f"DYNAMIC_INPUTS_COLLECTED (first 5 keys): {list(all_dynamic_inputs.keys())[:5]}")


    batch_db_conn = get_batch_runner_db_connection()
    if not batch_db_conn:
        log_to_batch_stdout("fatal", "Exiting due to DB connection failure.")
        sys.exit(1)
    batch_db_cursor = batch_db_conn.cursor(dictionary=True)

    overall_batch_status = "COMPLETED_FAIL" # Default to fail, will be updated if all pass
    completed_tc_count_in_batch = 0
    passed_tc_count_in_batch = 0
    total_tc_in_batch_from_db = 0 # Will be fetched

    try:
        # Mark batch as IN_PROGRESS (if PENDING) and get total TCs
        batch_db_cursor.execute("SELECT Status, TotalTestCases FROM batch_test_assignments WHERE BatchAssignmentID = %s FOR UPDATE", (batch_assignment_id,))
        batch_info = batch_db_cursor.fetchone()
        if not batch_info:
            log_to_batch_stdout("error", f"BatchAssignmentID {batch_assignment_id} not found in database.")
            raise ValueError("Batch not found")
        
        total_tc_in_batch_from_db = batch_info.get('TotalTestCases', 0)
        current_batch_db_status = batch_info['Status']

        if current_batch_db_status == 'PENDING' or current_batch_db_status == 'COMPLETED_FAIL': # Allow re-run for failed
            if current_batch_db_status == 'COMPLETED_FAIL':
                # Reset progress for re-run
                batch_db_cursor.execute("UPDATE batch_test_assignments SET Status = 'IN_PROGRESS', CompletedTestCases = 0, PassedTestCases = 0 WHERE BatchAssignmentID = %s", (batch_assignment_id,))
                log_to_batch_stdout("info", f"Resetting progress and starting re-run for failed Batch {batch_assignment_id}.")
            else:
                batch_db_cursor.execute("UPDATE batch_test_assignments SET Status = 'IN_PROGRESS' WHERE BatchAssignmentID = %s", (batch_assignment_id,))
                log_to_batch_stdout("info", f"Batch {batch_assignment_id} status updated to IN_PROGRESS.")
            batch_db_conn.commit()
        elif current_batch_db_status == 'IN_PROGRESS':
            log_to_batch_stdout("info", f"Resuming IN_PROGRESS Batch {batch_assignment_id}.")
            # Fetch current progress to continue accurately
            batch_db_cursor.execute("SELECT CompletedTestCases, PassedTestCases FROM batch_test_assignments WHERE BatchAssignmentID = %s", (batch_assignment_id,))
            progress_info = batch_db_cursor.fetchone()
            if progress_info:
                completed_tc_count_in_batch = progress_info.get('CompletedTestCases', 0)
                passed_tc_count_in_batch = progress_info.get('PassedTestCases', 0)
        else: # COMPLETED_PASS or CANCELLED
            log_to_batch_stdout("warning", f"Batch {batch_assignment_id} is already {current_batch_db_status}. No action taken.")
            sys.exit(0)


        # Fetch all individual test assignments for this batch, ordered
        batch_db_cursor.execute("""
            SELECT ta.AssignmentID, ta.TestCaseID, tc.Code AS TestCaseCode, ta.Status AS IndividualStatus
            FROM test_assignments ta
            JOIN testcases tc ON ta.TestCaseID = tc.TestCaseID
            WHERE ta.BatchAssignmentID = %s
            ORDER BY ta.AssignmentID ASC
        """, (batch_assignment_id,))
        individual_assignments = batch_db_cursor.fetchall()

        if not individual_assignments:
            log_to_batch_stdout("warning", f"No individual test assignments found for BatchID {batch_assignment_id}.")
            if total_tc_in_batch_from_db == 0: overall_batch_status = "COMPLETED_PASS" # No tests to run
            # This error will be caught by the main try-except and finalize batch status
            raise ValueError("No test cases assigned to this batch.")


        log_to_batch_stdout("info", f"Found {len(individual_assignments)} TCs. Initial progress: {completed_tc_count_in_batch}/{total_tc_in_batch_from_db} completed, {passed_tc_count_in_batch} passed.")

        for i, assignment in enumerate(individual_assignments):
            individual_assignment_id = assignment['AssignmentID']
            test_case_id_to_run = assignment['TestCaseID']
            test_case_code = assignment['TestCaseCode']
            current_individual_status = assignment['IndividualStatus']

            log_to_batch_stdout("info", f"--- Starting TC {i+1}/{len(individual_assignments)}: {test_case_code} (AssignmentID: {individual_assignment_id}) ---")

            # Skip if already executed (e.g., on resume, this specific TC was already done)
            if current_individual_status.startswith("EXECUTED"):
                log_to_batch_stdout("info", f"TC {test_case_code} (Assignment {individual_assignment_id}) already '{current_individual_status}'. Skipping.")
                # Ensure counters are accurate if resuming, but this should only happen if the loop is restarted
                # The initial fetch of completed/passed counts handles this better for a full resume.
                # This skip is mainly for safety if somehow an executed test is re-processed.
                continue

            # Update individual assignment to IN_PROGRESS in DB before running
            batch_db_cursor.execute("UPDATE test_assignments SET Status = 'IN_PROGRESS' WHERE AssignmentID = %s", (individual_assignment_id,))
            batch_db_conn.commit()
            log_to_batch_stdout("db_update", f"Individual Assignment {individual_assignment_id} status set to IN_PROGRESS.")


            # Prepare dynamic parameters for this specific test case
            tc_specific_dynamic_params = {}
            # 1. Add common params (keys like 'pincode')
            for common_key, common_val in all_dynamic_inputs.items():
                if not common_key.startswith(f"TC_") and not common_key.startswith(f"COMMON_"): # True common, not prefixed
                    tc_specific_dynamic_params[common_key] = common_val
                elif common_key.startswith("COMMON__"): # common_param_pincode became COMMON__pincode
                    actual_param_name = common_key.replace("COMMON__", "")
                    tc_specific_dynamic_params[actual_param_name] = common_val

            # 2. Add/override with TC-specific params (keys like 'TC_123__amount')
            tc_id_prefix_in_key = f"TC_{test_case_id_to_run}__"
            for specific_key, specific_val in all_dynamic_inputs.items():
                if specific_key.startswith(tc_id_prefix_in_key):
                    actual_param_name = specific_key.replace(tc_id_prefix_in_key, "")
                    tc_specific_dynamic_params[actual_param_name] = specific_val
            
            log_to_batch_stdout("debug", f"Dynamic params for TC {test_case_code}: {json.dumps(tc_specific_dynamic_params)}")

            # Construct command for generic_runner.py
            generic_runner_script_path = os.path.join(os.path.dirname(__file__), 'generic_runner.py')
            cmd_for_generic_runner = [
                sys.executable, generic_runner_script_path,
                device_id_arg,
                android_version_arg,
                str(test_case_id_to_run),
                str(executed_by_user_id),
                password_to_use if password_to_use else "NO_PASSWORD_PLACEHOLDER",
                str(individual_assignment_id)
            ]

            env_for_generic_runner = os.environ.copy()
            env_for_generic_runner['DYNAMIC_PARAMS'] = json.dumps(tc_specific_dynamic_params)
            
            log_to_batch_stdout("info", f"Executing generic_runner for TC {test_case_code} (Assignment {individual_assignment_id})")
            
            process = subprocess.Popen(cmd_for_generic_runner,
                                       stdout=subprocess.PIPE, # Capture for logging
                                       stderr=subprocess.PIPE, # Capture for logging
                                       text=True,
                                       env=env_for_generic_runner,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            
            # Stream stdout and stderr from generic_runner
            for line in process.stdout:
                log_to_batch_stdout("runner_out", f"[TC:{test_case_code}]> {line.strip()}")
            for line in process.stderr:
                log_to_batch_stdout("runner_err", f"[TC:{test_case_code} ERR]> {line.strip()}")
            
            process.wait()
            log_to_batch_stdout("info", f"Generic_runner for TC {test_case_code} finished with exit code: {process.returncode}.")

            # generic_runner.py is responsible for updating the individual test_assignments.Status
            # and creating the testexecutions record.

            # Fetch the final status of the just-run individual assignment
            batch_db_cursor.execute("SELECT Status FROM test_assignments WHERE AssignmentID = %s", (individual_assignment_id,))
            updated_assignment_info = batch_db_cursor.fetchone()
            
            completed_tc_count_in_batch += 1 # Increment after each attempt
            if updated_assignment_info and updated_assignment_info['Status'] == 'EXECUTED_PASS':
                passed_tc_count_in_batch += 1
            
            # Update batch progress in DB immediately
            batch_db_cursor.execute(
                "UPDATE batch_test_assignments SET CompletedTestCases = %s, PassedTestCases = %s WHERE BatchAssignmentID = %s",
                (completed_tc_count_in_batch, passed_tc_count_in_batch, batch_assignment_id)
            )
            batch_db_conn.commit()
            log_to_batch_stdout("db_update", f"Batch progress: {completed_tc_count_in_batch}/{total_tc_in_batch_from_db} done. Passed: {passed_tc_count_in_batch}.")

            # Optional: Small delay if needed
            # time.sleep(1) 

        # After all TCs in the batch are processed
        if completed_tc_count_in_batch >= total_tc_in_batch_from_db: # Use >= for safety
            if passed_tc_count_in_batch == total_tc_in_batch_from_db:
                overall_batch_status = "COMPLETED_PASS"
            else:
                overall_batch_status = "COMPLETED_FAIL"
        else:
            log_to_batch_stdout("warning", f"Batch loop finished, but not all TCs processed. "
                                         f"Completed: {completed_tc_count_in_batch}, Total in DB: {total_tc_in_batch_from_db}. Batch status set to FAIL.")
            overall_batch_status = "COMPLETED_FAIL"

    except Exception as e_main_batch:
        log_to_batch_stdout("fatal", f"CRITICAL BATCH ERROR: {e_main_batch}")
        import traceback
        log_to_batch_stdout("traceback", traceback.format_exc())
        overall_batch_status = "COMPLETED_FAIL" # Or a specific ERROR status
    finally:
        if batch_db_conn and batch_db_cursor: # Ensure they were initialized
            try:
                # Final update to batch_test_assignments
                # Ensure completed_tc_count reflects actual attempts if loop broke early
                final_completed = completed_tc_count_in_batch
                final_passed = passed_tc_count_in_batch

                batch_db_cursor.execute(
                    "UPDATE batch_test_assignments SET Status = %s, CompletedTestCases = %s, PassedTestCases = %s WHERE BatchAssignmentID = %s",
                    (overall_batch_status, final_completed, final_passed, batch_assignment_id)
                )
                batch_db_conn.commit()
                log_to_batch_stdout("db_update", f"Final BatchAssignmentID {batch_assignment_id} status: {overall_batch_status}. "
                                               f"Completed: {final_completed}, Passed: {final_passed}.")
            except Exception as e_db_final_batch:
                log_to_batch_stdout("error", f"Failed to update final batch execution status for ID {batch_assignment_id}: {e_db_final_batch}")

        if batch_db_cursor: batch_db_cursor.close()
        if batch_db_conn and batch_db_conn.is_connected(): batch_db_conn.close()

    log_to_batch_stdout("info", f"Batch runner process finished. Overall Batch Status: {overall_batch_status}.")
    sys.exit(0) # Exit 0 to indicate normal termination of this script

if __name__ == "__main__":
    main_batch_runner()