"""Run the unit tests at GitHub.

Same tests as ``python -m unittest discover -s comic_builder -t .``, with two
additions for unattended runs: each failure is printed as soon as it happens,
and a test that runs longer than PER_TEST_LIMIT seconds stops the run with a
report of where every thread was waiting.
"""
import faulthandler
import sys
import unittest

PER_TEST_LIMIT = 180


class ImmediateResult(unittest.TextTestResult):
    def startTest(self, test):
        faulthandler.dump_traceback_later(PER_TEST_LIMIT, exit=True)
        super().startTest(test)

    def stopTest(self, test):
        faulthandler.cancel_dump_traceback_later()
        super().stopTest(test)

    def _report(self, kind, test, err):
        self.stream.writeln(f"\n----- {kind}: {test.id()} -----")
        self.stream.writeln(self._exc_info_to_string(err, test))
        self.stream.flush()

    def addError(self, test, err):
        super().addError(test, err)
        self._report("ERROR", test, err)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._report("FAIL", test, err)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover("comic_builder", top_level_dir=".")
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2, resultclass=ImmediateResult)
    sys.exit(0 if runner.run(suite).wasSuccessful() else 1)
