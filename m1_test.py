import difflib
import json
import os
import shutil
import subprocess
import sys
import time
import re
import datetime
import json
import difflib

from docker_container import exec_in_container

# CONTAINER_NAME = "grader-test"
# IMAGE_NAME = "c-grader"
# PROJECT_PATH = "/Users/fkie01/developer/source/cs227/a02-ic-web-server-Fkie01"
# SAMPLES_PATH = os.path.abspath("auto_samples")

# ------------------------------------------------
# Utility
# ------------------------------------------------

# def run_cmd(cmd):
#     return subprocess.run(cmd, capture_output=True, text=True)

# def exec_in_container(command):
#     cmd = ["docker", "exec", CONTAINER_NAME, "bash", "-c", command]
#     result = run_cmd(cmd)
#     return result.stdout.strip(), result.stderr.strip(), result.returncode

# # ------------------------------------------------
# # Setup Sample Files
# # ------------------------------------------------

# def create_samples():
#     print("📁 Creating grader sample files...")

#     if os.path.exists(SAMPLES_PATH):
#         shutil.rmtree(SAMPLES_PATH)

#     os.makedirs(SAMPLES_PATH)

#     # 1KB file
#     content = "A" * 1024
#     with open(os.path.join(SAMPLES_PATH, "1k.html"), "w") as f:
#         f.write(content)

#     # index.html
#     file_path = os.path.join(SAMPLES_PATH, "index.html")

#     with open(file_path, "w") as f:
#         f.write("<!DOCTYPE html>\n")
#         f.write("<html lang='en'>\n")
#         f.write("<head>\n")
#         f.write("    <meta charset='UTF-8'>\n")
#         f.write("    <meta name='viewport' content='width=device-width, initial-scale=1.0'>\n")
#         f.write("    <title>ICWS Test Page</title>\n")
#         f.write("    <style>\n")
#         f.write("        body { font-family: Arial, sans-serif; margin: 40px; }\n")
#         f.write("        h1 { color: #2c3e50; }\n")
#         f.write("        .card { background: #f4f4f4; padding: 15px; margin-bottom: 10px; border-radius: 5px; }\n")
#         f.write("    </style>\n")
#         f.write("</head>\n")
#         f.write("<body>\n")
#         f.write("    <h1>ICWS Web Server Test</h1>\n")

#         # Generate multiple content sections
#         for i in range(100):   # increase this number to make file larger
#             f.write(f"    <div class='card'>\n")
#             f.write(f"        <h2>Section {i+1}</h2>\n")
#             f.write(f"        <p>This is automatically generated content block {i+1} for performance testing.</p>\n")
#             f.write("    </div>\n")

#         f.write("</body>\n")
#         f.write("</html>\n")

#     print("✅ Sample files created")

# # ------------------------------------------------
# # Docker
# # ------------------------------------------------

# def start_container():
#     print("🐳 Starting container...")

#     cmd = [
#     "docker", "run", "-d",
#     "--name", CONTAINER_NAME,
#     "--memory=2g",
#     "--cpus=2.0",
#     "--pids-limit=128",
#     "--network=none",                     # keep sandboxed
#     "-v", f"{PROJECT_PATH}:/sandbox",
#     "-v", f"{SAMPLES_PATH}:/sandbox/grader_samples",
#     "-w", "/sandbox/projects/p2",         # correct working directory
#     IMAGE_NAME,
#     "sleep", "600"
# ]

#     result = run_cmd(cmd)

#     if result.returncode != 0:
#         print("❌ Failed to start container")
#         print(result.stderr)
#         sys.exit(1)

# def stop_container():
#     subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)

# # ------------------------------------------------
# # Build + Server
# # ------------------------------------------------

# def build_project():
#     print("🔨 Building project...")

#     out, err, code = exec_in_container("make")

#     if code != 0:
#         print("❌ Build failed")
#         print("STDOUT:")
#         print(out)
#         print("STDERR:")
#         print(err)
#         return False

#     print("✅ Build success")
#     return True

# def start_server():
#     print("🚀 Starting server...")

#     exec_in_container(
#         "nohup ./icws "
#         "--port 9000 "
#         "--root /sandbox/grader_samples/ "
#         "--numThreads 4 "
#         "--timeout 10 "
#         "--cgiHandler ./cgi-demo/dumper.py"
#         "> server.log 2>&1 &"
#     )

#     # Wait for port
#     for _ in range(20):
#         out, _, _ = exec_in_container("ss -ltn | grep 9000")
#         if out:
#             print("✅ Server listening on port 9000")
#             return
#         time.sleep(0.5)

#     print("❌ Server did not open port 9000")
#     print(exec_in_container("cat server.log"))
#     sys.exit(1)

# def stop_server():
#     exec_in_container("pkill icws")

# ------------------------------------------------
# Testing
# ------------------------------------------------
def normalize_http_response(resp: str) -> str:
    lines = resp.splitlines()
    filtered = []

    for line in lines:
        line = line.strip()

        if not line:
            continue

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
        if line[0].isdigit() and "----" in line:
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

    # Keep status line separate
    status_line = filtered[0]
    headers = filtered[1:]

    # Sort headers to ignore order differences
    headers_sorted = sorted(headers, key=lambda x: x.lower())

    return "\n".join([status_line] + headers_sorted).strip()

# -----------------------------
# Extract HTTP status code
# -----------------------------
def extract_status_code(resp: str):
    """
    Extracts HTTP status code from response.
    Example: HTTP/1.1 501 Not Implemented
    """
    match = re.search(r"HTTP/\d\.\d\s+(\d{3})", resp)
    if match:
        return match.group(1)

    # fallback if expected file only contains "501"
    match = re.search(r"\b(\d{3})\b", resp)
    if match:
        return match.group(1)

    return None



def run_tests_m1(student_name="unknown_student"):
    with open("tests.json") as f:
        data = json.load(f)

    # Milestone 1 is index 0
    milestone = data["milestones"][0]
    print(f"\n===== {milestone['name']} =====")

    passed = 0
    total_tests = len(milestone["tests"])
    test_history = []  # 📝 Tracks each test for the JSON log

    for test in milestone["tests"]:
        print(f"\nRunning {test['name']}...")
        
        # Initialize log entry
        test_log = {
            "test_name": test["name"],
            "command": test["command"],
            "mode": test.get("mode", "full"),
            "result": "FAIL",
            "details": ""
        }

        # Execute command in container
        stdout, stderr, code = exec_in_container(test["command"])
        combined_output = stdout + "\n" + stderr
        test_log["output_raw"] = combined_output

        # Load expected output
        if not os.path.exists(test["expected"]):
            test_log["details"] = f"Expected file missing: {test['expected']}"
            test_history.append(test_log)
            print(f"  ❌ {test_log['details']}")
            continue

        with open(test["expected"]) as ef:
            expected = ef.read()

        mode = test_log["mode"]

        # ------------------------
        # STATUS MODE
        # ------------------------
        if mode == "status":
            actual_code = extract_status_code(stdout)
            expected_code = extract_status_code(expected)

            if actual_code == expected_code:
                test_log["result"] = "PASS"
                passed += 1
            else:
                test_log["details"] = f"Status mismatch. Expected: {expected_code}, Actual: {actual_code}"

        # ------------------------
        # FULL MODE (Diff based)
        # ------------------------
        else:
            # Note: normalize_http_response and extract_status_code 
            # must be defined in your utility section
            actual_norm = normalize_http_response(stdout).lower()
            expected_norm = normalize_http_response(expected).lower()

            if actual_norm == expected_norm:
                test_log["result"] = "PASS"
                passed += 1
            else:
                # Generate diff for the "details" field in JSON
                diff = list(difflib.unified_diff(
                    expected_norm.splitlines(),
                    actual_norm.splitlines(),
                    lineterm=""
                ))
                test_log["details"] = "Full response mismatch."
                test_log["diff"] = diff

        test_history.append(test_log)
        print(f"  {'✅' if test_log['result'] == 'PASS' else '❌'} {test_log['result']}")

    # ── Final Scoring Logic ──
    weight_score = passed * milestone["weight"]

    # ── 📝 JSON LOGGING BLOCK ──
    # Pull internal server log for debugging C-level errors (segfaults, etc.)
    server_stdout, _, _ = exec_in_container("cat server.log 2>/dev/null || echo 'No server.log found'")
    
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

    log_filename = f"results/result_M1_{student_name}.json"
    with open(log_filename, "w") as jf:
        json.dump(final_log, jf, indent=4)
    
    print(f"\n{milestone['name']} Score: {passed}/{total_tests}")
    print(f"📂 Full result log saved to: {log_filename}")

    return [passed, weight_score]