# showEachTestRuntime
# usage:

- You can put the script anywhere in your django project

- Then you need to use the following line of code in settings.py

  ```
  TEST_RUNNER = 'path.DiscoverSlowestTestsRunner' # the path is where you put the script
  ```

- When you run the djagno test you can have addition commands:

  `--time=300`,The console will output the execution time of each test instead of the test run info. while the time is greater than 300 ms, it will turn red.

  `--report=300` When all test runs are completed, the console will output a test report with a run time greater than 300 ms.

  `--limit=15` The final test report will only show the slowest 15 data



