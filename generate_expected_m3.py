import os
import sys
import subprocess
import json
import re
import time

# ── config ────────────────────────────────────────────────
TEST_FILE     = "tests.json"
MILESTONE_IDX = 2              # M1=0, M2=1, M3=2
EXPECTED_DIR  = "expected/m3"  # all output files saved here

# ── key lines to extract per test (body_contains mode) ───
# Only these lines are saved to the expected file.
# Runner checks each one exists somewhere in actual output.
BODY_KEYS = {
    "CGI_Query_String": [
        "QUERY_STRING=name=ICWS",
        "REQUEST_METHOD=GET",
    ],
    "CGI_POST_Request": [
        "REQUEST_METHOD=POST",
        "REQUEST_BODY=name=ICWS",
    ],
    "CGI_Env_Vars": [
        "GATEWAY_INTERFACE=CGI/1.1",
        "REQUEST_METHOD=GET",
        "SERVER_PROTOCOL=HTTP/1.1",
        "HTTP_USER_AGENT=TestAgent",
        "HTTP_COOKIE=session=123",
        "HTTP_ACCEPT=text/html",
        "REMOTE_ADDR=127.0.0.1",
        "SERVER_PORT=9000",
        "QUERY_STRING=",
    ],
    "CGI_Env_GET_C_Binary": [
        "METHOD=GET",
        "QUERY=x=1",       # query string present
        "LENGTH=",         # LENGTH should be empty/null for GET
    ],
    "CGI_Env_POST_C_Binary": [
        "METHOD=POST",
        "QUERY=",          # query string empty for POST
        "LENGTH=3",        # x=1 is exactly 3 bytes
    ],
    "CGI_Large_POST": [
        "REQUEST_METHOD=POST",
        "CONTENT_LENGTH=10240",
    ],
}

# ------------------------------------------------
# Run command locally
# ------------------------------------------------
def run_command(command):
    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True
    )
    return result.stdout + result.stderr


# ------------------------------------------------
# Extractors
# ------------------------------------------------
def extract_status(output):
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("< "):
            line = line[2:]
        if re.match(r"HTTP/[\d.]+\s+\d{3}", line):
            return line.strip()
    return ""


def extract_last_status(output):
    last = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("< "):
            line = line[2:]
        if re.match(r"HTTP/[\d.]+\s+\d{3}", line):
            last = line.strip()
    return last or ""


def extract_body_contains(output, test_name):
    """
    If the test has specific key lines defined in BODY_KEYS,
    only save those lines that actually appear in the output.
    This avoids saving the entire env dump as expected output.
    """
    key_lines = BODY_KEYS.get(test_name)

    if key_lines:
        # save only the predefined key lines that exist in output
        found = []
        missing = []
        for line in key_lines:
            if line in output:
                found.append(line)
            else:
                missing.append(line)

        if missing:
            print(f"  ⚠ Key lines NOT found in output:")
            for m in missing:
                print(f"     missing: {m}")

        return "\n".join(found)

    # no key lines defined — save all non-empty body lines
    body_lines = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("*") or stripped.startswith(">"):
            continue
        if stripped.startswith("< "):
            continue
        if re.match(r"HTTP/[\d.]+\s+\d{3}", stripped):
            continue
        if stripped:
            body_lines.append(stripped)

    seen   = set()
    unique = []
    for line in body_lines:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    return "\n".join(unique)


def extract_connection_header(output):
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("< "):
            line = line[2:]
        if line.lower().startswith("connection:"):
            return line.strip()
    return ""


def extract_performance_metrics(output):
    rps    = None
    failed = 0

    for pattern in [
        r"Requests per second:\s*([0-9.]+)",
        r"Requests/sec:\s*([0-9.]+)",
        r"([0-9.]+)\s*requests/sec",
    ]:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            rps = float(match.group(1))
            break

    for pattern in [
        r"Failed requests:\s*(\d+)",
        r"Non-2xx or 3xx responses:\s*(\d+)",
        r"Failed:\s*(\d+)",
    ]:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            failed = int(match.group(1))
            break

    if rps is None:
        return ""

    return f"Requests/sec: {rps}\nFailed requests: {failed}"


# ------------------------------------------------
# Verify server is reachable
# ------------------------------------------------
def check_server():
    result = subprocess.run(
        ["bash", "-c",
         "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/index.html"],
        capture_output=True, text=True
    )
    return "200" in (result.stdout + result.stderr)


# ------------------------------------------------
# Generate
# ------------------------------------------------
def generate_expected():

    if not check_server():
        print("❌ Server not reachable at http://127.0.0.1:9000")
        print("   Start your reference icws server first:")
        print("   ./icws --port 9000 --root ./grader_samples \\")
        print("          --numThreads 4 --timeout 5 \\")
        print("          --cgiHandler ./grader_cgi/dispatcher.py")
        sys.exit(1)

    print("✅ Server reachable\n")

    if not os.path.exists(TEST_FILE):
        print(f"❌ {TEST_FILE} not found")
        sys.exit(1)

    with open(TEST_FILE) as f:
        data = json.load(f)

    milestones = data.get("milestones", [])
    if MILESTONE_IDX >= len(milestones):
        print(f"❌ milestones[{MILESTONE_IDX}] not found — only {len(milestones)} in file")
        sys.exit(1)

    milestone = milestones[MILESTONE_IDX]
    print(f"===== Generating: {milestone['name']} =====\n")

    os.makedirs(EXPECTED_DIR, exist_ok=True)

    failed_tests = []

    for test in milestone["tests"]:

        filename    = os.path.basename(test["expected"])
        output_path = os.path.join(EXPECTED_DIR, filename)
        test_name   = test["name"]

        print(f"Generating {test_name}...")
        print(f"  Command : {test['command'][:90]}")

        raw_output = run_command(test["command"])
        mode       = test.get("mode", "full")

        if mode == "status":
            result = extract_status(raw_output)
        elif mode == "status_last":
            result = extract_last_status(raw_output)
        elif mode == "close":
            result = extract_connection_header(raw_output)
        elif mode == "body_contains":
            result = extract_body_contains(raw_output, test_name)
        elif mode == "performance":
            result = extract_performance_metrics(raw_output)
        else:
            result = raw_output.strip()

        if not result.strip():
            print(f"  ⚠ WARNING: Empty result for {test_name}")
            print(f"  Raw output:\n{raw_output[:400]}\n")
            failed_tests.append(test_name)
            result = f"GENERATION_FAILED_{mode.upper()}"

        with open(output_path, "w") as f:
            f.write(result.strip() + "\n")

        print(f"  Mode    : {mode}")
        print(f"  Saved   : {result.strip()[:120]}")
        print(f"  → {output_path}\n")

    print("=" * 55)
    if failed_tests:
        print(f"⚠  {len(failed_tests)} test(s) had empty output:")
        for name in failed_tests:
            print(f"   - {name}")
    else:
        print(f"✅ All expected files saved to {EXPECTED_DIR}/")


# ------------------------------------------------
# Entry point
# ------------------------------------------------
if __name__ == "__main__":
    generate_expected()