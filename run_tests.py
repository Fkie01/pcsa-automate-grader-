import difflib
import json
import os
import shutil
import subprocess
import sys
import time

CONTAINER_NAME = "grader-test"
IMAGE_NAME = "c-grader"
PROJECT_PATH = "/Users/fkie01/developer/source/cs227/a02-ic-web-server-Fkie01"
SAMPLES_PATH = os.path.abspath("auto_samples")

# ------------------------------------------------
# Utility
# ------------------------------------------------

def run_cmd(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)

def exec_in_container(command):
    cmd = ["docker", "exec", CONTAINER_NAME, "bash", "-c", command]
    result = run_cmd(cmd)
    return result.stdout.strip(), result.stderr.strip(), result.returncode

# ------------------------------------------------
# Setup Sample Files
# ------------------------------------------------

def create_samples():
    print("📁 Creating grader sample files...")

    if os.path.exists(SAMPLES_PATH):
        shutil.rmtree(SAMPLES_PATH)

    os.makedirs(SAMPLES_PATH)

    # 1KB file
    content = "A" * 1024
    with open(os.path.join(SAMPLES_PATH, "1k.html"), "w") as f:
        f.write(content)

    # index.html
    with open(os.path.join(SAMPLES_PATH, "index.html"), "w") as f:
        f.write("<h1>ICWS TEST</h1>")

    print("✅ Sample files created")

# ------------------------------------------------
# Docker
# ------------------------------------------------

def start_container():
    print("🐳 Starting container...")

    cmd = [
    "docker", "run", "-d",
    "--name", CONTAINER_NAME,
    "--memory=2g",
    "--cpus=2.0",
    "--pids-limit=128",
    "--network=none",                     # keep sandboxed
    "-v", f"{PROJECT_PATH}:/sandbox",
    "-v", f"{SAMPLES_PATH}:/sandbox/grader_samples",
    "-w", "/sandbox/projects/p2",         # correct working directory
    IMAGE_NAME,
    "sleep", "600"
]

    result = run_cmd(cmd)

    if result.returncode != 0:
        print("❌ Failed to start container")
        print(result.stderr)
        sys.exit(1)

def stop_container():
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)

# ------------------------------------------------
# Build + Server
# ------------------------------------------------

def build_project():
    print("🔨 Building project...")

    out, err, code = exec_in_container("make")

    if code != 0:
        print("❌ Build failed")
        print("STDOUT:")
        print(out)
        print("STDERR:")
        print(err)
        return False

    print("✅ Build success")
    return True

def start_server():
    print("🚀 Starting server...")

    exec_in_container(
        "nohup ./icws "
        "--port 9000 "
        "--root /sandbox/grader_samples/ "
        "--numThreads 4 "
        "--timeout 10 "
        "--cgiHandler ./cgi-demo/dumper.py"
        "> server.log 2>&1 &"
    )

    # Wait for port
    for _ in range(20):
        out, _, _ = exec_in_container("ss -ltn | grep 9000")
        if out:
            print("✅ Server listening on port 9000")
            return
        time.sleep(0.5)

    print("❌ Server did not open port 9000")
    print(exec_in_container("cat server.log"))
    sys.exit(1)

def stop_server():
    exec_in_container("pkill icws")

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

        # Remove curl debug & progress junk
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

        # Remove "< " prefix from verbose headers
        if line.startswith("< "):
            line = line[2:]

        # Ignore dynamic headers
        if line.startswith("Date:"):
            continue
        if line.startswith("Last-Modified:"):
            continue

        filtered.append(line)

    return "\n".join(filtered).strip()


def run_tests():
    with open("tests.json") as f:
        data = json.load(f)

    total_passed = 0
    total_tests = 0

    for milestone in data["milestones"]:
        print(f"\n===== {milestone['name']} =====")

        passed = 0
        tests = milestone["tests"]

        for test in tests:
            print(f"\nRunning {test['name']}...")

            stdout, stderr, code = exec_in_container(test["command"])

            with open(test["expected"]) as ef:
                expected = ef.read()

            actual_norm = normalize_http_response(stdout)
            expected_norm = normalize_http_response(expected)

            total_tests += 1

            if actual_norm == expected_norm:
                print("✅ PASS")
                passed += 1
                total_passed += 1
            else:
                print("❌ FAIL")
                diff = difflib.unified_diff(
                    expected_norm.splitlines(),
                    actual_norm.splitlines(),
                    lineterm=""
                )
                print("\n".join(diff))

        print(f"\n{milestone['name']} Score: {passed}/{len(tests)}")

    print(f"\nFINAL SCORE: {total_passed}/{total_tests}")

# ------------------------------------------------
# Main
# ------------------------------------------------

if __name__ == "__main__":
    try:
        create_samples()
        start_container()

        if not build_project():
            stop_container()
            sys.exit(1)

        start_server()
        run_tests()

    finally:
        stop_server()
        stop_container()