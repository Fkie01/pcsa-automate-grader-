import difflib
import json
import os
import shutil
import subprocess
import sys
import time
import re
import stat

CONTAINER_NAME = "grader-test"
IMAGE_NAME     = "c-grader"

# ── grader-owned directories (on host) ───────────────────
SAMPLES_PATH    = os.path.abspath("grader_samples")
GRADER_CGI_PATH = os.path.abspath("grader_cgi")

# ── paths inside the container ───────────────────────────
CONTAINER_SAMPLES = "/grader/samples"
CONTAINER_CGI     = "/grader/cgi"
CONTAINER_PROJECT = "/sandbox"

# ------------------------------------------------
# Utility
# ------------------------------------------------

def run_cmd(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)

def exec_in_container(command):
    """
    Run command inside container via stdin.
    Merges stderr into stdout so CGI body output isn't lost.
    """
    cmd = ["docker", "exec", "-i", CONTAINER_NAME, "bash"]
    result = subprocess.run(
        cmd,
        input=command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,   # merge stderr into stdout
        text=True
    )
    return result.stdout.strip(), "", result.returncode

def _make_executable(path):
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

def _write_file(path, content, executable=False):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    if executable:
        _make_executable(path)
    print(f"   {'✅' if executable else '📄'} {os.path.relpath(path)}")

# ------------------------------------------------
# Setup Sample Files
# ------------------------------------------------

def create_samples():
    print("📁 Creating grader sample files...")

    for d in [SAMPLES_PATH, GRADER_CGI_PATH]:
        if os.path.exists(d):
            shutil.rmtree(d)
        os.makedirs(d)

    # M2 — 1KB file
    with open(os.path.join(SAMPLES_PATH, "1k.html"), "w") as f:
        f.write("A" * 1024)

    # M3 — 4096-byte file for static benchmark
    with open(os.path.join(SAMPLES_PATH, "test.html"), "wb") as f:
        f.write(b"\x00" * 4096)

    # M2/M3 — index.html
    with open(os.path.join(SAMPLES_PATH, "index.html"), "w") as f:
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
        for i in range(100):
            f.write(f"    <div class='card'>\n")
            f.write(f"        <h2>Section {i+1}</h2>\n")
            f.write(f"        <p>This is automatically generated content block {i+1} for performance testing.</p>\n")
            f.write("    </div>\n")
        f.write("</body>\n")
        f.write("</html>\n")

    # dispatcher
    _write_file(
        os.path.join(GRADER_CGI_PATH, "dispatcher.py"),
        "#!/usr/bin/env python3\n"
        "import os, sys, subprocess\n"
        "\n"
        "script_dir = os.path.dirname(os.path.abspath(__file__))\n"
        "\n"
        "def get_target_name():\n"
        "    candidates = [\n"
        "        os.environ.get('PATH_INFO', ''),\n"
        "        os.environ.get('REQUEST_URI', ''),\n"
        "        os.environ.get('SCRIPT_NAME', ''),\n"
        "        os.environ.get('PATH_TRANSLATED', ''),\n"
        "    ]\n"
        "    for src in candidates:\n"
        "        seg = src.strip().split('/')[-1].split('?')[0].strip()\n"
        "        if seg and seg not in ('cgi', ''):\n"
        "            return seg\n"
        "    return ''\n"
        "\n"
        "ROUTES = {\n"
        "    'test'  : os.path.join(script_dir, 'test'),\n"
        "    'slow'  : os.path.join(script_dir, 'slow.py'),\n"
        "    'error' : os.path.join(script_dir, 'error.py'),\n"
        "}\n"
        "\n"
        "target_name = get_target_name()\n"
        "target      = ROUTES.get(target_name,\n"
        "                         os.path.join(script_dir, 'dumper.py'))\n"
        "\n"
        "if not os.path.isfile(target):\n"
        "    print('Content-Type: text/plain')\n"
        "    print()\n"
        "    print(f'ERROR: dispatcher target not found: {target}')\n"
        "    sys.exit(1)\n"
        "\n"
        "proc = subprocess.run(\n"
        "    [target],\n"
        "    stdin=sys.stdin.buffer,\n"
        "    stdout=sys.stdout.buffer,\n"
        "    stderr=sys.stderr.buffer,\n"
        "    env=os.environ.copy(),\n"
        ")\n"
        "sys.exit(proc.returncode)\n",
        executable=True
    )

    # dumper
    _write_file(
        os.path.join(GRADER_CGI_PATH, "dumper.py"),
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "\n"
        "print('Content-Type: text/plain')\n"
        "print()\n"
        "\n"
        "for key, value in sorted(os.environ.items()):\n"
        "    print(f'{key}={value}')\n"
        "\n"
        "content_length = os.environ.get('CONTENT_LENGTH', '0')\n"
        "try:\n"
        "    length = int(content_length)\n"
        "except ValueError:\n"
        "    length = 0\n"
        "\n"
        "if length > 0:\n"
        "    body = sys.stdin.buffer.read(length)\n"
        "    print(f'REQUEST_BODY={body.decode(errors=\"replace\")}')\n",
        executable=True
    )

    # slow
    _write_file(
        os.path.join(GRADER_CGI_PATH, "slow.py"),
        "#!/usr/bin/env python3\n"
        "import time\n"
        "print('Content-Type: text/plain')\n"
        "print()\n"
        "time.sleep(10)\n"
        "print('done')\n",
        executable=True
    )

    # error
    _write_file(
        os.path.join(GRADER_CGI_PATH, "error.py"),
        "#!/usr/bin/env python3\n"
        "print('Content-Type: text/plain')\n"
        "print()\n"
        "raise Exception('Intentional CGI failure for grader')\n",
        executable=True
    )

    # C binary
    c_src = os.path.join(GRADER_CGI_PATH, "test.c")
    c_bin = os.path.join(GRADER_CGI_PATH, "test")

    _write_file(
        c_src,
        '#include <stdio.h>\n'
        '#include <stdlib.h>\n'
        '#include <string.h>\n'
        '\n'
        'int main() {\n'
        '    printf("Content-Type: text/plain\\r\\n\\r\\n");\n'
        '    char *method = getenv("REQUEST_METHOD");\n'
        '    char *query  = getenv("QUERY_STRING");\n'
        '    char *length = getenv("CONTENT_LENGTH");\n'
        '    printf("METHOD=%s\\n", method ? method : "(null)");\n'
        '    printf("QUERY=%s\\n",  query  ? query  : "(empty)");\n'
        '    printf("LENGTH=%s\\n", length ? length : "(null)");\n'
        '    if (length && atoi(length) > 0) {\n'
        '        int len = atoi(length);\n'
        '        char *buf = malloc(len + 1);\n'
        '        if (buf) {\n'
        '            fread(buf, 1, len, stdin);\n'
        '            buf[len] = 0;\n'
        '            printf("BODY=%s\\n", buf);\n'
        '            free(buf);\n'
        '        }\n'
        '    }\n'
        '    return 0;\n'
        '}\n'
    )

    # test.c is written to host — compiled inside container in start_server()
    # macOS-compiled binaries cannot run on Linux/Ubuntu
    print(f"   📄 {os.path.relpath(c_src)} (will compile inside container)")

    print("\n✅ All grader files ready:")
    print(f"\n   Static  → host: {SAMPLES_PATH}/")
    print(f"             container: {CONTAINER_SAMPLES}/")
    print(f"\n   CGI     → host: {GRADER_CGI_PATH}/")
    print(f"             container: {CONTAINER_CGI}/")
    print(f"   dispatcher.py → dumper.py / test / slow.py / error.py")
    print(f"\n   ✋ Student project files are not touched.")


# ------------------------------------------------
# Docker
# ------------------------------------------------

def start_container(project_path):
    print("🐳 Starting container...")

    project_path = os.path.abspath(project_path)

    if not os.path.exists(project_path):
        print(f"❌ Project path not found: {project_path}")
        sys.exit(1)

    workdir = _find_workdir(project_path)
    print(f"   Working dir: {workdir}")

    cmd = [
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "--memory=2g",
        "--cpus=4.0",
        "--pids-limit=512",
        "--network=host",
        "-v", f"{project_path}:{CONTAINER_PROJECT}",
        "-v", f"{SAMPLES_PATH}:{CONTAINER_SAMPLES}:ro",
        "-v", f"{GRADER_CGI_PATH}:{CONTAINER_CGI}",
        "-w", workdir,
        IMAGE_NAME,
        "sleep", "600"
    ]

    result = run_cmd(cmd)

    if result.returncode != 0:
        print("❌ Failed to start container")
        print(result.stderr)
        sys.exit(1)

    print("✅ Container started")


def _find_workdir(project_path):
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if not d.startswith('.')
                   and d not in ['node_modules', '__pycache__', '.git']]
        if "Makefile" in files or "makefile" in files:
            rel = os.path.relpath(root, project_path)
            if rel == ".":
                return CONTAINER_PROJECT
            return f"{CONTAINER_PROJECT}/{rel}"
    return CONTAINER_PROJECT


def stop_container():
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)


# ------------------------------------------------
# Build + Server
# ------------------------------------------------

def build_project():
    print("🔨 Building project...")

    exec_in_container("make clean 2>/dev/null || true")
    out, err, code = exec_in_container("make")

    if code != 0:
        print("❌ Build failed")
        print("STDOUT:", out)
        print("STDERR:", err)
        return False

    out, _, _ = exec_in_container("ls -la icws 2>/dev/null || echo MISSING")
    if "MISSING" in out:
        print("❌ Build succeeded but icws binary not found")
        return False

    print("✅ Build success")
    return True


def start_server():
    print("🚀 Starting server...")

    # kill any server already running (e.g. started by student Makefile)
    exec_in_container("pkill -f icws 2>/dev/null || true")
    time.sleep(1)

    # compile test.c inside container — macOS binaries can't run on Linux
    print("   🔨 Compiling test binary inside container...")
    out, _, code = exec_in_container(
        f"gcc -o {CONTAINER_CGI}/test {CONTAINER_CGI}/test.c && echo OK"
    )
    if "OK" in out:
        print("   ✅ test binary compiled (Linux)")
    else:
        print(f"   ⚠ gcc failed inside container: {out[:200]}")

    # fix execute permissions — macOS Docker doesn't preserve them
    exec_in_container(
        f"chmod +x {CONTAINER_CGI}/dispatcher.py "
        f"{CONTAINER_CGI}/dumper.py "
        f"{CONTAINER_CGI}/slow.py "
        f"{CONTAINER_CGI}/error.py "
        f"{CONTAINER_CGI}/test "
        f"2>/dev/null || true"
    )

    exec_in_container(
        "nohup ./icws "
        "--port 9000 "
        f"--root {CONTAINER_SAMPLES} "
        "--numThreads 32 "
        "--timeout 5 "
        f"--cgiHandler {CONTAINER_CGI}/dispatcher.py "
        "> server.log 2>&1 &"
    )

    for _ in range(20):
        out, _, _ = exec_in_container("ss -ltn | grep 9000")
        if out:
            print("✅ Server listening on port 9000")
            return
        time.sleep(0.5)

    print("❌ Server did not open port 9000")
    log, _, _ = exec_in_container("cat server.log")
    print(log)
    sys.exit(1)


def stop_server():
    exec_in_container("pkill -f icws 2>/dev/null || true")