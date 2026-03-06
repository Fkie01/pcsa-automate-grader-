#!/usr/bin/env python3
import os, sys

print('Content-Type: text/plain')
print()

for key, value in sorted(os.environ.items()):
    print(f'{key}={value}')

content_length = os.environ.get('CONTENT_LENGTH', '0')
try:
    length = int(content_length)
except ValueError:
    length = 0

if length > 0:
    body = sys.stdin.buffer.read(length)
    print(f'REQUEST_BODY={body.decode(errors="replace")}')
