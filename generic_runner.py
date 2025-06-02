# generic_runner.py

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
    if not expected_keywords_list:
        return True

    return all(keyword.strip().lower() in actual_lower for keyword in expected_keywords_list if keyword.strip())

def detect_current_step(actual_response, all_processed_steps):

    if not actual_response or not all_processed_steps:
        log_to_stdout("DETECT_STEP_DEBUG: No actual_response or all_processed_steps provided.")
        print("DETECT_STEP_DEBUG: No actual_response or all_processed_steps provided.")
        return None

    valid_steps_for_detection = []
    for step in all_processed_steps:
        if isinstance(step.get("expected_keywords"), list) and isinstance(step.get("step_order"), int):
            valid_steps_for_detection.append(step)
        #else:
            #log_to_stdout(f"DETECT_STEP_DEBUG: Skipping step due to invalid format: {step.get('step_order')}")

    
    sorted_by_step_order = sorted(valid_steps_for_detection, key=lambda s: s["step_order"])
    # Ensure keywords exist before len()
    sorted_steps_for_detection = sorted(
        sorted_by_step_order, 
        key=lambda s: len(s["expected_keywords"]) if s["expected_keywords"] else 0, 
        reverse=True
    )

    for step_info in sorted_steps_for_detection:
        if response_matches_keywords(step_info["expected_keywords"], actual_response):
            return step_info["step_order"]
    return None

def perform_adaptive_ussd_navigation_and_detection(appium_driver_instance, all_processed_steps_list, current_response_locators, base_screenshots_dir_path, failed_step_order):

    log_to_stdout(f"RUNNER_ADAPTIVE: Mismatch on step {failed_step_order}. Initiating adaptive '*' navigation and detection.")
    
    detected_step_order_after_adaptive = None
    adaptive_actual_response_text = "No adaptive response captured." # Default

    try:
        # Attempt to send '*'
        log_to_stdout(f"RUNNER_APPIUM_ADAPTIVE: Attempting to send keys '*'")
        input_field_adaptive = None
        try:

            input_field_elements = appium_driver_instance.find_elements(AppiumBy.XPATH, '//android.widget.EditText')
            if input_field_elements:
                input_field_adaptive = input_field_elements[0] # Take the first one
                if input_field_adaptive.is_displayed():
                    input_field_adaptive.clear()
                    input_field_adaptive.send_keys('*')
                    log_to_stdout(f"RUNNER_APPIUM_ADAPTIVE: Sent '*' to input field.")
                else:
                    input_field_adaptive = None 
                    log_to_stdout(f"RUNNER_APPIUM_ADAPTIVE_WARN: Input field found but not displayed. Cannot send '*'.")
            else:
                log_to_stdout(f"RUNNER_APPIUM_ADAPTIVE_WARN: No EditText field found to send '*'.")

        except Exception as e_input_field:
            log_to_stdout(f"RUNNER_APPIUM_ADAPTIVE_WARN: Error interacting with input field for '*': {e_input_field}. Reading current screen state.")

        if input_field_adaptive: 
            try:
               
                send_button_adaptive = WebDriverWait(appium_driver_instance, 7).until(
                    EC.element_to_be_clickable((AppiumBy.XPATH, "//*[@text='SEND' or @text='Send' or @text='send']"))
                )
                send_button_adaptive.click()
                log_to_stdout(f"RUNNER_APPIUM_ADAPTIVE: Clicked SEND after typing '*'.")
                time.sleep(3) 
            except Exception as e_send_button:
                log_to_stdout(f"RUNNER_APPIUM_ADAPTIVE_WARN: Could not click SEND button after '*': {e_send_button}. Reading current screen state.")
                time.sleep(1)
        else: 
             log_to_stdout(f"RUNNER_APPIUM_ADAPTIVE_INFO: Skipping SEND click as '*' was not entered.")
             time.sleep(1) 
       
        adaptive_response_found = False
        for by_method, locator_str in current_response_locators:
            try:
                adaptive_response_elements = WebDriverWait(appium_driver_instance, 5).until(
                    EC.presence_of_all_elements_located((by_method, locator_str))
                )
                if adaptive_response_elements:
                    best_adaptive_text = ""
                    for el in adaptive_response_elements:
                        if el.is_displayed():
                            current_text = el.text
                            if current_text and len(current_text) > len(best_adaptive_text): # Prefer longer, likely more complete, text
                                best_adaptive_text = current_text
                    
                    if best_adaptive_text.strip():
                        adaptive_actual_response_text = best_adaptive_text.strip()
                        adaptive_response_found = True
                        log_to_stdout(f"RUNNER_APPIUM_ADAPTIVE: Response after adaptive action: '{adaptive_actual_response_text}'")
                        break # Found a response
            except Exception: 
                continue 
        
        if not adaptive_response_found:
            log_to_stdout("RUNNER_APPIUM_WARN_ADAPTIVE: Could not find a USSD response element after adaptive action using configured locators.")
            print("RUNNER_APPIUM_WARN_ADAPTIVE: Could not find a USSD response element after adaptive action using configured locators.")
            # adaptive_actual_response_text will retain its default or last successfully captured value.

        # Detect current page/step using the updated detect_current_step function
        detected_step_order_after_adaptive = detect_current_step(adaptive_actual_response_text, all_processed_steps_list)

        if detected_step_order_after_adaptive is not None:
            log_to_stdout(f"RUNNER_ADAPTIVE_DETECT: After adaptive action, USSD page matches keywords for step_order: {detected_step_order_after_adaptive}.")
            print(f"RUNNER_ADAPTIVE_DETECT: After adaptive action, USSD page matches keywords for step_order: {detected_step_order_after_adaptive}.")
        else:
            log_to_stdout(f"RUNNER_ADAPTIVE_DETECT: After adaptive action, could not identify USSD page based on known step keywords. Final adaptive response: '{adaptive_actual_response_text}'")
            print(f"RUNNER_ADAPTIVE_DETECT: After adaptive action, could not identify USSD page based on known step keywords. Final adaptive response: '{adaptive_actual_response_text}'")
            return

        # Take a screenshot after adaptive action, if screenshot directory is valid
        if base_screenshots_dir_path and os.path.isdir(base_screenshots_dir_path):
            try:
                adaptive_screenshot_filename = f"step_{failed_step_order}_adaptive_action_response.png"
                adaptive_screenshot_path_on_disk = os.path.join(base_screenshots_dir_path, adaptive_screenshot_filename)
                appium_driver_instance.save_screenshot(adaptive_screenshot_path_on_disk)
                log_to_stdout(f"RUNNER_APPIUM_ADAPTIVE: Screenshot after adaptive action saved to: {adaptive_screenshot_path_on_disk}")
            except Exception as e_screenshot_adaptive:
                log_to_stdout(f"RUNNER_APPIUM_WARN_ADAPTIVE: Failed to take screenshot after adaptive action: {e_screenshot_adaptive}")
        
        return detected_step_order_after_adaptive, adaptive_actual_response_text

    except Exception as e_adaptive_action:
        log_to_stdout(f"RUNNER_ERROR_ADAPTIVE: Critical exception during adaptive action for step {failed_step_order}: {e_adaptive_action}")
        # Return current state even on error
        return None, adaptive_actual_response_text


def cancel_ussd():
    # (Your existing cancel_ussd function - no changes needed here for this task)
    try:
        cancel_button_locator = (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("Cancel")') # Case-sensitive
        # More robust: new UiSelector().textMatches("(?i)Cancel") for case-insensitive
        
        # Try a few common variations for cancel
        cancel_locators = [
            (AppiumBy.XPATH, "//*[@text='Cancel' or @text='CANCEL' or @text='cancel']"),
            (AppiumBy.XPATH, "//*[@text='Dismiss' or @text='DISMISS' or @text='dismiss']")
        ]

        cancelled = False
        for by, loc_val in cancel_locators:
            try:
                cancel_button = WebDriverWait(appium_driver, 3).until(EC.element_to_be_clickable((by, loc_val)))
                cancel_button.click()
                log_to_stdout("USSD session cancelled successfully via adaptive cancel.", flush=True)
                cancelled = True
                break
            except Exception:
                continue # Try next locator
        
        if not cancelled:
            log_to_stdout("Could not find a standard Cancel/Dismiss button for USSD.", flush=True)
            # As a last resort, try pressing back key
            try:
                log_to_stdout("Attempting to cancel USSD via KEYCODE_BACK.", flush=True)
                appium_driver.press_keycode(4) # Android Keycode for BACK
                time.sleep(0.5)
            except Exception as e_back:
                log_to_stdout(f"Error pressing KEYCODE_BACK during cancel_ussd: {e_back}", flush=True)


    except Exception as e:
        log_to_stdout(f"Failed to cancel USSD session during adaptive logic: {e}", flush=True)


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

    execution_overall_status = "PASS"
    final_log_message = "Execution started but did not complete successfully."
    appium_session_started = False
    summary_stats = {'TotalSteps': 0, 'Attempted': 0, 'Passed': 0, 'Failed': 0}
    override_response_text_for_current_iteration = None
    
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
        options.no_reset = True
        options.new_command_timeout = 180 

        try:
            appium_driver = webdriver.Remote('http://localhost:4723', options=options)
            appium_session_started = True
            log_to_stdout("RUNNER_INFO: Appium driver setup complete.")
        except Exception as e_appium_setup:
            log_to_stdout(f"RUNNER_ERROR: Appium driver setup failed: {e_appium_setup}")
            final_log_message = f"Appium session could not be started: {e_appium_setup}"
            execution_overall_status = "FAIL"
            raise

        screenshots_subdir = f'screenshots_exec_{current_execution_id}'
        base_report_dir = 'static/reports' if os.path.exists('static/reports') else 'reports'
        if not os.path.exists(base_report_dir): os.makedirs(base_report_dir, exist_ok=True)
        report_dir_path_for_screenshots = os.path.join(base_report_dir, screenshots_subdir) 
        os.makedirs(report_dir_path_for_screenshots, exist_ok=True)

        current_step_index = 0
        max_adaptive_jumps_total = 5 
        adaptive_jump_count = 0
        hard_fail_occurred_in_loop = False 

        while current_step_index < len(processed_steps_for_appium):
            step_data = processed_steps_for_appium[current_step_index]
            summary_stats['Attempted'] += 1
            step_db_id = step_data['db_step_id']
            step_order = step_data['step_order']
            input_to_send = step_data['input']
            expected_kws = step_data['expected_keywords']
            
            step_status = "FAIL"
            actual_response_text = "No response captured or step failed before response."
            step_start_time_dt = datetime.now()
            db_screenshot_path = None
            step_log_message_details = []

            log_to_stdout(f"RUNNER_STEP Start: Order={step_order} (Index: {current_step_index}), Input='{input_to_send}', Expected KWs='{expected_kws}'")

            try:
                response_found = False
                if override_response_text_for_current_iteration:
                    actual_response_text = override_response_text_for_current_iteration
                    response_found = True
                    log_to_stdout(f"RUNNER_INFO: Using pre-captured response for step {step_order}: '{actual_response_text[:100]}...'")
                    step_log_message_details.append(f"Starting with pre-captured response from adaptive action: '{actual_response_text[:100]}...'")
                    override_response_text_for_current_iteration = None
                else:
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
                                    # cancel_ussd()
                                    # time.sleep(0.3)
                                    input_field = WebDriverWait(appium_driver, 20).until( # Increased wait
                                        EC.presence_of_element_located((AppiumBy.XPATH, '//android.widget.EditText'))
                                    )
                                    input_field.clear()
                                    input_field.send_keys("*")
                                    time.sleep(0.5)
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
                        input_field = WebDriverWait(appium_driver, 20).until(
                            EC.presence_of_element_located((AppiumBy.XPATH, '//android.widget.EditText'))
                        )
                        input_field.clear()
                        input_field.send_keys(input_to_send)
                        send_button = WebDriverWait(appium_driver, 10).until(
                            EC.element_to_be_clickable((AppiumBy.XPATH, "//*[@text='SEND' or @text='Send' or @text='send']"))
                        )
                        send_button.click()
                    
                    if not response_found:
                        time.sleep(5)
                        possible_response_elements_locators = [
                            (AppiumBy.ID, "android:id/message"),
                            (AppiumBy.ID, "com.android.phone:id/message"),
                        ]
                        temp_actual_response_text = "No USSD response element found or text was empty."
                        for by_method, locator_str in possible_response_elements_locators:
                            try:
                                response_elements = WebDriverWait(appium_driver, 7).until(
                                    EC.presence_of_all_elements_located((by_method, locator_str))
                                )
                                if response_elements:
                                    best_candidate_text = ""
                                    for el in response_elements:
                                        if el.is_displayed():
                                            current_text = el.text
                                            if current_text and len(current_text) > len(best_candidate_text):
                                                best_candidate_text = current_text
                                    if best_candidate_text.strip():
                                        temp_actual_response_text = best_candidate_text.strip()
                                        response_found = True
                                        log_to_stdout(f"RUNNER_APPIUM: Response captured: '{temp_actual_response_text}'")
                                        break 
                            except Exception:
                                continue
                        actual_response_text = temp_actual_response_text

                if not response_found and actual_response_text == "No USSD response element found or text was empty.":
                    log_to_stdout(f"RUNNER_APPIUM_WARN: Could not find a USSD response element reliably for step {step_order}.")
                    step_log_message_details.append("Failed to find/capture USSD response text.")

                screenshot_filename_on_disk = f"step_{step_order}_{current_step_index}_main.png"
                screenshot_path_on_disk = os.path.join(report_dir_path_for_screenshots, screenshot_filename_on_disk)
                appium_driver.save_screenshot(screenshot_path_on_disk)
                db_screenshot_path = os.path.join(screenshots_subdir, screenshot_filename_on_disk).replace("\\", "/")
                log_to_stdout(f"RUNNER_APPIUM: Main screenshot for step {step_order} (Attempt index {current_step_index}) saved to {db_screenshot_path}")

                if response_matches_keywords(expected_kws, actual_response_text):
                    step_status = "PASS"
                    summary_stats['Passed'] += 1
                    step_log_message_details.append(f"Matched expected keywords. Actual: '{actual_response_text}'.")
                    current_step_index += 1
                else:
                    step_status = "FAIL"
                    summary_stats['Failed'] += 1 

                    mismatch_log = f"Expected keywords '{','.join(expected_kws)}' NOT found in response: '{actual_response_text}'."
                    step_log_message_details.append(f"MISMATCH: {mismatch_log}")
                    log_to_stdout(f"RUNNER_STEP_MISMATCH: Step {step_order} - {mismatch_log}")

                    if adaptive_jump_count >= max_adaptive_jumps_total:
                        log_to_stdout(f"RUNNER_ADAPTIVE: Max adaptive jumps ({max_adaptive_jumps_total}) reached. Test will fail.")
                        hard_fail_occurred_in_loop = True
                        current_step_index +=1 
                    else:
                        detected_adaptive_step_order, adaptive_response = perform_adaptive_ussd_navigation_and_detection(
                            appium_driver,
                            processed_steps_for_appium,
                            possible_response_elements_locators, 
                            report_dir_path_for_screenshots,
                            step_order
                        )
                        step_log_message_details.append(f"Adaptive Action: Sent '*'. New Response: '{adaptive_response}'. Detected Page for Step: {detected_adaptive_step_order if detected_adaptive_step_order is not None else 'Unknown'}.")
                        
                        if detected_adaptive_step_order is not None:
                            target_index = -1
                            for i, s_info in enumerate(processed_steps_for_appium):
                                if s_info['step_order'] == detected_adaptive_step_order:
                                    target_index = i
                                    break
                            
                            if target_index != -1 and target_index < current_step_index :
                                log_to_stdout(f"RUNNER_ADAPTIVE_JUMP: Mismatch on step {step_order}. Attempting to jump to step {detected_adaptive_step_order} (index {target_index}).")
                                current_step_index = target_index
                                override_response_text_for_current_iteration = adaptive_response
                                adaptive_jump_count += 1
                            else:
                                log_to_stdout(f"RUNNER_ADAPTIVE_NO_JUMP: Detected step {detected_adaptive_step_order} is not a valid earlier step or same. Current step {step_order} fails definitively.")
                                hard_fail_occurred_in_loop = True
                                current_step_index += 1
                        else:
                            log_to_stdout(f"RUNNER_ADAPTIVE_FAIL: Adaptive navigation did not identify a known step after failure on step {step_order}. Current step fails definitively.")
                            hard_fail_occurred_in_loop = True
                            current_step_index += 1
                
                log_to_stdout(f"RUNNER_STEP Result: Order={step_order}, Status={step_status}")

            except Exception as e_step:
                log_to_stdout(f"RUNNER_ERROR: Exception during Appium Step {step_order} (Index {current_step_index}): {e_step}")
                # log_to_stdout(traceback.format_exc()) # Log full traceback for step errors
                step_status = "FAIL" 
                hard_fail_occurred_in_loop = True
                summary_stats['Failed'] += 1
                
                actual_response_text_on_error = f"Error during step execution: {e_step}"
                step_log_message_details.append(f"CRITICAL_ERROR: {actual_response_text_on_error}")
                actual_response_text = actual_response_text_on_error
                if appium_driver and appium_session_started:
                    try:
                        error_screenshot_filename = f"step_{step_order}_{current_step_index}_ERROR.png"
                        error_screenshot_path_on_disk = os.path.join(report_dir_path_for_screenshots, error_screenshot_filename)
                        appium_driver.save_screenshot(error_screenshot_path_on_disk)
                        db_error_screenshot_path = os.path.join(screenshots_subdir, error_screenshot_filename).replace("\\", "/")
                        log_to_stdout(f"RUNNER_APPIUM: Error screenshot for step {step_order} saved to: {db_error_screenshot_path}")
                        if not db_screenshot_path: 
                            db_screenshot_path = db_error_screenshot_path 
                    except Exception as e_screenshot_err:
                        log_to_stdout(f"RUNNER_APPIUM_WARN: Failed to take error screenshot: {e_screenshot_err}")
                current_step_index += 1
            
            step_end_time_dt = datetime.now()
            step_duration_sec = (step_end_time_dt - step_start_time_dt).total_seconds()
            final_step_log_message = f"Status: {step_status}. Expected KWs: '{','.join(expected_kws) if expected_kws else 'N/A'}'. " + " | ".join(step_log_message_details)
            final_step_log_message_truncated = (final_step_log_message[:1990] + '...') if len(final_step_log_message) > 1990 else final_step_log_message

            db_cursor.execute(
                """INSERT INTO stepresults (ExecutionID, StepID, ActualInput, ActualOutput, Status, Screenshot, StartTime, EndTime, Duration, LogMessage)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (current_execution_id, step_db_id, input_to_send, actual_response_text, step_status,
                 db_screenshot_path, step_start_time_dt, step_end_time_dt, round(step_duration_sec, 3), final_step_log_message_truncated)
            )
            db_conn.commit()
            log_to_stdout(f"RUNNER_DB: Logged result for StepID {step_db_id} (Order: {step_order}) with status {step_status}")

            if hard_fail_occurred_in_loop:
                log_to_stdout(f"RUNNER_INFO: Unrecoverable failure occurred (Step Order: {step_order}, Status: {step_status}). Aborting test execution loop.")
                break 

            if step_status == "FAIL" and not override_response_text_for_current_iteration: 
                log_to_stdout(f"RUNNER_INFO: Step {step_order} failed, and no successful adaptive jump to an earlier step was made. Aborting test execution loop.")
                hard_fail_occurred_in_loop = True 
                break 

        log_to_stdout("RUNNER_INFO: Main step execution loop completed.")

        if hard_fail_occurred_in_loop:
            execution_overall_status = "FAIL"
            final_log_message = f"Execution failed due to an unrecoverable error or persistent step failure. Defined: {summary_stats['TotalSteps']}."
        elif summary_stats['Attempted'] == 0 and summary_stats['TotalSteps'] > 0:
            execution_overall_status = "FAIL"
            final_log_message = "No steps were attempted, possibly due to early Appium failure."
        elif summary_stats['TotalSteps'] == 0:
            execution_overall_status = "FAIL"
            final_log_message = f"No steps defined for TestCaseID {testcase_id_arg}."
        else:
            log_to_stdout("RUNNER_INFO: Verifying final status of all defined steps from database...")
            all_defined_steps_passed_eventually = True
            if current_execution_id: # Ensure we have an execution ID to query
                for step_meta_info in processed_steps_for_appium:
                    db_step_id_to_verify = step_meta_info['db_step_id']
                    db_cursor.execute(
                        """SELECT Status FROM stepresults 
                           WHERE ExecutionID = %s AND StepID = %s 
                           ORDER BY EndTime DESC LIMIT 1""",
                        (current_execution_id, db_step_id_to_verify)
                    )
                    last_attempt = db_cursor.fetchone()

                    if not last_attempt:
                        log_to_stdout(f"RUNNER_VERIFY_FAIL: StepID {db_step_id_to_verify} (Order: {step_meta_info['step_order']}) has no recorded result. Overall FAIL.")
                        all_defined_steps_passed_eventually = False
                        break
                    elif last_attempt['Status'] != 'PASS':
                        log_to_stdout(f"RUNNER_VERIFY_FAIL: StepID {db_step_id_to_verify} (Order: {step_meta_info['step_order']}) last recorded attempt was '{last_attempt['Status']}'. Overall FAIL.")
                        all_defined_steps_passed_eventually = False
                        break
            else: # Should not happen if execution record was created
                all_defined_steps_passed_eventually = False
                log_to_stdout("RUNNER_VERIFY_ERROR: current_execution_id is not set for final verification.")
            
            if all_defined_steps_passed_eventually:
                execution_overall_status = "PASS"
                final_log_message = f"Execution completed successfully. All {summary_stats['TotalSteps']} defined steps ultimately passed."
            else:
                execution_overall_status = "FAIL"
                final_log_message = f"Execution completed with failures. Not all defined steps ultimately passed. Check step results."
        
    except Exception as e_main_flow:
        log_to_stdout(f"RUNNER_CRITICAL_ERROR: Main execution flow error: {e_main_flow}")
        # log_to_stdout(traceback.format_exc())
        execution_overall_status = "FAIL"
        if "Execution started but did not complete successfully." in final_log_message or not final_log_message:
            final_log_message = f"Critical error during execution: {e_main_flow}"

    finally:
        dialog_dismissed = False 
        if appium_driver and appium_session_started: 
            try:
                log_to_stdout("RUNNER_INFO: Attempting to close any open USSD dialog before quitting driver...")
                time.sleep(1) 
                cancel_dismiss_xpaths = [
                    "//*[@class='android.widget.Button' and (contains(@text, 'Cancel') or contains(@text, 'CANCEL'))]",
                    "//*[@class='android.widget.Button' and (contains(@text, 'Dismiss') or contains(@text, 'DISMISS'))]"
                ]
                for xpath in cancel_dismiss_xpaths:
                    if dialog_dismissed: break 
                    try:
                        buttons = appium_driver.find_elements(AppiumBy.XPATH, xpath)
                        for btn in buttons:
                            if btn.is_displayed():
                                log_to_stdout(f"RUNNER_INFO: Found dialog button (Cancel/Dismiss type) with text '{btn.text}'. Clicking.")
                                btn.click()
                                time.sleep(1.5) 
                                dialog_dismissed = True
                                break 
                    except Exception: 
                        pass 
                if not dialog_dismissed:
                    log_to_stdout("RUNNER_INFO: Cancel/Dismiss not found or clicked. Looking for OK buttons...")
                    ok_xpaths = [
                        "//*[@class='android.widget.Button' and (contains(@text, 'OK') or contains(@text, 'Ok') or text()='OK' or text()='ok')]",
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
                if not dialog_dismissed:
                    log_to_stdout("RUNNER_WARN: No standard dialog dismissal/OK buttons found or clicked. Trying KEYCODE_BACK.")
                    try:
                        if appium_driver: appium_driver.press_keycode(4) 
                        time.sleep(0.5)
                        log_to_stdout("RUNNER_INFO: Sent KEYCODE_BACK.")
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
                db_final_log_message = (final_log_message[:1990] + '...') if len(final_log_message) > 1990 else final_log_message
                db_cursor.execute("UPDATE testexecutions SET OverallStatus = %s, LogMessage = %s WHERE ExecutionID = %s",
                                  (execution_overall_status, db_final_log_message, current_execution_id))
                db_conn.commit()
                log_to_stdout(f"RUNNER_DB: Final TestExecutionID {current_execution_id} status: {execution_overall_status}. Log: '{db_final_log_message}'")
            except Exception as e_db_final:
                log_to_stdout(f"RUNNER_ERROR: Failed to update final execution status for ID {current_execution_id}: {e_db_final}")

        if assignment_id_arg and current_execution_id and db_conn and db_cursor:
            assignment_final_db_status = "EXECUTED_FAIL"
            if execution_overall_status == "PASS": 
                assignment_final_db_status = "EXECUTED_PASS"
            
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
        if db_conn and db_conn.is_connected(): 
            db_conn.close()
            log_to_stdout("RUNNER_INFO: Database connection closed.") 

        log_to_stdout(f"RUNNER_INFO: generic_runner.py finished. OverallStatus: {execution_overall_status}.")
        sys.exit(0 if execution_overall_status == "PASS" else 1) # Exit with 0 for PASS, 1 for FAIL


if __name__ == "__main__":
    main_runner()


# appium --allow-insecure adb_shell