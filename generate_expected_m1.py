import os
import subprocess
import json

BASE_URL = "http://127.0.0.1:9000"
OUTPUT_DIR = "expected/m1"
TEST_FILE = "tests.json"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def run_command(command):
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True
    )
    return result.stdout


def generate_expected():
    with open(TEST_FILE) as f:
        data = json.load(f)

    for milestone in data["milestones"]:
        for test in milestone["tests"]:

            filename = os.path.basename(test["expected"])
            output_path = os.path.join(OUTPUT_DIR, filename)

            print(f"Generating {filename}...")

            stdout = run_command(test["command"])

            # STATUS MODE → save only status line
            if test.get("mode") == "status":
                status_line = None
                for line in stdout.splitlines():
                    if line.startswith("HTTP/"):
                        status_line = line
                        break

                if status_line is None:
                    status_line = stdout.strip()

                with open(output_path, "w") as f:
                    f.write(status_line + "\n")

            # FULL MODE → save entire response
            else:
                with open(output_path, "w") as f:
                    f.write(stdout)

            print("  Done.")

    print("\nAll expected files generated successfully.")


if __name__ == "__main__":
    generate_expected()