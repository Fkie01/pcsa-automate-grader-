import datetime
import json
import os
import re
import subprocess
import time

# Assuming these are defined in your docker_container.py
from docker_container import (
    build_project,
    exec_in_container,
    restart_server,
    start_container,
    stop_container,
)

CONTAINER_NAME = "grader-test"
CONTAINER_SAMPLES = "/grader/samples"
CONTAINER_CGI = "/grader/cgi"

# --- RECOVERY UTILITIES ---


# def restart_server():
#     """Soft restart: Kills the process and restarts it within the same container."""
#     print("  ⚠ Server not responding — attempting soft restart...")
#     exec_in_container("pkill -f icws 2>/dev/null || true")
#     time.sleep(2)
#     exec_in_container(
#         f"nohup ./icws --port 9000 "
#         f"--root {CONTAINER_SAMPLES} "
#         f"--numThreads 32 --timeout 5 "
#         f"--cgiHandler {CONTAINER_CGI}/dispatcher.py "
#         f"> server.log 2>&1 &"
#     )
#     # Wait for port to open
#     for _ in range(20):
#         out, _, _ = exec_in_container("ss -ltn | grep 9000")
#         if out:
#             print("  ✅ Server process restarted")
#             return True
#         time.sleep(0.5)
#     return False


def force_restart_container(project_path="."):
    """Hard restart: Nukes the container, rebuilds, and restarts everything."""
    print(f"  🚨 CRITICAL: Container '{CONTAINER_NAME}' unresponsive.")
    print("  🔄 Performing HARD RESTART (Container + Build)...")

    stop_container()  # From your docker utility
    time.sleep(1)
    start_container(project_path)  # From your docker utility

    if build_project():  # From your docker utility
        return restart_server()
    return False


# --- HELPER FUNCTIONS ---


def is_server_up():
    out, _, _ = exec_in_container(
        "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/index.html"
    )
    return "200" in out


def normalize_http_response(resp: str):
    lines = resp.splitlines()
    filtered = []
    for line in lines:
        line = line.strip()
        if not line or any(x in line for x in ["*", ">", "{", "%", "Dload"]):
            continue
        if "Re-using existing connection" in line:
            return "REUSED_CONNECTION"
        if line.startswith("< "):
            line = line[2:]
        if any(line.startswith(h) for h in ["Date:", "Last-Modified:", "Server:"]):
            continue
        filtered.append(line)
    if not filtered:
        return ""
    status = filtered[0]
    headers = sorted(filtered[1:], key=lambda x: x.lower())
    return "\n".join([status] + headers).strip()


def extract_status_code(resp: str):
    match = re.search(r"HTTP/[\d.]+\s+(\d{3})", resp)
    return match.group(1) if match else None


def extract_last_status_code(resp: str):
    matches = re.findall(r"HTTP/[\d.]+\s+(\d{3})", resp)
    return matches[-1] if matches else None


def extract_expected_rps(expected_path):
    if not os.path.exists(expected_path):
        return None
    with open(expected_path) as f:
        content = f.read()
    for pattern in [
        r"Requests/sec:\s*([0-9.]+)",
        r"Requests\s*per\s*second:\s*([0-9.]+)",
    ]:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def extract_failed_requests(expected_path):
    if not os.path.exists(expected_path):
        return 0
    with open(expected_path) as f:
        text = f.read()
    m = re.search(r"Failed requests:\s*(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    status_matches = re.findall(r"\[(\d+)\]\s+(\d+)\s+responses", text)
    if status_matches:
        total = sum(int(c) for _, c in status_matches)
        success = sum(int(c) for code, c in status_matches if code == "200")
        return total - success
    return 0


def parse_performance_output(output):
    rps_match = re.search(r"Requests/sec:\s*([0-9.]+)", output, re.IGNORECASE)
    rps = float(rps_match.group(1)) if rps_match else None
    status_matches = re.findall(r"\[(\d+)\]\s+(\d+)\s+responses", output)
    failed = 0
    if status_matches:
        total = sum(int(c) for _, c in status_matches)
        success = sum(int(c) for code, c in status_matches if code == "200")
        failed = total - success
    return rps, failed


def has_connection_close(resp):
    return any(
        "connection:" in l.lower() and "close" in l.lower() for l in resp.splitlines()
    )


# --- MAIN GRADER LOOP ---


def run_tests_m2(student_name="unknown_student"):
    if not os.path.exists("tests.json"):
        print("❌ tests.json not found!")
        return [0, 0]

    with open("tests.json") as f:
        data = json.load(f)

    milestone = data["milestones"][1]
    print(f"\n===== {milestone['name']} =====")

    passed = 0
    total_tests = len(milestone["tests"])
    test_history = []

    for test in milestone["tests"]:
        print(f"\nRunning {test['name']}...")
        test_log = {
            "test_name": test["name"],
            "command": test["command"],
            "mode": test.get("mode", "performance"),
            "result": "FAIL",
            "details": "",
        }

        # --- TIERED RECOVERY LOGIC ---
        if not is_server_up():
            if not restart_server():
                if not force_restart_container("."):
                    print("  ❌ CRITICAL FAILURE: Server and Container are dead.")
                    test_log["details"] = "Server/Container recovery failed."
                    test_history.append(test_log)
                    continue

        mode = test_log["mode"]
        expected_path = test["expected"]

        if mode != "performance":
            stdout, stderr, code = exec_in_container(test["command"])
            combined_output = stdout + "\n" + (stderr or "")
            test_log["output_raw"] = combined_output

        # --- MODE LOGIC ---
        if mode == "performance":
            expected_rps = extract_expected_rps(expected_path)
            expected_failed = extract_failed_requests(expected_path)

            if expected_rps is None:
                test_log["details"] = "Could not read expected RPS."
                test_history.append(test_log)
                continue

            required_rps = expected_rps * 0.8
            runs = 5
            total_rps = 0.0
            max_failed_seen = 0
            success = True
            all_run_outputs = []  # To store raw tool output for debugging

            for i in range(runs):
                stdout, stderr, code = exec_in_container(test["command"])
                combined = stdout + "\n" + (stderr or "")
                all_run_outputs.append(f"--- Run {i + 1} ---\n{combined}")

                rps, failed = parse_performance_output(combined)
                if rps is None:
                    success = False
                    break
                total_rps += rps
                max_failed_seen = max(max_failed_seen, failed or 0)
                time.sleep(1)

            avg_rps = total_rps / runs if success else 0

            # Add detailed data to the log
            test_log["performance_data"] = {
                "expected_rps": expected_rps,
                "required_rps": required_rps,
                "actual_avg_rps": avg_rps,
                "max_failed_allowed": expected_failed,
                "actual_max_failed": max_failed_seen,
            }
            # Save the raw output of the last run (or all runs) so you can see the tool's error messages
            test_log["output_raw"] = all_run_outputs[-1]

            if (
                success
                and avg_rps >= required_rps
                and max_failed_seen <= expected_failed
            ):
                test_log["result"] = "PASS"
                passed += 1
            else:
                test_log["details"] = (
                    f"Fail: Avg RPS {avg_rps:.2f} < Req {required_rps:.2f} "
                    f"OR Max Failed {max_failed_seen} > Allowed {expected_failed}"
                )

        elif mode == "persistent":
            if (
                "REUSED_CONNECTION" in normalize_http_response(combined_output)
                or "Re-using" in combined_output
            ):
                test_log["result"] = "PASS"
                passed += 1
            else:
                test_log["details"] = "Keep-alive not detected."

        elif mode == "close":
            if has_connection_close(combined_output):
                test_log["result"] = "PASS"
                passed += 1
            else:
                test_log["details"] = "Connection: close missing."

        elif mode in ["status", "status_last"]:
            with open(expected_path) as f:
                exp_content = f.read()
                exp = extract_status_code(exp_content)
            act = (
                extract_status_code(combined_output)
                if mode == "status"
                else extract_last_status_code(combined_output)
            )
            if act == exp:
                test_log["result"] = "PASS"
                passed += 1
            else:
                test_log["details"] = f"Status mismatch. Exp: {exp}, Got: {act}"
                # Add this to see exactly what the server sent back vs what was expected
                test_log["expected_status"] = exp
                test_log["actual_status"] = act

        test_history.append(test_log)
        print(
            f"  {'✅' if test_log['result'] == 'PASS' else '❌'} {test_log['result']}"
        )

    # Final Logging
    server_stdout, _, _ = exec_in_container("cat server.log")
    final_log = {
        "metadata": {
            "student": student_name,
            "timestamp": datetime.datetime.now().isoformat(),
            "milestone": milestone["name"],
        },
        "summary": {
            "passed": passed,
            "total": total_tests,
            "weighted_score": passed * milestone["weight"],
        },
        "test_history": test_history,
        "server_internal_log": server_stdout,
    }

    os.makedirs("results", exist_ok=True)
    with open(f"results/result_M2_{student_name}.json", "w") as jf:
        json.dump(final_log, jf, indent=4)

    return [passed, passed * milestone["weight"]]
