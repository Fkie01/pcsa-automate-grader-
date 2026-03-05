import json
import os
import time
import re

from docker_container import exec_in_container


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
    if not os.path.exists(expected_path):
        return 0

    with open(expected_path) as f:
        content = f.read()

    match = re.search(r"Failed requests:\s*(\d+)", content, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def parse_performance_output(output):
    rps = None
    failed = 0

    # Sum ALL rps matches (handles interleaved ab output)
    rps_patterns = [
        r"Requests per second:\s*([0-9.]+)",
        r"Requests/sec:\s*([0-9.]+)",
        r"([0-9.]+)\s*requests/sec",
    ]

    for pattern in rps_patterns:
        matches = re.findall(pattern, output, re.IGNORECASE)
        if matches:
            rps = sum(float(x) for x in matches)  # ← sum all, not just first
            break

    # Sum ALL failed matches — but only first number per line (avoid sub-errors)
    fail_patterns = [
        r"Failed requests:\s*(\d+)",
        r"Non-2xx or 3xx responses:\s*(\d+)",
        r"Failed:\s*(\d+)",
    ]

    for pattern in fail_patterns:
        matches = re.findall(pattern, output, re.IGNORECASE)
        if matches:
            failed = sum(int(x) for x in matches)  # ← sum all
            break

    return rps, failed

def has_connection_close(resp):
    for line in resp.splitlines():
        line = line.strip()
        if line.startswith("< "):
            line = line[2:]
        if line.lower().startswith("connection:") and "close" in line.lower():
            return True
    return False


def run_tests_m2():
    with open("tests.json") as f:
        data = json.load(f)

    milestone = data["milestones"][1]
    print(f"\n===== {milestone['name']} =====")

    passed = 0
    total_tests = len(milestone["tests"])

    for test in milestone["tests"]:
        print(f"\nRunning {test['name']}...")

        mode = test.get("mode", "performance")
        expected_path = test["expected"]

        # ── only run once upfront for non-performance modes ──
        if mode != "performance":
            stdout, stderr, code = exec_in_container(test["command"])
            combined_output = stdout + "\n" + stderr

        # =====================================================
        # PERFORMANCE
        # =====================================================
        if mode == "performance":
            expected_rps = extract_expected_rps(expected_path)
            expected_failed = extract_failed_requests(expected_path)

            if expected_rps is None:
                print("❌ FAIL (Could not read expected RPS)")
                continue

            required_rps = expected_rps * 0.80  # 20% safety margin
            print(f"Expected RPS : {expected_rps}")
            print(f"Required RPS : {required_rps:.2f}  (80% threshold)")
            print(f"Max failed   : {expected_failed}")

            runs = 5
            total_rps = 0.0
            max_failed_seen = 0
            success = True

            for i in range(runs):
                stdout, stderr, code = exec_in_container(test["command"])
                rps, failed = parse_performance_output(stdout + "\n" + stderr)

                if rps is None:
                    print(f"❌ FAIL (Could not parse RPS on run {i+1})")
                    print(stdout)
                    success = False
                    break

                failed = failed or 0
                print(f"  Run {i+1}: RPS={rps:.2f}, Failed={failed}")
                total_rps += rps
                max_failed_seen = max(max_failed_seen, failed)
                time.sleep(1)

            if not success:
                continue

            average_rps = total_rps / runs
            print(f"Average RPS      : {average_rps:.2f}")
            print(f"Max failed seen  : {max_failed_seen}")

            if average_rps >= required_rps and max_failed_seen <= expected_failed:
                print("✅ PASS")
                passed += 1
            else:
                print("❌ FAIL")
                if average_rps < required_rps:
                    print(f"  → RPS {average_rps:.2f} < required {required_rps:.2f}")
                if max_failed_seen > expected_failed:
                    print(f"  → Failed {max_failed_seen} > allowed {expected_failed}")

        # =====================================================
        # PERSISTENT
        # =====================================================
        elif mode == "persistent":
            if "Re-using existing connection" in combined_output:
                print("✅ PASS (Connection reused)")
                passed += 1
            else:
                print("❌ FAIL (Keep-alive not detected)")
                print(combined_output[:500])

        # =====================================================
        # CLOSE
        # =====================================================
        elif mode == "close":
            if has_connection_close(combined_output):
                print("✅ PASS (Connection: close detected)")
                passed += 1
            else:
                print("❌ FAIL (Connection: close not found)")
                print(combined_output[:500])

        # =====================================================
        # STATUS
        # =====================================================
        elif mode == "status":
            if not os.path.exists(expected_path):
                print("❌ FAIL (Expected file missing)")
                continue

            with open(expected_path) as f:
                expected_content = f.read()

            expected_status = extract_status_code(expected_content)
            actual_status = extract_status_code(combined_output)

            if actual_status == expected_status:
                print(f"✅ PASS (Status {actual_status})")
                passed += 1
            else:
                print("❌ FAIL (Status mismatch)")
                print(f"  Expected : {expected_status}")
                print(f"  Actual   : {actual_status}")
                print(combined_output[:500])

        # =====================================================
        # STATUS_LAST  (e.g. Persistent_Idle_Timeout)
        # =====================================================
        elif mode == "status_last":
            if not os.path.exists(expected_path):
                print("❌ FAIL (Expected file missing)")
                continue

            with open(expected_path) as f:
                expected_content = f.read()

            expected_status = extract_status_code(expected_content)
            actual_status = extract_last_status_code(combined_output)

            if actual_status == expected_status:
                print(f"✅ PASS (Last status {actual_status})")
                passed += 1
            else:
                print("❌ FAIL (Last status mismatch)")
                print(f"  Expected : {expected_status}")
                print(f"  Actual   : {actual_status}")
                print(combined_output[:500])

        else:
            print(f"⚠ Unsupported mode: {mode}")

    weight_score = passed * milestone["weight"]
    print(f"\n{milestone['name']} Score          : {passed}/{total_tests}")
    print(f"{milestone['name']} Weighted Score : {weight_score:.2f}/{total_tests * milestone['weight']:.2f}")

    return [passed, weight_score]