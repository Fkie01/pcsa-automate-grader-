import json
import os
import time
import re
import subprocess

# ── config ────────────────────────────────────────────────
MILESTONE_IDX     = 2
CONTAINER_NAME    = "grader-test"
CONTAINER_SAMPLES = "/grader/samples"
CONTAINER_CGI     = "/grader/cgi"


# ------------------------------------------------
# Execute command inside container via stdin
# avoids shell escaping issues with $, %, quotes
# ------------------------------------------------
def exec_in_container(command):
    # unescape JSON-escaped quotes so bash sees: "$code" not \"$code\"
    command = command.replace('\\"', '"')
    cmd = ["docker", "exec", "-i", CONTAINER_NAME, "bash"]
    result = subprocess.run(
        cmd,
        input=command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    return result.stdout.strip(), "", result.returncode


# ------------------------------------------------
# Container health check
# ------------------------------------------------
def is_container_running():
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", CONTAINER_NAME],
        capture_output=True, text=True
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
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("* "):
            continue
        if stripped.startswith("> "):
            continue
        if stripped.startswith("< "):
            continue
        if re.match(r"HTTP/[\d.]+\s+\d{3}", stripped):
            continue
        if re.match(
            r"(Date|Server|Last-Modified|Content-Length"
            r"|Connection|Transfer-Encoding"
            r"|Cache-Control|ETag):",
            stripped, re.IGNORECASE
        ):
            continue
        if re.match(r"<h1>.*</h1>", stripped):
            continue
        if not stripped:
            continue
        lines.append(stripped)
    return "\n".join(lines)


# ------------------------------------------------
# Body contains check
# ------------------------------------------------
def check_body_contains(actual: str, expected_path: str):
    if not os.path.exists(expected_path):
        return False, ["Expected file missing"]

    with open(expected_path) as f:
        expected_lines = [l.strip() for l in f.readlines() if l.strip()]

    normalized = normalize_output(actual)
    missing = [line for line in expected_lines if line not in normalized]
    return len(missing) == 0, missing


# ------------------------------------------------
# Performance parsing — sum ALL matches
# ------------------------------------------------
def parse_performance_output(output):
    rps = None
    failed = 0

    # 1. Improved RPS parsing (handles commas and multiple tool formats)
    rps_patterns = [
        r"Requests per second:\s*([\d,.]+)",   # ab
        r"Requests/sec:\s*([\d,.]+)",          # hey
        r"([\d,.]+)\s*requests/sec",           # wrk
    ]
    
    for pattern in rps_patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            # Remove commas before converting to float
            rps = float(match.group(1).replace(',', ''))
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
    # hey status code distribution parsing
    status_matches = re.findall(r"\[(\d+)\]\s+(\d+)\s+responses", output)

    if status_matches:
        total = 0
        success_200 = 0

        for code, count in status_matches:
            count = int(count)
            total += count
            if code == "200":
                success_200 += count

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
def run_tests_m3():
    with open("tests.json") as f:
        data = json.load(f)

    milestones = data["milestones"]
    if MILESTONE_IDX >= len(milestones):
        print(f"❌ milestones[{MILESTONE_IDX}] not found")
        return [0, 0]

    milestone   = milestones[MILESTONE_IDX]
    passed      = 0
    total_tests = len(milestone["tests"])

    print(f"\n{'='*55}")
    print(f"  {milestone['name']}")
    print(f"{'='*55}")

    for test in milestone["tests"]:

        print(f"\nRunning {test['name']}...")

        # ── container health check ─────────────────────────
        if not assert_container(test['name']):
            print(f"  ⏭ Skipping remaining tests — container not running")
            break

        # ── server health check + auto-restart ────────────
        if not is_server_up():
            if not restart_server():
                print(f"  ❌ FAIL (Server could not be restarted)")
                continue

        mode          = test.get("mode", "status")
        expected_path = test["expected"]

        if mode != "performance":
            stdout, stderr, code = exec_in_container(test["command"])
            combined_output = stdout + "\n" + stderr

            if not assert_container(test['name']):
                break

        # =====================================================
        # STATUS
        # =====================================================
        if mode == "status":

            if not os.path.exists(expected_path):
                print("  ❌ FAIL (Expected file missing)")
                continue

            expected_status = extract_expected_status(expected_path)
            actual_status   = extract_status_code(combined_output)

            if actual_status == expected_status:
                print(f"  ✅ PASS (Status {actual_status})")
                passed += 1
            else:
                print(f"  ❌ FAIL (Status mismatch)")
                print(f"     Expected : {expected_status}")
                print(f"     Actual   : {actual_status}")
                print(f"     Output   :\n{combined_output[:400]}")

        # =====================================================
        # STATUS_LAST
        # =====================================================
        elif mode == "status_last":

            if not os.path.exists(expected_path):
                print("  ❌ FAIL (Expected file missing)")
                continue

            expected_status = extract_expected_status(expected_path)
            actual_status   = extract_last_status_code(combined_output)

            if actual_status == expected_status:
                print(f"  ✅ PASS (Last status {actual_status})")
                passed += 1
            else:
                print(f"  ❌ FAIL (Last status mismatch)")
                print(f"     Expected : {expected_status}")
                print(f"     Actual   : {actual_status}")
                print(f"     Output   :\n{combined_output[:400]}")

        # =====================================================
        # BODY_CONTAINS
        # =====================================================
        elif mode == "body_contains":

            ok, missing = check_body_contains(combined_output, expected_path)

            if ok:
                print(f"  ✅ PASS (All expected body lines found)")
                passed += 1
            else:
                print(f"  ❌ FAIL (Missing lines in body)")
                for m in missing:
                    print(f"     Missing : {m}")
                print(f"     Normalized output:\n{normalize_output(combined_output)[:600]}")

        # =====================================================
        # PERFORMANCE
        # runs=1 for sequential stability (loops internally)
        # runs=3 for ab/wrk benchmarks
        # =====================================================
        elif mode == "performance":

            expected_rps    = extract_expected_rps(expected_path)
            expected_failed = extract_expected_failed(expected_path)

            if expected_rps is None:
                print("  ❌ FAIL (Could not read expected RPS)")
                continue

            required_rps = expected_rps * 0.80
            print(f"  Expected RPS : {expected_rps:.2f}")
            print(f"  Required RPS : {required_rps:.2f}  (80% threshold)")
            print(f"  Max failed   : {expected_failed}")

            runs            = 1 if test["name"] == "CGI_Sequential_Stability" else 3
            total_rps       = 0.0
            max_failed_seen = 0
            success         = True

            for i in range(runs):

                if not assert_container(test['name']):
                    success = False
                    break

                # restart server between runs if it crashed
                if not is_server_up():
                    if not restart_server():
                        success = False
                        break

                stdout, stderr, code = exec_in_container(test["command"])
                rps, failed = parse_performance_output(stdout + "\n" + stderr)

                if rps is None:
                    print(f"  ❌ FAIL (Could not parse RPS on run {i+1})")
                    print("----- FULL BENCHMARK OUTPUT -----")
                    # Add these lines to see the raw error from the tool
                    print(f"STDOUT: {stdout}")
                    print(f"STDERR: {stderr}") 
                    print("---------------------------------")
                    
                    # Also, peek at the server's log inside the container
                    log_out, _, _ = exec_in_container("tail -n 20 server.log")
                    print(f"----- SERVER LOG TAIL -----\n{log_out}")
                    success = False
                    break   

            if not success:
                continue

            average_rps = total_rps / runs
            print(f"  Average RPS  : {average_rps:.2f}")
            print(f"  Max failed   : {max_failed_seen}")

            if average_rps >= required_rps and max_failed_seen <= expected_failed:
                print("  ✅ PASS")
                passed += 1
            else:
                print("  ❌ FAIL")
                if average_rps < required_rps:
                    print(f"     → RPS {average_rps:.2f} < required {required_rps:.2f}")
                if max_failed_seen > expected_failed:
                    print(f"     → Failed {max_failed_seen} > allowed {expected_failed}")

        else:
            print(f"  ⚠ Unsupported mode: {mode}")

    # ── final score ───────────────────────────────────────────
    weight_score = passed * milestone["weight"]
    print(f"\n{'='*55}")
    print(f"  {milestone['name']} Score          : {passed}/{total_tests}")
    print(f"  {milestone['name']} Weighted Score : "
          f"{weight_score:.2f}/{total_tests * milestone['weight']:.2f}")
    print(f"{'='*55}\n")

    return [passed, weight_score]


if __name__ == "__main__":
    run_tests_m3()