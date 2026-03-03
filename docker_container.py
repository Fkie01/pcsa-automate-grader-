import difflib
import json
import os
import shutil
import subprocess
import sys
import time
import re

CONTAINER_NAME = "grader-test"
IMAGE_NAME = "c-grader"
# PROJECT_PATH = "/Users/fkie01/developer/source/cs227/a02-ic-web-server-Fkie01"
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
    file_path = os.path.join(SAMPLES_PATH, "index.html")

    with open(file_path, "w") as f:
        f.write("<!DOCTYPE html>\n")
        f.write("<html lang='en'>\n")
        f.write("<head>\n")
        f.write("    <meta charset='UTF-8'>\n")
        f.write("    <meta name='viewport' content='width=device-width, initial-scale=1.0'>\n")
        f.write("    <title>ICWS Test Page</title>\n")
        f.write("    <style>\n")
        f.write("        body { font-family: Arial, sans-serif; margin: 40px; }\n")
        f.write("        h1 { color: #2c3e50; }\n")
        f.write("        .card { background: #f4f4f4; padding: 15px; margin-bottom: 10px; border-radius: 5px; }\n")
        f.write("    </style>\n")
        f.write("</head>\n")
        f.write("<body>\n")
        f.write("    <h1>ICWS Web Server Test</h1>\n")

        # Generate multiple content sections
        for i in range(100):   # increase this number to make file larger
            f.write(f"    <div class='card'>\n")
            f.write(f"        <h2>Section {i+1}</h2>\n")
            f.write(f"        <p>This is automatically generated content block {i+1} for performance testing.</p>\n")
            f.write("    </div>\n")

        f.write("</body>\n")
        f.write("</html>\n")

    print("✅ Sample files created")

# ------------------------------------------------
# Docker
# ------------------------------------------------

def start_container(project_path):
    print("🐳 Starting container...")

    cmd = [
    "docker", "run", "-d",
    "--name", CONTAINER_NAME,
    "--memory=2g",
    "--cpus=2.0",
    "--pids-limit=128",
    "--network=none",                     # keep sandboxed
    "-v", f"{project_path}:/sandbox",
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
