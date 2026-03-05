import os
import subprocess
import json
import re

TEST_FILE = "tests.json"


def run_command(command):
    result = subprocess.run(
        ["bash", "-c", command],   # must be bash for background jobs (&, wait)
        capture_output=True,
        text=True
    )
    return result.stdout + result.stderr


# -----------------------------
# STATUS EXTRACTION
# -----------------------------
def extract_status(output):
    """Return the FIRST HTTP status line."""
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("< "):
            line = line[2:]
        if re.match(r"HTTP/[\d.]+\s+\d{3}", line):
            return line.strip()
    return ""


def extract_last_status(output):
    """Return the LAST HTTP status line (for 2-request tests)."""
    last = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("< "):
            line = line[2:]
        if re.match(r"HTTP/[\d.]+\s+\d{3}", line):
            last = line.strip()
    return last or ""


# -----------------------------
# CONNECTION HEADER EXTRACTION
# -----------------------------
def extract_connection_header(output):
    """Return the Connection: header value."""
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("< "):
            line = line[2:]
        if line.lower().startswith("connection:"):
            return line.strip()
    return ""


# -----------------------------
# PERSISTENT MODE EXTRACTION
# -----------------------------
def extract_persistent_info(output):
    """Look for Re-using existing connection marker."""
    for line in output.splitlines():
        if "Re-using existing connection" in line:
            return "REUSED_CONNECTION"
    return ""


# -----------------------------
# PERFORMANCE EXTRACTION
# -----------------------------
def extract_performance_metrics(output):
    """
    Extract RPS and failed requests as plain numbers so the
    runner's regex always matches cleanly.
    Output format:
        Requests/sec: 1234.56
        Failed requests: 0
    """
    rps = None
    failed = 0

    for pattern in [
        r"Requests per second:\s*([0-9.]+)",   # ab
        r"Requests/sec:\s*([0-9.]+)",           # wrk / custom
        r"([0-9.]+)\s*requests/sec",            # wrk alt
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


# -----------------------------
# GENERATE EXPECTED (M2 ONLY)
# -----------------------------
def generate_expected():
    with open(TEST_FILE) as f:
        data = json.load(f)

    print("\n===== Generating Expected Outputs (M2) =====")

    milestone = data["milestones"][1]

    failed_tests = []

    for test in milestone["tests"]:

        output_path = test["expected"]
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        print(f"\nGenerating {test['name']}...")
        print(f"  Command : {test['command'][:80]}...")

        raw_output = run_command(test["command"])
        mode = test.get("mode", "full")

        # ── extract by mode ──────────────────────────────────
        if mode == "status":
            result = extract_status(raw_output)

        elif mode == "status_last":
            result = extract_last_status(raw_output)

        elif mode == "close":
            result = extract_connection_header(raw_output)

        elif mode == "performance":
            result = extract_performance_metrics(raw_output)

        elif mode == "persistent":
            result = extract_persistent_info(raw_output)

        else:
            # full mode — not recommended but kept as fallback
            result = raw_output.strip()

        # ── validate result is not empty ─────────────────────
        if not result.strip():
            print(f"  ⚠ WARNING: Empty result for {test['name']}")
            print(f"  Raw output was:\n{raw_output[:300]}")
            failed_tests.append(test["name"])
            # still write the file so the runner doesn't crash,
            # but flag it clearly
            result = f"GENERATION_FAILED_{mode.upper()}"

        with open(output_path, "w") as f:
            f.write(result.strip() + "\n")

        print(f"  Mode    : {mode}")
        print(f"  Saved   : {result.strip()[:120]}")
        print(f"  → {output_path}")

    # ── final summary ─────────────────────────────────────────
    print("\n" + "="*50)
    if failed_tests:
        print(f"⚠ WARNING: {len(failed_tests)} test(s) had empty output:")
        for name in failed_tests:
            print(f"  - {name}")
        print("Re-run generate after confirming the server is running.")
    else:
        print("✅ All expected files generated successfully.")


if __name__ == "__main__":
    generate_expected()