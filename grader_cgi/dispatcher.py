#!/usr/bin/env python3
import os, sys, subprocess

script_dir = os.path.dirname(os.path.abspath(__file__))

def get_target_name():
    candidates = [
        os.environ.get('PATH_INFO', ''),
        os.environ.get('REQUEST_URI', ''),
        os.environ.get('SCRIPT_NAME', ''),
        os.environ.get('PATH_TRANSLATED', ''),
    ]
    for src in candidates:
        seg = src.strip().split('/')[-1].split('?')[0].strip()
        if seg and seg not in ('cgi', ''):
            return seg
    return ''

ROUTES = {
    'test'  : os.path.join(script_dir, 'test'),
    'slow'  : os.path.join(script_dir, 'slow.py'),
    'error' : os.path.join(script_dir, 'error.py'),
}

target_name = get_target_name()
target      = ROUTES.get(target_name,
                         os.path.join(script_dir, 'dumper.py'))

if not os.path.isfile(target):
    print('Content-Type: text/plain')
    print()
    print(f'ERROR: dispatcher target not found: {target}')
    sys.exit(1)

proc = subprocess.run(
    [target],
    stdin=sys.stdin.buffer,
    stdout=sys.stdout.buffer,
    stderr=sys.stderr.buffer,
    env=os.environ.copy(),
)
sys.exit(proc.returncode)
