import glob
import json
import time
import sys
import os
import os.path
import logging
from unittest import TestSuite, TestLoader
from unittest.runner import TextTestRunner, registerResult
from unittest.suite import _isnotsuite
from django.test.runner import DiscoverRunner
from django.conf import settings

try:  # pragma: no cover
    import freezegun


    def _time():
        return freezegun.api.real_time()
except ImportError:  # pragma: no cover
    def _time():
        return time.time()

time_only = False
report = False
# if time > valid(ms) the result will turn red
valid_time = 300
# the final report will only count results greater than slow_test_threshold_ms(ms)
slow_test_threshold_ms = 0
# the final report will display the longest slow_test_threshold_ms data
num_slow_tests = None

if 'test' in sys.argv:
    try:
        for i in sys.argv[1:]:
            if i.startswith("--time"):
                sys.argv.remove(i)
                try:
                    param = i.split("=")[1]
                    valid_time = int(param) / 1000 if int(param) > 0 else valid_time
                except IndexError:
                    pass
                finally:
                    # disable logging during tests
                    time_only = True
                    logging.disable(logging.CRITICAL)

            if i.startswith("--report"):
                sys.argv.remove(i)
                try:
                    param = i.split("=")[1]
                    slow_test_threshold_ms = int(param) if int(param) > 0 else slow_test_threshold_ms
                except IndexError:
                    pass
                finally:
                    report = True

            if i.startswith("--limit"):
                sys.argv.remove(i)
                try:
                    param = i.split("=")[1]
                    num_slow_tests = int(param) if int(param) > 0 else num_slow_tests
                except IndexError:
                    pass

    except Exception:
        print("Error invalid params")


class TimingTextTestRunner(TextTestRunner):
    def run(self, test):
        "Run the given test case or test suite."
        result = self._makeResult()
        registerResult(result)
        result.failfast = self.failfast
        result.buffer = self.buffer
        startTime = time.time()
        startTestRun = getattr(result, 'startTestRun', None)
        if startTestRun is not None:
            startTestRun()
        try:
            test(result)
        finally:
            stopTestRun = getattr(result, 'stopTestRun', None)
            if stopTestRun is not None:
                stopTestRun()
        stopTime = time.time()
        timeTaken = stopTime - startTime
        result.printErrors()
        if hasattr(result, 'separator2'):
            self.stream.writeln(result.separator2)
        run = result.testsRun
        self.stream.writeln("Ran %d test%s in %.3fs" %
                            (run, run != 1 and "s" or "", timeTaken))
        self.stream.writeln()

        expectedFails = unexpectedSuccesses = skipped = 0
        try:
            results = map(len, (result.expectedFailures,
                                result.unexpectedSuccesses,
                                result.skipped))
        except AttributeError:
            pass
        else:
            expectedFails, unexpectedSuccesses, skipped = results

        infos = []
        if not result.wasSuccessful():
            self.stream.write("FAILED")
            failed, errored = map(len, (result.failures, result.errors))
            if failed:
                infos.append("failures=%d" % failed)
            if errored:
                infos.append("errors=%d" % errored)
        else:
            self.stream.write("OK")
        if skipped:
            infos.append("skipped=%d" % skipped)
        if expectedFails:
            infos.append("expected failures=%d" % expectedFails)
        if unexpectedSuccesses:
            infos.append("unexpected successes=%d" % unexpectedSuccesses)
        if infos:
            self.stream.writeln(" (%s)" % (", ".join(infos),))
        else:
            self.stream.write("\n")
        # save timeTaken for later use in report
        result.timeTaken = timeTaken
        return result


class TimingSuite(TestSuite):
    """
    TestSuite wrapper that times each test.
    """

    def save_test_time(self, test_name, duration):
        file_prefix = getattr(
            settings, 'TESTS_REPORT_TMP_FILES_PREFIX', '_tests_report_'
        )
        file_name = '{}{}.txt'.format(file_prefix, os.getpid())
        with open(file_name, "a+") as f:
            f.write("{name},{duration:.6f}\n".format(
                name=test_name, duration=duration
            ))

    def run(self, result, debug=False):
        topLevel = False
        if getattr(result, '_testRunEntered', False) is False:
            result._testRunEntered = topLevel = True

        for test in self:
            if result.shouldStop:
                break

            start_time = _time()

            if _isnotsuite(test):
                self._tearDownPreviousClass(test, result)
                self._handleModuleFixture(test, result)
                self._handleClassSetUp(test, result)
                result._previousTestClass = test.__class__

                if (getattr(test.__class__, '_classSetupFailed', False) or
                        getattr(result, '_moduleSetUpFailed', False)):
                    continue

            if not debug:
                test(result)
            else:
                test.debug()
            self.save_test_time(str(test), _time() - start_time)

            if time_only:
                timing = _time() - start_time
                if timing > valid_time:
                    print("\n\033[31m{t:.4f}s\033[0m \033[94m{f}\033[0m".format(f=str(test), t=float(timing)))
                else:
                    print("\n\033[32m{t:.4f}s\033[0m \033[94m{f}\033[0m".format(f=str(test), t=float(timing)))

        if topLevel:
            self._tearDownPreviousClass(None, result)
            self._handleModuleTearDown(result)
            result._testRunEntered = False

        return result


class TimingLoader(TestLoader):
    suiteClass = TimingSuite


class DiscoverSlowestTestsRunner(DiscoverRunner):
    """
    Runner that extends Django's DiscoverRunner to time the tests.
    """
    test_suite = TimingSuite
    test_loader = TimingLoader()
    test_runner = TimingTextTestRunner

    def __init__(self, report_path=None, generate_report=False, **kwargs):
        super(DiscoverSlowestTestsRunner, self).__init__(**kwargs)
        self.report_path = report_path[0] if report_path else None
        self.should_generate_report = generate_report

    @classmethod
    def add_arguments(cls, parser):
        DiscoverRunner.add_arguments(parser)
        parser.add_argument(
            '--slowreport',
            action='store_true',
            dest='generate_report',
            help='Generate a report of slowest tests',
        )
        parser.add_argument(
            '--slowreportpath',
            nargs=1,
            dest='report_path',
            help='Save report to given file'
        )

    def generate_report(self, test_results, result):
        test_result_count = len(test_results)
        SLOW_TEST_THRESHOLD_MS = slow_test_threshold_ms

        if self.report_path:
            data = {
                'threshold': SLOW_TEST_THRESHOLD_MS,
                'slower_tests': [
                    {"name": func_name, "execution_time": float(timing)}
                    for func_name, timing in test_results
                ],
                'nb_tests': result.testsRun,
                'nb_failed': len(result.errors + result.failures),
                'total_execution_time': result.timeTaken,
            }
            with open(self.report_path, 'w') as outfile:
                json.dump(data, outfile)
        else:
            if test_result_count:
                if SLOW_TEST_THRESHOLD_MS:
                    print("\n{r} slowest tests over {ms}ms:".format(
                        r=test_result_count, ms=SLOW_TEST_THRESHOLD_MS)
                    )

            if report:
                for func_name, timing in test_results:
                    if timing > valid_time:
                        print("\n\033[31m{t:.4f}s\033[0m \033[94m{f}\033[0m".format(f=func_name, t=float(timing)))
                    else:
                        print("\n\033[32m{t:.4f}s\033[0m \033[94m{f}\033[0m".format(f=func_name, t=float(timing)))

            if not len(test_results) and SLOW_TEST_THRESHOLD_MS:
                print("\nNo tests slower than {ms}ms".format(
                    ms=SLOW_TEST_THRESHOLD_MS)
                )

    def read_timing_files(self):
        file_prefix = getattr(
            settings, 'TESTS_REPORT_TMP_FILES_PREFIX', '_tests_report_'
        )
        files = glob.glob("{}*.txt".format(file_prefix))
        for report_file in files:
            yield report_file

    def get_timings(self):
        timings = []
        for report_file in self.read_timing_files():
            with open(report_file, "r") as f:
                for line in f:
                    name, duration = line.strip('\n').split(',')
                    timings.append((name, float(duration)))
            os.remove(report_file)
        return timings

    def remove_timing_tmp_files(self):
        for report_file in self.read_timing_files():
            os.remove(report_file)

    def suite_result(self, suite, result):
        return_value = super(DiscoverSlowestTestsRunner, self).suite_result(
            suite, result
        )

        should_generate_report = (
                getattr(settings, 'ALWAYS_GENERATE_SLOW_REPORT', True) or
                self.should_generate_report
        )
        if not should_generate_report:
            self.remove_timing_tmp_files()
            return return_value

        # Grab slowest tests
        timings = self.get_timings()
        by_time = sorted(timings, key=lambda x: x[1], reverse=True)

        if by_time is not None and num_slow_tests:
            by_time = by_time[:num_slow_tests]

        test_results = by_time

        if slow_test_threshold_ms:
            # Filter tests by threshold
            test_results = []

            for result in by_time:
                # Convert test time from seconds to miliseconds for comparison
                result_time_ms = result[1] * 1000

                # If the test was under the threshold
                # don't show it to the user
                if result_time_ms < slow_test_threshold_ms:
                    continue

                test_results.append(result)

        self.generate_report(test_results, result)
        return return_value
