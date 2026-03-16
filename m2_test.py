import json
import os
import time
import re
import subprocess
import datetime
import json
from docker_container import exec_in_container

CONTAINER_NAME    = "grader-test"
CONTAINER_SAMPLES = "/grader/samples"
CONTAINER_CGI     = "/grader/cgi"


def normalize_http_response(resp: str):
    lines = resp.splitlines()
    filtered = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if "Re-using existing connection" in line:
            return "REUSED_CONNECTION"
        if line.startswith("*"):
            continue
        if line.startswith(">"):
            continue
        if line.startswith("{"):
            continue
        if line.startswith("%"):
            continue
        if "Dload" in line:
            continue
        if line.startswith("< "):
            line = line[2:]
        if line.startswith("Date:"):
            continue
        if line.startswith("Last-Modified:"):
            continue
        if line.startswith("Server:"):
            continue
        filtered.append(line)

    if not filtered:
        return ""

    status = filtered[0]
    headers = sorted(filtered[1:], key=lambda x: x.lower())
    return "\n".join([status] + headers).strip()

def is_server_up():
    out, _, _ = exec_in_container(
        "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/index.html"
    )
    return "200" in out


def restart_server():
    print("  ⚠ Server not responding — restarting...")
    exec_in_container("pkill -f icws 2>/dev/null || true")
    time.sleep(2)
    exec_in_container(
        f"nohup ./icws --port 9000 "
        f"--root {CONTAINER_SAMPLES} "
        f"--numThreads 32 --timeout 5 "
        f"--cgiHandler {CONTAINER_CGI}/dispatcher.py "
        f"> server.log 2>&1 &"
    )
    # wait up to 10s for port to open
    for _ in range(20):
        out, _, _ = exec_in_container("ss -ltn | grep 9000")
        if out:
            print("  ✅ Server restarted")
            return True
        time.sleep(0.5)
    print("  ❌ Server failed to restart")
    return False


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
        r"Requests\s*per\s*second:\s*([0-9.]+)",
        r"Requests/sec:\s*([0-9.]+)",
        r"([0-9.]+)\s*requests/sec",
    ]:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return float(match.group(1))

    return None


def extract_failed_requests(expected_path):
    with open(expected_path) as f:
        text = f.read()

    # Standard failure patterns (ab style)
    for pattern in [
        r"Failed requests:\s*(\d+)",
        r"Non-2xx or 3xx responses:\s*(\d+)",
        r"Failed:\s*(\d+)",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return int(m.group(1))

    # hey style: status code distribution
    status_matches = re.findall(r"\[(\d+)\]\s+(\d+)\s+responses", text)

    if status_matches:
        total = 0
        success_200 = 0

        for code, count in status_matches:
            count = int(count)
            total += count
            if code == "200":
                success_200 += count

        return total - success_200

    return 0

def parse_performance_output(output):
    rps = None
    failed = 0

    # Extract Requests/sec from hey
    rps_match = re.search(r"Requests/sec:\s*([0-9.]+)", output, re.IGNORECASE)
    if rps_match:
        rps = float(rps_match.group(1))

    # Parse status code distribution
    status_matches = re.findall(r"\[(\d+)\]\s+(\d+)\s+responses", output)

    total = 0
    success_200 = 0

    for code, count in status_matches:
        count = int(count)
        total += count
        if code == "200":
            success_200 += count

    failed = total - success_200

    return rps, failed

def has_connection_close(resp):
    for line in resp.splitlines():
        line = line.strip()
        if line.startswith("< "):
            line = line[2:]
        if line.lower().startswith("connection:") and "close" in line.lower():
            return True
    return False

def run_tests_m2(student_name="unknown_student"):
    with open("tests.json") as f:
        data = json.load(f)

    # Milestone 2 is index 1 in your data structure
    milestone = data["milestones"][1]
    print(f"\n===== {milestone['name']} =====")

    passed = 0
    total_tests = len(milestone["tests"])
    test_history = []  # 📝 Added to track details for JSON

    for test in milestone["tests"]:
        print(f"\nRunning {test['name']}...")
        
        # Initialize log entry for this specific test
        test_log = {
            "test_name": test["name"],
            "command": test["command"],
            "mode": test.get("mode", "performance"),
            "result": "FAIL",
            "details": ""
        }

        if not is_server_up():
            if not restart_server():
                print("  ❌ Cannot run test without server")
                test_log["details"] = "Server down and failed to restart."
                test_history.append(test_log)
                continue

        mode = test_log["mode"]
        expected_path = test["expected"]

        # Run command upfront for non-performance modes
        if mode != "performance":
            stdout, stderr, code = exec_in_container(test["command"])
            combined_output = stdout + "\n" + stderr
            test_log["output_raw"] = combined_output

        # --- MODE: PERFORMANCE ---
        if mode == "performance":
            expected_rps = extract_expected_rps(expected_path)
            expected_failed = extract_failed_requests(expected_path)
            
            if expected_rps is None:
                test_log["details"] = "Could not read expected RPS from file."
                test_history.append(test_log)
                continue

            required_rps = expected_rps * 0.80
            runs = 5
            total_rps = 0.0
            max_failed_seen = 0
            success = True

            for i in range(runs):
                stdout, stderr, code = exec_in_container(test["command"])
                rps, failed = parse_performance_output(stdout + "\n" + stderr)

                if rps is None:
                    success = False; break

                total_rps += rps
                max_failed_seen = max(max_failed_seen, failed or 0)
                time.sleep(1)

            if success:
                avg_rps = total_rps / runs
                test_log["avg_rps"] = avg_rps
                test_log["max_failed"] = max_failed_seen
                if avg_rps >= required_rps and max_failed_seen <= expected_failed:
                    test_log["result"] = "PASS"
                    passed += 1
            else:
                test_log["details"] = "Failed to parse RPS during performance runs."

        # --- MODE: PERSISTENT ---
        elif mode == "persistent":
            if "Re-using existing connection" in combined_output:
                test_log["result"] = "PASS"
                passed += 1
            else:
                test_log["details"] = "Keep-alive / Connection reuse not detected in curl output."

        # --- MODE: CLOSE ---
        elif mode == "close":
            if has_connection_close(combined_output):
                test_log["result"] = "PASS"
                passed += 1
            else:
                test_log["details"] = "Connection: close header missing."

        # --- MODE: STATUS / STATUS_LAST ---
        elif mode in ["status", "status_last"]:
            if not os.path.exists(expected_path):
                test_log["details"] = "Expected output file missing."
            else:
                with open(expected_path) as f:
                    expected_status = extract_status_code(f.read())
                
                actual_status = (extract_status_code(combined_output) if mode == "status" 
                                 else extract_last_status_code(combined_output))

                if actual_status == expected_status:
                    test_log["result"] = "PASS"
                    passed += 1
                else:
                    test_log["details"] = f"Status mismatch. Exp: {expected_status}, Got: {actual_status}"

        test_history.append(test_log)
        print(f"  {'✅' if test_log['result'] == 'PASS' else '❌'} {test_log['result']}")

    # ── Final Scoring Logic ──
    weight_score = passed * milestone["weight"]
    
    # ── 📝 JSON LOGGING BLOCK ──
    # Pull the server log from the container to see student's internal prints
    server_stdout, _, _ = exec_in_container("cat server.log")
    
    final_log = {
        "metadata": {
            "student": student_name,
            "timestamp": datetime.datetime.now().isoformat(),
            "milestone": milestone["name"]
        },
        "summary": {
            "passed": passed,
            "total": total_tests,
            "weighted_score": weight_score
        },
        "test_history": test_history,
        "server_internal_log": server_stdout
    }

    log_filename = f"results/result_M2_{student_name}.json"
    with open(log_filename, "w") as jf:
        json.dump(final_log, jf, indent=4)
    
    print(f"\n📂 Grading log saved to: {log_filename}")
    return [passed, weight_score]