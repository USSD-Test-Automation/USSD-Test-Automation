#!/usr/bin/env python

import sys
import os
import json
import time
import mysql.connector
from datetime import datetime
from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
# from openpyxl import Workbook # Excel reporting can be kept if desired
# from openpyxl.styles import PatternFill
# from openpyxl.chart import BarChart, Reference

# --- Configuration ---
DB_CONFIG_RUNNER = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'actual_db',
    'autocommit': False
}

# --- Global Variables ---
current_execution_id = None
db_conn = None
db_cursor = None
appium_driver = None

# --- Helper Functions ---
def log_to_stdout(message):
    print(message, flush=True)

def get_runner_db_connection():
    try:
        conn = mysql.connector.connect(**DB_CONFIG_RUNNER)
        return conn
    except mysql.connector.Error as err:
        log_to_stdout(f"RUNNER_ERROR: Database connection failed: {err}")
        return None

def response_matches_keywords(expected_keywords_list, actual_response_text):
    if not actual_response_text:
        return False
    actual_lower = actual_response_text.lower()
    return all(keyword.strip().lower() in actual_lower for keyword in expected_keywords_list if keyword.strip())

def cancel_ussd():
                     
        try:
            cancel_button_locator = (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("Cancel")')

            WebDriverWait(appium_driver, 5).until(EC.element_to_be_clickable(cancel_button_locator))
            cancel_button = appium_driver.find_element(*cancel_button_locator)

            cancel_button.click()
            log_to_stdout("USSD session cancelled successfully.", flush=True)

        except Exception as e:
            log_to_stdout(f"Failed to cancel': {e}", flush=True)
            appium_driver.driver.quit()
            exit()

def main_runner():
    global current_execution_id, db_conn, db_cursor, appium_driver

    log_to_stdout("RUNNER_INFO: Appium generic_runner.py started.")

    if len(sys.argv) < 5:
        log_to_stdout("RUNNER_ERROR: Insufficient args. Expected: device_id android_ver tc_id user_id [pass] [assign_id]")
        sys.exit(1)

    device_id_arg = sys.argv[1]
    android_version_arg = sys.argv[2]
    testcase_id_arg_str = sys.argv[3]
    executed_by_user_id_arg_str = sys.argv[4]
    
    password_arg = None
    assignment_id_arg = None

    current_arg_index = 5
    if len(sys.argv) > current_arg_index:
        if sys.argv[current_arg_index] != "NO_PASSWORD_PLACEHOLDER":
            password_arg = sys.argv[current_arg_index]
        current_arg_index += 1
    if len(sys.argv) > current_arg_index:
        if sys.argv[current_arg_index] != "NO_ASSIGNMENT_ID_PLACEHOLDER" and sys.argv[current_arg_index].isdigit():
            assignment_id_arg = int(sys.argv[current_arg_index])

    log_to_stdout(f"RUNNER_PARAMS: DeviceID='{device_id_arg}', AndroidVersion='{android_version_arg}', TestCaseID='{testcase_id_arg_str}', ExecutedByID='{executed_by_user_id_arg_str}', PasswordGiven='{'Yes' if password_arg else 'No'}', AssignmentID='{assignment_id_arg}'")

    try:
        testcase_id_arg = int(testcase_id_arg_str)
        executed_by_user_id_arg = int(executed_by_user_id_arg_str)
    except ValueError:
        log_to_stdout("RUNNER_ERROR: TestCaseID and ExecutedByUserID must be integers.")
        sys.exit(1)

    dynamic_params_json = os.environ.get('DYNAMIC_PARAMS', '{}')
    dynamic_params = json.loads(dynamic_params_json)
    log_to_stdout(f"RUNNER_PARAMS: DynamicParams='{dynamic_params}'")

    db_conn = get_runner_db_connection()
    if not db_conn:
        sys.exit(1)
    db_cursor = db_conn.cursor(dictionary=True)

    execution_overall_status = "FAIL"
    final_log_message = "Execution started but did not complete successfully."
    appium_session_started = False
    summary_stats = {'TotalSteps': 0, 'Attempted': 0, 'Passed': 0, 'Failed': 0}

    try:
        db_cursor.execute("SELECT DeviceID FROM devices WHERE SerialNumber = %s", (device_id_arg,))
        device_row = db_cursor.fetchone()
        db_device_id_for_exec = device_row['DeviceID'] if device_row else None
        if not db_device_id_for_exec:
            db_cursor.execute("INSERT INTO devices (SerialNumber, Name, OSVersion) VALUES (%s, %s, %s)",
                              (device_id_arg, f"Device_{device_id_arg}", android_version_arg))
            db_device_id_for_exec = db_cursor.lastrowid
            log_to_stdout(f"RUNNER_INFO: Created new device ID {db_device_id_for_exec} for SN {device_id_arg}")
        db_conn.commit()

        default_suite_id = 1 

        execution_start_time = datetime.now()
        initial_db_status = 'NOT EXECUTED'
        exec_params_to_store = {
            "device_id": device_id_arg, "android_version": android_version_arg,
            "dynamic_inputs": dynamic_params, "password_provided": bool(password_arg)
        }
        
        sql_insert_execution = """
            INSERT INTO testexecutions (TestCaseID, SuiteID, DeviceID, ExecutedBy, ExecutionTime, OverallStatus, Parameters)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        db_cursor.execute(sql_insert_execution, (
            testcase_id_arg, default_suite_id, db_device_id_for_exec, executed_by_user_id_arg,
            execution_start_time, initial_db_status, json.dumps(exec_params_to_store)
        ))
        current_execution_id = db_cursor.lastrowid
        db_conn.commit()
        log_to_stdout(f"RUNNER_INFO: Created TestExecutionID: {current_execution_id} status {initial_db_status}")

        log_to_stdout(f"RUNNER_INFO: Fetching steps for TestCaseID: {testcase_id_arg}")
        db_cursor.execute(
            "SELECT StepID, StepOrder, Input, ExpectedResponse, InputType, ParamName FROM steps WHERE TestCaseID = %s ORDER BY StepOrder",
            (testcase_id_arg,)
        )
        db_step_rows = db_cursor.fetchall()
        summary_stats['TotalSteps'] = len(db_step_rows)

        if not db_step_rows:
            final_log_message = f"No steps defined for TestCaseID {testcase_id_arg}."
            execution_overall_status = "FAIL"
            log_to_stdout(f"RUNNER_WARNING: {final_log_message}")
            raise ValueError(final_log_message)

        processed_steps_for_appium = []
        for r_step_db in db_step_rows:
            input_val = r_step_db['Input']
            if r_step_db.get('InputType') == 'dynamic' and r_step_db.get('ParamName'):
                param_name = r_step_db['ParamName']
                if param_name in dynamic_params:
                    input_val = dynamic_params[param_name]
                else:
                    log_to_stdout(f"RUNNER_WARNING: Dynamic param '{param_name}' for step {r_step_db['StepOrder']} not found. Using template: '{input_val}'")
            
            kws = [kw.strip() for kw in (r_step_db['ExpectedResponse'] or "").split(',') if kw.strip()]
            processed_steps_for_appium.append({
                'db_step_id': r_step_db['StepID'], 'step_order': r_step_db['StepOrder'],
                'input': input_val, 'expected_keywords': kws
            })

        log_to_stdout("RUNNER_INFO: Setting up Appium driver...")
        options = UiAutomator2Options()
        options.platform_name = 'Android'
        options.platform_version = android_version_arg
        options.device_name = device_id_arg
        options.udid = device_id_arg
        options.automation_name = 'UiAutomator2'
        # For USSD, we often don't specify appPackage/appActivity, or we use the phone/dialer app.
        # If you are testing a specific app, set these. For general USSD:
        # options.app_package = 'com.android.phone' # This might vary
        # options.app_activity = '.activities.DialtactsActivity' # This might vary
        options.no_reset = True
        options.new_command_timeout = 180 # Increased timeout

        try:
            appium_driver = webdriver.Remote('http://localhost:4723', options=options)
            appium_session_started = True
            log_to_stdout("RUNNER_INFO: Appium driver setup complete.")
        except Exception as e_appium_setup:
            log_to_stdout(f"RUNNER_ERROR: Appium driver setup failed: {e_appium_setup}")
            final_log_message = f"Appium session could not be started: {e_appium_setup}"
            raise

        screenshots_subdir = f'screenshots_exec_{current_execution_id}'
        report_dir_path_for_screenshots = os.path.join('static/reports', screenshots_subdir) # 'reports' should be in static or served
        os.makedirs(report_dir_path_for_screenshots, exist_ok=True)

        execution_overall_status = "PASS" # Assume PASS until a step fails

        for step_data in processed_steps_for_appium:
            summary_stats['Attempted'] += 1
            step_db_id = step_data['db_step_id']
            step_order = step_data['step_order']
            input_to_send = step_data['input']
            expected_kws = step_data['expected_keywords']
            
            step_status = "FAIL"
            actual_response_text = "No response captured or step failed before response."
            step_start_time_dt = datetime.now()
            db_screenshot_path = None

            log_to_stdout(f"RUNNER_STEP Start: Order={step_order}, Input='{input_to_send}', Expected KWs='{expected_kws}'")

            print(f"RUNNER_STEP Start: Order={step_order}, Input='{input_to_send}', Expected KWs='{expected_kws}'")

            try:
                if step_order == 1 and input_to_send.startswith('*') and input_to_send.endswith('#'):
                    ussd_code_encoded = input_to_send.replace('#', '%23')
                    log_to_stdout(f"RUNNER_APPIUM: Dialing USSD via 'am start': tel:{ussd_code_encoded}")
                    
                    for attempt in range(1, 10):
                            print(f"Dialing USSD code (Attempt {attempt})...", flush=True)

                            appium_driver.execute_script('mobile: shell', {
                                'command': 'am',
                                'args': ['start', '-a', 'android.intent.action.CALL', f'tel:{ussd_code_encoded}']
                            })

                            try:
                                message_element = WebDriverWait(appium_driver, 10).until(
                                    EC.presence_of_element_located((AppiumBy.ID, "com.android.phone:id/message"))
                                )
                                ussd_text = message_element.text.strip()
                    
                                if all(word.lower() in ussd_text.lower() for word in ["welcome", "Bank", "Abyssinia"]):
                                    print("Expected Home USSD page matched.", flush=True)
                                    break
                                else:
                                    cancel_ussd()
                                    time.sleep(0.3)

                            except Exception as e:
                                print(f"Error waiting for USSD response: {e}", flush=True)

                else:
                    log_to_stdout(f"RUNNER_APPIUM: Sending keys '{input_to_send}'")
                    input_field = WebDriverWait(appium_driver, 20).until( # Increased wait
                        EC.presence_of_element_located((AppiumBy.XPATH, '//android.widget.EditText'))
                    )
                    input_field.clear()
                    input_field.send_keys(input_to_send)
                    
                    send_button = WebDriverWait(appium_driver, 10).until(
                        EC.element_to_be_clickable((AppiumBy.XPATH, "//*[@text='SEND' or @text='Send' or @text='send']")) # More robust text match
                    )
                    send_button.click()
                
                time.sleep(5) 

                possible_response_elements_locators = [
                    (AppiumBy.ID, "android:id/message"),
                    (AppiumBy.ID, "com.android.phone:id/message"), # Might be specific to older OS/Phone apps
                    (AppiumBy.XPATH, "//*[contains(@resource-id, 'message') and @class='android.widget.TextView']"),
                    (AppiumBy.XPATH, "//android.widget.TextView[@displayed='true' and string-length(@text) > 0 and not(contains(@text,'SEND') or contains(@text,'Send') or contains(@text,'CANCEL') or contains(@text,'Cancel'))]") # Generic visible text, excluding buttons
                ]
                
                response_found = False
                for by_method, locator_str in possible_response_elements_locators:
                    try:
                        log_to_stdout(f"RUNNER_APPIUM: Trying to find response with: {by_method} - {locator_str}")
                        response_elements = appium_driver.find_elements(by_method, locator_str) # Use find_elements
                        if response_elements:
                            # Prefer the element with the longest text, or the first visible one
                            best_candidate_text = ""
                            for el in response_elements:
                                if el.is_displayed():
                                    current_text = el.text
                                    if current_text and len(current_text) > len(best_candidate_text):
                                        best_candidate_text = current_text
                            
                            if best_candidate_text.strip():
                                actual_response_text = best_candidate_text.strip()
                                response_found = True
                                log_to_stdout(f"RUNNER_APPIUM: Response captured: '{actual_response_text}'")
                                break
                    except Exception as e_find_resp:
                        log_to_stdout(f"RUNNER_APPIUM_DEBUG: Error finding response with {locator_str}: {e_find_resp}")
                        continue
                
                if not response_found:
                    log_to_stdout("RUNNER_APPIUM_WARN: Could not find a USSD response element reliably.")
                    actual_response_text = "No USSD response element found or text was empty."

                screenshot_filename_on_disk = f"step_{step_order}.png" # Simpler name for step
                screenshot_path_on_disk = os.path.join(report_dir_path_for_screenshots, screenshot_filename_on_disk)
                appium_driver.save_screenshot(screenshot_path_on_disk)
                db_screenshot_path = os.path.join(screenshots_subdir, screenshot_filename_on_disk).replace("\\", "/")
                log_to_stdout(f"RUNNER_APPIUM: Screenshot saved to {screenshot_path_on_disk} (DB path: {db_screenshot_path})")

                if response_matches_keywords(expected_kws, actual_response_text):
                    step_status = "PASS"
                    summary_stats['Passed'] += 1
                else:
                    step_status = "FAIL"
                    summary_stats['Failed'] += 1
                    execution_overall_status = "FAIL"
                
                log_to_stdout(f"RUNNER_STEP Result: Order={step_order}, Status={step_status}")

            except Exception as e_step:
                log_to_stdout(f"RUNNER_ERROR: Exception during Appium Step {step_order}: {e_step}")
                step_status = "FAIL"
                summary_stats['Failed'] += 1
                execution_overall_status = "FAIL"
                actual_response_text = f"Error during step execution: {e_step}"
                if appium_driver and appium_session_started:
                    try:
                        screenshot_filename_on_disk = f"step_{step_order}_ERROR.png"
                        screenshot_path_on_disk = os.path.join(report_dir_path_for_screenshots, screenshot_filename_on_disk)
                        appium_driver.save_screenshot(screenshot_path_on_disk)
                        db_screenshot_path = os.path.join('reports', screenshots_subdir, screenshot_filename_on_disk).replace("\\", "/")
                        log_to_stdout(f"RUNNER_APPIUM: Error screenshot: {db_screenshot_path}")
                        log_to_stdout(f"RUNNER_APPIUM: Screenshot saved to {screenshot_path_on_disk} (DB path for static serving: {db_screenshot_path})")
                    except Exception as e_screenshot_err:
                        log_to_stdout(f"RUNNER_APPIUM_WARN: Failed to take error screenshot: {e_screenshot_err}")
            
            step_end_time_dt = datetime.now()
            step_duration_sec = (step_end_time_dt - step_start_time_dt).total_seconds()
            step_log_message = f"Actual: '{actual_response_text}'. Expected KWs: '{','.join(expected_kws)}'. Status: {step_status}"

            db_cursor.execute(
                """INSERT INTO stepresults (ExecutionID, StepID, ActualInput, ActualOutput, Status, Screenshot, StartTime, EndTime, Duration, LogMessage)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (current_execution_id, step_db_id, input_to_send, actual_response_text, step_status,
                 db_screenshot_path, step_start_time_dt, step_end_time_dt, round(step_duration_sec, 3), step_log_message)
            )
            db_conn.commit()
            log_to_stdout(f"RUNNER_DB: Logged result for StepID {step_db_id} (Order: {step_order})")

            if step_status == "FAIL" and execution_overall_status == "FAIL":
                 log_to_stdout(f"RUNNER_INFO: Step {step_order} failed. Aborting further steps.")
                 break 

        if summary_stats['Attempted'] == 0 and summary_stats['TotalSteps'] > 0:
            execution_overall_status = "FAIL"
            final_log_message = "No steps were attempted, possibly due to early Appium failure."
        elif summary_stats['Failed'] > 0:
            execution_overall_status = "FAIL"
        elif summary_stats['TotalSteps'] > 0 and summary_stats['Passed'] == summary_stats['TotalSteps']: # All defined steps attempted and passed
            execution_overall_status = "PASS"
        # If summary_stats['TotalSteps'] == 0, it's handled earlier by raising ValueError.

        final_log_message = f"Execution completed. Total Defined Steps: {summary_stats['TotalSteps']}. Attempted: {summary_stats['Attempted']}, Passed: {summary_stats['Passed']}, Failed: {summary_stats['Failed']}."

    except Exception as e_main_flow:
        log_to_stdout(f"RUNNER_CRITICAL_ERROR: Main execution flow error: {e_main_flow}")
        import traceback
        log_to_stdout(traceback.format_exc())
        execution_overall_status = "FAIL"
        if final_log_message == "Execution started but did not complete successfully.": # If not already set by specific error
            final_log_message = f"Critical error during execution: {e_main_flow}"
    finally:
        dialog_dismissed = False # Flag to track if we successfully dismissed a dialog

        if appium_driver and appium_session_started: # Ensure driver exists and session was started
            try:
                log_to_stdout("RUNNER_INFO: Attempting to close any open USSD dialog before quitting driver...")
                time.sleep(1) # Brief pause for UI to settle if test ended abruptly

                # Attempt 1: Look for "Cancel" or "Dismiss" type buttons
                cancel_dismiss_xpaths = [
                    "//*[@class='android.widget.Button' and (contains(@text, 'Cancel') or contains(@text, 'CANCEL'))]",
                    "//*[@class='android.widget.Button' and (contains(@text, 'Dismiss') or contains(@text, 'DISMISS'))]"
                    # Add other variations like "إلغاء" if you have other languages
                ]
                
                for xpath in cancel_dismiss_xpaths:
                    if dialog_dismissed: break # No need to search further if already dismissed
                    try:
                        buttons = appium_driver.find_elements(AppiumBy.XPATH, xpath)
                        for btn in buttons:
                            if btn.is_displayed():
                                log_to_stdout(f"RUNNER_INFO: Found dialog button (Cancel/Dismiss type) with text '{btn.text}'. Clicking.")
                                btn.click()
                                time.sleep(1.5) 
                                dialog_dismissed = True
                                break 
                    except Exception: # Catch StaleElementReference or NoSuchElement
                        pass # Try next xpath or next button type

                # Attempt 2: If not dismissed, look for "OK" type buttons (often for informational dialogs)
                if not dialog_dismissed:
                    log_to_stdout("RUNNER_INFO: Cancel/Dismiss not found or clicked. Looking for OK buttons...")
                    ok_xpaths = [
                        "//*[@class='android.widget.Button' and (contains(@text, 'OK') or contains(@text, 'Ok') or text()='OK' or text()='ok')]",
                        # Add other variations like "موافق"
                    ]
                    for xpath in ok_xpaths:
                        if dialog_dismissed: break
                        try:
                            buttons = appium_driver.find_elements(AppiumBy.XPATH, xpath)
                            for btn in buttons:
                                if btn.is_displayed():
                                    log_to_stdout(f"RUNNER_INFO: Found dialog button (OK type) with text '{btn.text}'. Clicking.")
                                    btn.click()
                                    time.sleep(1.5)
                                    dialog_dismissed = True
                                    break
                        except Exception:
                            pass
                
                # Attempt 3: If still not dismissed by specific buttons, try pressing the BACK key
                if not dialog_dismissed:
                    log_to_stdout("RUNNER_WARN: No standard dialog dismissal/OK buttons found or clicked. Trying KEYCODE_BACK.")
                    try:
                        appium_driver.press_keycode(4) # Android Keycode for BACK
                        time.sleep(0.5)
                        # Optional: A second back press if one isn't always enough
                        # appium_driver.press_keycode(4) 
                        # time.sleep(0.5)
                        log_to_stdout("RUNNER_INFO: Sent KEYCODE_BACK.")
                        # We can't easily confirm dialog_dismissed with BACK key, so we assume it might have worked.
                    except Exception as e_back:
                        log_to_stdout(f"RUNNER_WARN: Error pressing KEYCODE_BACK: {e_back}")
                elif dialog_dismissed:
                     log_to_stdout("RUNNER_INFO: USSD Dialog likely dismissed by button click.")


            except Exception as e_close_dialog:
                log_to_stdout(f"RUNNER_WARN: General exception during attempt to close USSD dialog: {e_close_dialog}")

        if appium_driver:
            try:
                appium_driver.quit()
                log_to_stdout("RUNNER_INFO: Appium driver quit.")
            except Exception as e_quit:
                log_to_stdout(f"RUNNER_WARN: Error quitting Appium driver: {e_quit}")

        if current_execution_id and db_conn and db_cursor:
            try:
                # Ensure final_log_message is not excessively long for the DB field
                db_final_log_message = (final_log_message[:1990] + '...') if len(final_log_message) > 1990 else final_log_message
                db_cursor.execute("UPDATE testexecutions SET OverallStatus = %s, LogMessage = %s WHERE ExecutionID = %s",
                                  (execution_overall_status, db_final_log_message, current_execution_id))
                db_conn.commit()
                log_to_stdout(f"RUNNER_DB: Final TestExecutionID {current_execution_id} status: {execution_overall_status}. Log: {db_final_log_message}")
            except Exception as e_db_final:
                log_to_stdout(f"RUNNER_ERROR: Failed to update final execution status for ID {current_execution_id}: {e_db_final}")

        if assignment_id_arg and current_execution_id and db_conn and db_cursor:
            assignment_final_db_status = "EXECUTED_FAIL"
            if execution_overall_status == "PASS": assignment_final_db_status = "EXECUTED_PASS"
            elif execution_overall_status == "FAIL": assignment_final_db_status = "EXECUTED_FAIL"
            
            try:
                db_cursor.execute(
                    "UPDATE test_assignments SET Status = %s, ExecutionID = %s WHERE AssignmentID = %s AND AssignedToUserID = %s",
                    (assignment_final_db_status, current_execution_id, assignment_id_arg, executed_by_user_id_arg)
                )
                db_conn.commit()
                log_to_stdout(f"RUNNER_DB: AssignmentID {assignment_id_arg} status updated to {assignment_final_db_status}.")
            except Exception as e_assign_final:
                log_to_stdout(f"RUNNER_ERROR: Failed to update final assignment status for ID {assignment_id_arg}: {e_assign_final}")

        if db_cursor: db_cursor.close()
        if db_conn and db_conn.is_connected(): db_conn.close(); log_to_stdout("RUNNER_INFO: Database connection closed.") 

        log_to_stdout(f"RUNNER_INFO: generic_runner.py finished. OverallStatus: {execution_overall_status}.")
        sys.exit(0)

if __name__ == "__main__":
    main_runner()