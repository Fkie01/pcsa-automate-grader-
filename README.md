# pcsa-automate-grader
This is repo for automate grader application for pcsa class

** Currently is just a simple python script script that work on GET, HEAD HTTP method
1. After git clone to your machine 
2. go to directory with Dockerfile
``` bash
docker build -t my-image-name .
```
3. Then change the Project_PATH in the run_test.py to be your web server folder ex.
``` bash
/Users/fkie01/developer/source/cs227/a02-ic-web-server-Fkie01
```
4. After that run the script 
``` bash
python run_tests.py
```
--- 
You can create a new test case on the tests.json with name of testcase and command, and also add the expected result in expected folde.
Furthermore, the sample file is create only 2 file you can add it more later. 

Requirement
- python3 
- Docker