#!/usr/bin/env python3
import subprocess
import sys

def get_connected_device():
    try:
        result = subprocess.run(['adb', 'devices'], capture_output=True, text=True, check=True)
        output_lines = result.stdout.strip().split('\n')
        if len(output_lines) > 1:
            device_line = output_lines[1].split('\t')
            if len(device_line) == 2 and device_line[1] == 'device':
                return device_line[0]
        return None
    except subprocess.CalledProcessError as e:
        print(f"Error running ADB devices: {e}")
        return None

def get_android_version(device_id):
    try:
        result = subprocess.run(['adb', '-s', device_id, 'shell', 'getprop', 'ro.build.version.release'],
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error getting Android version for {device_id}: {e}")
        return None

def run_adb_shell(cmd_args):
    """
    Runs an ADB shell command and returns its stdout (trimmed).
    """
    try:
        result = subprocess.run(
            ["adb", "shell"] + cmd_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error running adb shell {' '.join(cmd_args)}:\n{e.stderr}", file=sys.stderr)
        sys.exit(1)

def resolve_dialer_component():
    """
    First tries the modern `cmd package resolve-activity` API; if that fails
    (older Android), falls back to `pm resolve-activity`.
    Returns the raw resolution output.
    """
    # Try the newer `cmd` interface
    try:
        return run_adb_shell([
            "cmd", "package", "resolve-activity",
            "--brief",
            "-a", "android.intent.action.DIAL"
        ])
    except SystemExit:
        # Fallback to pm
        return run_adb_shell([
            "pm", "resolve-activity",
            "-a", "android.intent.action.DIAL",
            "-c", "android.intent.category.DEFAULT"
        ])

def parse_component(component_str):
    """
    Given a component string like:
      com.android.dialer/.DialtactsActivity
    or
      com.android.contacts/.activities.DialtactsActivity
    returns a tuple (package, fully.qualified.ActivityName).
    """
    # split package and activity
    if "/" not in component_str:
        raise ValueError(f"Unexpected format: {component_str!r}")
    pkg, act = component_str.split("/", 1)
    # if activity is short (starts with '.'), prepend the package
    if act.startswith("."):
        act = pkg + act
    return pkg, act

def get_dialer_info():
    raw = resolve_dialer_component()
    # some devices print multiple lines; pick the last line containing '/'
    lines = [line for line in raw.splitlines() if "/" in line]
    if not lines:
        raise RuntimeError("Could not find any resolved component in:\n" + raw)
    return parse_component(lines[-1].strip())

def main():
    pkg, main_activity = get_dialer_info()
    device_id = get_connected_device()
    android_version = get_android_version(device_id)
    print(f"Default dialer package: {pkg}")
    print(f"Default dialer main activity: {main_activity}")
    print(f"Device Id: {device_id}")
    print(f"Android Version: {android_version}")

if __name__ == "__main__":
    main()
