import datetime
import json
import os
import re
import subprocess
import time

from docker_container import (
    build_project,
    exec_in_container,
    restart_server,
    start_container,
    stop_container,
)

# ── config ────────────────────────────────────────────────
MILESTONE_IDX = 2
CONTAINER_NAME = "grader-test"
CONTAINER_SAMPLES = "/grader/samples"
CONTAINER_CGI = "/grader/cgi"


# log function for debugging

# def save_grading_log(student_name, milestone_name, passed, total, weight_score, test_details):
#     # 1. Pull the actual server.log from inside the container
#     server_log_content, _, _ = exec_in_container("cat server.log")

#     # 2. Structure the data
#     log_data = {
#         "metadata": {
#             "student_name": student_name,
#             "milestone": milestone_name,
#             "timestamp": datetime.datetime.now().isoformat(),
#             "container_id": CONTAINER_NAME
#         },
#         "results": {
#             "passed_count": passed,
#             "total_tests": total,
#             "weighted_score": weight_score,
#             "percentage": (passed / total * 100) if total > 0 else 0
#         },
#         "tests": test_details,  # List of dicts for each test run
#         "raw_server_stdout": server_log_content
#     }

#     # 3. Save to JSON
#     filename = f"grade_{milestone_name.replace(' ', '_')}_{student_name}.json"
#     with open(filename, "w") as f:
#         json.dump(log_data, f, indent=4)

#     print(f"\n📂 Grading log saved to: {filename}")


# ------------------------------------------------
# Execute command inside container via stdin
# avoids shell escaping issues with $, %, quotes
# ------------------------------------------------
# def exec_in_container(command, timeout=10):
#     # unescape JSON-escaped quotes so bash sees: "$code" not \"$code\"
#     command = command.replace('\\"', '"')
#     cmd = ["docker", "exec", "-i", CONTAINER_NAME, "bash"]

#     try:
#         result = subprocess.run(
#             cmd,
#             input=command,
#             stdout=subprocess.PIPE,
#             stderr=subprocess.STDOUT,
#             text=True,
#             errors="replace",
#             timeout=timeout  # The clock starts here
#         )
#         return result.stdout.strip(), "", result.returncode

#     except subprocess.TimeoutExpired as e:
#         # Capture whatever output was produced before the timeout (if any)
#         stdout_so_far = e.stdout.decode("utf-8", "replace") if e.stdout else ""
#         error_msg = f"❌ TIMEOUT: Command exceeded {timeout}s limit"

#         # Return a custom error state that your main loop can recognize
#         return stdout_so_far + "\n" + error_msg, "TIMEOUT_ERROR", 124


# ------------------------------------------------
# Container health check
# ------------------------------------------------
def is_container_running():
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", CONTAINER_NAME],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def assert_container(test_name):
    if not is_container_running():
        print(f"\n❌ Container died during '{test_name}'")
        print(f"   Likely cause: --pids-limit too low for concurrent test")
        return False
    return True


# ------------------------------------------------
# Server health check + auto-restart
# ------------------------------------------------
def is_server_up():
    out, _, _ = exec_in_container(
        "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/index.html"
    )
    return "200" in out


# def restart_server():
#     print("  ⚠ Server not responding — restarting...")
#     exec_in_container("pkill -f icws 2>/dev/null || true")
#     time.sleep(2)
#     exec_in_container(
#         f"nohup ./icws --port 9000 "
#         f"--root {CONTAINER_SAMPLES} "
#         f"--numThreads 32 --timeout 5 "
#         f"--cgiHandler {CONTAINER_CGI}/dispatcher.py "
#         f"> server.log 2>&1 &"
#     )
#     # wait up to 10s for port to open
#     for _ in range(20):
#         out, _, _ = exec_in_container("ss -ltn | grep 9000")
#         if out:
#             print("  ✅ Server restarted")
#             return True
#         time.sleep(0.5)
#     print("  ❌ Server failed to restart")
#     return False


# ------------------------------------------------
# Status extraction — FIRST HTTP/ line
# ------------------------------------------------
def extract_status_code(resp: str):
    for line in resp.splitlines():
        line = line.strip()
        if line.startswith("< "):
            line = line[2:]
        match = re.match(r"HTTP/[\d.]+\s+(\d{3})", line)
        if match:
            return match.group(1)
    return None


# ------------------------------------------------
# Status extraction — LAST HTTP/ line
# ------------------------------------------------
def extract_last_status_code(resp: str):
    last = None
    for line in resp.splitlines():
        line = line.strip()
        if line.startswith("< "):
            line = line[2:]
        match = re.match(r"HTTP/[\d.]+\s+(\d{3})", line)
        if match:
            last = match.group(1)
    return last


# ------------------------------------------------
# Expected status from file
# ------------------------------------------------
def extract_expected_status(path: str):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        content = f.read()
    match = re.search(r"HTTP/[\d.]+\s+(\d{3})", content)
    return match.group(1) if match else None


# ------------------------------------------------
# Normalize output — strip curl noise, keep CGI body
# ------------------------------------------------
def normalize_output(output: str) -> str:
    lines = []
    in_shell_env = False
    env_dict = {}

    for line in output.splitlines():
        stripped = line.strip()

        if "<H3>Shell Environment:</H3>" in stripped:
            in_shell_env = True
            continue

        if in_shell_env:
            # Only exit when we hit the closing DL *after* seeing env content,
            # or when a new H3 section starts (meaning the env block is done)
            if stripped == "</DL>":
                if env_dict:  # we've collected something — this is the real end
                    in_shell_env = False
                continue  # skip the tag either way

            if stripped.startswith("<H3>") and env_dict:
                in_shell_env = False
                continue

            match = re.search(
                r"<DT>\s*([^<]+)\s*<DD>\s*([^<]*)", stripped, re.IGNORECASE
            )
            if match:
                key = match.group(1).strip()
                value = match.group(2).strip()
                if key not in env_dict:  # first occurrence wins
                    env_dict[key] = value
                    lines.append(f"{key}={value}")
            continue

        # --- Plain text path ---
        if re.match(r"^[A-Z_][A-Z0-9_]*=", stripped):
            lines.append(stripped)
            key, _, value = stripped.partition("=")
            env_dict[key] = value

        if stripped.startswith("REQUEST_BODY="):
            if stripped not in lines:
                lines.append(stripped)
            env_dict["REQUEST_BODY"] = stripped[len("REQUEST_BODY=") :]

    # Aliases for C binary tests
    if "REQUEST_METHOD" in env_dict:
        lines.append(f"METHOD={env_dict['REQUEST_METHOD']}")
    if "QUERY_STRING" in env_dict:
        lines.append(f"QUERY={env_dict['QUERY_STRING']}")
    if "CONTENT_LENGTH" in env_dict:
        lines.append(f"LENGTH={env_dict['CONTENT_LENGTH']}")
    else:
        lines.append("LENGTH=")

    return "\n".join(lines)


# ------------------------------------------------
# Body contains check
# ------------------------------------------------
def check_body_contains(actual: str, expected_path: str):
    if not os.path.exists(expected_path):
        return False, ["Expected file missing"]

    with open(expected_path) as f:
        expected_lines = [l.strip() for l in f.readlines() if l.strip()]

    normalized = normalize_output(actual)  # ← no .lower()
    normalized_lower = normalized.lower()  # for comparison only

    missing = [line for line in expected_lines if line.lower() not in normalized_lower]
    return len(missing) == 0, missing


# ------------------------------------------------
# Performance parsing — sum ALL matches
# ------------------------------------------------
def parse_performance_output(output):
    rps = None
    failed = 0

    # 1. Improved RPS parsing (handles commas and multiple tool formats)
    rps_patterns = [
        r"Requests per second:\s*([\d,.]+)",  # ab
        r"Requests/sec:\s*([\d,.]+)",  # hey
        r"([\d,.]+)\s*requests/sec",  # wrk
    ]

    for pattern in rps_patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            # Remove commas before converting to float
            rps = float(match.group(1).replace(",", ""))
            break

    # 2. Capture 'failed' counts from common tools
    # ApacheBench: 'Failed requests: 5'
    # Hey: '[500] 10 responses' (where 500 is the status code)

    # Check for AB failure count
    ab_fail_match = re.search(r"Failed requests:\s*(\d+)", output, re.IGNORECASE)
    if ab_fail_match:
        failed = int(ab_fail_match.group(1))

    # Check for Hey status distribution if AB didn't match
    else:
        status_matches = re.findall(r"\[(\d+)\]\s+(\d+)\s+responses", output)
        if status_matches:
            total = 0
            success_200 = 0
            for code, count in status_matches:
                c = int(count)
                total += c
                if code == "200":
                    success_200 += c
            failed = total - success_200

    return rps, failed


# ------------------------------------------------
# Expected RPS / failed from file
# ------------------------------------------------
def extract_expected_rps(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        content = f.read()
    for pattern in [
        r"Requests per second:\s*([0-9.]+)",
        r"Requests/sec:\s*([0-9.]+)",
        r"([0-9.]+)\s*requests/sec",
    ]:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def extract_expected_failed(path):
    if not os.path.exists(path):
        return 0

    with open(path) as f:
        content = f.read()

    # ApacheBench style
    match = re.search(r"Failed requests:\s*(\d+)", content, re.IGNORECASE)
    if match:
        return int(match.group(1))

    # wrk sometimes prints this
    match = re.search(r"Non-2xx or 3xx responses:\s*(\d+)", content, re.IGNORECASE)
    if match:
        return int(match.group(1))

    # hey status distribution
    status_matches = re.findall(r"\[(\d+)\]\s+(\d+)\s+responses", content)

    if status_matches:
        total = 0
        success = 0

        for code, count in status_matches:
            count = int(count)
            total += count

            if code.startswith("2") or code.startswith("3"):
                success += count

        return total - success

    return 0


# ------------------------------------------------
# Main test runner — Milestone 3
# ------------------------------------------------


def run_tests_m3(student_name):
    with open("tests.json") as f:
        data = json.load(f)

    milestones = data["milestones"]
    if MILESTONE_IDX >= len(milestones):
        print(f"❌ milestones[{MILESTONE_IDX}] not found")
        return [0, 0]

    milestone = milestones[MILESTONE_IDX]
    passed = 0
    total_tests = len(milestone["tests"])
    test_history = []  # 📝 Fix: Added to track details for JSON

    print(f"\n{'=' * 55}")
    print(f"  {milestone['name']}")
    print(f"{'=' * 55}")

    for test in milestone["tests"]:
        print(f"\nRunning {test['name']}...")

        # Initialize log entry for this specific test
        test_log = {
            "test_name": test["name"],
            "command": test["command"],
            "result": "FAIL",
            "details": "",
        }

        if not assert_container(test["name"]):
            test_log["details"] = "Container died."
            test_history.append(test_log)
            break

        if not is_server_up():
            if not restart_server():
                test_log["details"] = "Server down and failed to restart."
                test_history.append(test_log)
                continue

        mode = test.get("mode", "status")
        expected_path = test["expected"]

        if mode != "performance":
            stdout, stderr, code = exec_in_container(test["command"])
            combined_output = stdout + "\n" + stderr
            test_log["output_raw"] = combined_output
            if not assert_container(test["name"]):
                break

            print(f"  --- stdout ---")
            print(combined_output)
            print(f"  --- end stdout ---")

        # --- MODE: STATUS ---
        if mode == "status":
            expected_status = extract_expected_status(expected_path)
            actual_status = extract_status_code(combined_output)
            if actual_status == expected_status:
                test_log["result"] = "PASS"
                passed += 1
            else:
                test_log["details"] = (
                    f"Status mismatch: Exp {expected_status}, Got {actual_status}"
                )

        # --- MODE: STATUS_LAST ---
        elif mode == "status_last":
            expected_status = extract_expected_status(expected_path)
            actual_status = extract_last_status_code(combined_output)
            if actual_status == expected_status:
                test_log["result"] = "PASS"
                passed += 1
            else:
                test_log["details"] = (
                    f"Last status mismatch: Exp {expected_status}, Got {actual_status}"
                )

        # --- MODE: BODY_CONTAINS ---
        elif mode == "body_contains":
            normalized_debug = normalize_output(combined_output)
            print(f"  --- normalized ---")
            print(normalized_debug)
            print(f"  --- end normalized ---")
            ok, missing = check_body_contains(combined_output, expected_path)
            if ok:
                test_log["result"] = "PASS"
                passed += 1
            else:
                test_log["details"] = f"Missing body lines: {missing}"

        # --- MODE: PERFORMANCE ---
        elif mode == "performance":
            expected_rps = extract_expected_rps(expected_path)
            expected_failed = extract_expected_failed(expected_path)
            required_rps = expected_rps * 0.80

            runs = 1 if test["name"] == "CGI_Sequential_Stability" else 3
            total_rps = 0.0
            max_failed_seen = 0
            success = True

            for i in range(runs):
                if not is_server_up() and not restart_server():
                    success = False
                    break

                stdout, stderr, code = exec_in_container(test["command"])
                rps, failed = parse_performance_output(stdout + "\n" + stderr)

                if rps is not None:
                    total_rps += rps
                    max_failed_seen = max(max_failed_seen, failed)
                else:
                    success = False
                    break

            if success:
                avg_rps = total_rps / runs
                test_log["rps_actual"] = avg_rps
                test_log["failed_count"] = max_failed_seen
                if avg_rps >= required_rps and max_failed_seen <= expected_failed:
                    test_log["result"] = "PASS"
                    passed += 1
            else:
                test_log["details"] = (
                    "Performance benchmark failed to execute or parse."
                )

        test_history.append(test_log)
        # Visual feedback
        print(
            f"  {'✅' if test_log['result'] == 'PASS' else '❌'} {test_log['result']}"
        )

    # ── Final Scoring Logic ───────────────────────────────────
    weight_score = passed * milestone["weight"]

    # ── 📝 JSON LOGGING BLOCK ────────────────────────────────
    server_stdout, _, _ = exec_in_container("cat server.log")

    final_log = {
        "metadata": {
            "student": student_name,
            "timestamp": datetime.datetime.now().isoformat(),
            "milestone": milestone["name"],
        },
        "score": {"passed": passed, "total": total_tests, "weighted": weight_score},
        "test_results": test_history,
        "server_log_raw": server_stdout,
    }

    log_filename = (
        f"results/result_{student_name}_{milestone['name'].replace(' ', '_')}.json"
    )
    with open(log_filename, "w") as jf:
        json.dump(final_log, jf, indent=4)

    print(f"\n📂 Full result log saved to: {log_filename}")
    return [passed, weight_score]


if __name__ == "__main__":
    run_tests_m3()
