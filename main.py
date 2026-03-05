import difflib
import json
import os
import shutil
import subprocess
import sys
import time
import re
from unittest import result

from docker_container import create_samples, start_container, stop_container, build_project, start_server, stop_server
from m1_test import run_tests_m1
from m2_test import run_tests_m2
from write_csv import write_student_result

PROJECT_PATH = "/Users/fkie01/developer/source/cs227/a02-ic-web-server-Fkie01"



if __name__ == "__main__":
    try:
        create_samples()
        start_container(PROJECT_PATH)

        if not build_project():
            stop_container()
            sys.exit(1)

        start_server()
        result_m1 = run_tests_m1()
        result_m2 = run_tests_m2()
        result = result_m1 + result_m2
        for i in range(2):
            result.append(0)
        write_student_result("results.csv", "Fkie01", *result)



    finally:
        stop_server()
        stop_container()