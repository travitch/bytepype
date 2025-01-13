import json
import os
import pathlib
import sys
import tempfile
from typing import ClassVar, List
import unittest

import bytepype

# TODO: Other tests should:
#
# Validate the module name lookup code
#
# Validate the disassembly / conversion

class TestPype(unittest.TestCase):
    '''These tests validate that the message handling infrastructure is working correctly
    '''

    # The python path in place before the test runs
    #
    # We save this because we need to modify the path for each test and want to be able to restore
    # it when needed
    saved_pythonpath: ClassVar[List[str]]

    response_tempdir: ClassVar[tempfile.TemporaryDirectory]

    @classmethod
    def setUpClass(cls):
        cls.saved_pythonpath = sys.path
        cls.response_tempdir = tempfile.TemporaryDirectory(prefix='bytepype-tests', delete=True)

    @classmethod
    def tearDownClass(cls):
        pass # cls.response_tempdir.cleanup()

    def tearDown(self):
        sys.path = TestPype.saved_pythonpath

    @staticmethod
    def response_file_name(name: str) -> pathlib.Path:
        tmp_dir_path = pathlib.Path(TestPype.response_tempdir.name)
        return tmp_dir_path / name

    def test_exit(self):
        with open(TestPype.response_file_name('test_exit.json'), 'w') as f:
            msg = bytepype.ExitBytepype()
            result = bytepype.handle_message(msg, f)
            self.assertTrue(result)

    def test_disassemble_empty_dependency(self):
        sys.path.append('test/resources/site-packages-1')
        outfile = TestPype.response_file_name('test_disassemble_empty_dependency.json')
        with open(outfile, 'w') as f:
            msg = bytepype.DisassembleModule('empty')
            result = bytepype.handle_message(msg, f)

            self.assertFalse(result)

        # Close the file so it is flushed
        #
        # Check that there is just one constant: None. Even empty files produce this
        with open(outfile) as f:
            obj = json.load(f)
            consts = obj['constants']
            self.assertEqual(len(consts), 1)
            self.assertEqual(len(obj['names']), 0)

    def test_disassemble_constants(self):
        sys.path.append('test/resources/site-packages-1')
        outfile = TestPype.response_file_name('test_disassemble_constants.json')
        with open(outfile, 'w') as f:
            msg = bytepype.DisassembleModule('constants')
            result = bytepype.handle_message(msg, f)

            self.assertFalse(result)

        with open(outfile) as f:
            obj = json.load(f)
            consts = obj['constants']
            # While there are lexically 9 constants (two included inside the tuple), there are only
            # 7 constants in the table. The constants in the tuple are stored in the tuple in
            # bytecode and not as separate references.
            self.assertEqual(len(consts), 7)
            self.assertEqual(len(obj['names']), 7)
            # Make sure we are actually serializing instructions
            self.assertEqual(len(obj['instructions']), 16)


if __name__ == '__main__':
    unittest.main()
