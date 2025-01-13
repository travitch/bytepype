import json
import os
import sys
from typing import ClassVar, List
import unittest

import bytepype

class TestTranslation(unittest.TestCase):

    def test_empty(self):
        '''This is a basic test of what an empty module looks like.
        This is validating that the most fundamental infrastructure is working.
        '''
        w = bytepype.CodeObjectWrapper.from_file('test/resources/site-packages-1/empty.py')
        obj = w.translate()

        # Empty modules have two instructions:
        #
        # 1. RESUME
        # 2. RETURN_CONST (None)
        self.assertEqual(len(obj.instructions), 2)
        self.assertEqual(obj.instructions[0].opname, 'RESUME')
        self.assertEqual(obj.instructions[1].opname, 'RETURN_CONST')

        # Even though the file is empty, there is a `None` constant implicitly referenced (see
        # above)
        self.assertEqual(len(obj.constants), 1)
        self.assertEqual(obj.num_locals, 0)

    def test_global_constants(self):
        '''Test that global definitions have the expected form.'''
        w = bytepype.CodeObjectWrapper.from_file('test/resources/site-packages-1/constants.py')
        obj = w.translate()

        # We expect our 7 top-level constant definitions
        #
        # Note that `None` is actually referenced twice: once by the obvious definition and once by
        # the implicit `RETURN_CONST (NONE)`
        self.assertEqual(len(obj.constants), 7)

        # The global names are in obj.names
        self.assertEqual(len(obj.names), 7)
        self.assertIn('c_tuple', obj.names)

        # There are none of these; including no local variables because this is not within a
        # function scope
        self.assertEqual(len(obj.free_variables), 0)
        self.assertEqual(len(obj.cell_variables), 0)
        self.assertEqual(len(obj.local_variables), 0)

        # Each global assignment looks like:
        #
        # > LOAD_CONST ###
        # > STORE_NAME ###
        #
        # So we expect to see 7 of each instruction
        self.assertEqual(TestTranslation.count_instructions_of_type(obj, 'LOAD_CONST'), 7)
        self.assertEqual(TestTranslation.count_instructions_of_type(obj, 'STORE_NAME'), 7)

    def test_simple_definition(self):
        '''Explore what a function definition looks like.'''
        w = bytepype.CodeObjectWrapper.from_file('test/resources/site-packages-1/simple_definition.py')
        obj = w.translate()

        self.assertIn('const', obj.names)
        self.assertIn('top_level_func', obj.names)

        # Defined functions are treated as top-level constants. There is a code object constant in
        # the top-level module's constant table.
        func_obj = self.expect_defined_code_object(obj, 'top_level_func')

        # The function arguments are included in the local variables list
        self.assertEqual(func_obj.argcount, 1)
        self.assertEqual(func_obj.num_locals, 2)
        self.assertIn('arg1', func_obj.local_variables)
        # The other local is the local binding in the body
        self.assertIn('v', func_obj.local_variables)

        # The function references a global variable
        self.assertIn('const', func_obj.names)
        self.assertEqual(len(func_obj.names), 1)

        # The function has a binary operator (+)
        #
        # The operator pulls its operands from the stack. The operator argument is the operation (0 is add)
        add_op = func_obj.instructions[3]
        self.assertEqual(add_op.opname, 'BINARY_OP')
        self.assertEqual(add_op.arg, 0)

    def test_unnamed_temporaries(self):
        '''Explore what unnamed temporary values look like'''
        w = bytepype.CodeObjectWrapper.from_file('test/resources/site-packages-1/unnamed_temporaries.py')
        obj = w.translate()

        func_obj = self.expect_defined_code_object(obj, 'top_level_func')
        # We defined the function to just have one argument
        self.assertEqual(func_obj.argcount, 1)
        # There is only one local variable (the argument), which means that temporaries are stored
        # somewhere else: the local stack. In this way, despite all of these tables, Python is just
        # a stack machine
        self.assertEqual(func_obj.num_locals, 1)
        self.assertIn('arg1', func_obj.local_variables)

        # The function ends in a return, which returns the value on the top of the local stack.  The
        # opcode itself has no argument.
        ret_inst = func_obj.instructions[-1]
        self.assertEqual('RETURN_VALUE', ret_inst.opname)
        self.assertEqual(None, ret_inst.arg)

    def test_nested_function_nocapture(self):
        '''Explore what nested functions look like when they do not capture anything in the environment'''
        w = bytepype.CodeObjectWrapper.from_file('test/resources/site-packages-1/nested_function_nocapture.py')
        obj = w.translate()

        top_level_func = self.expect_defined_code_object(obj, 'top_level_func')

        # The nested function is *instantiated* by loading the function onto the stack (LOAD_CONST)
        # and then calling MAKE_FUNCTION, which pops the code object from the stack
        #
        # We start at index 1 here because index 0 is the RESUME instruction
        load_code_inst = top_level_func.instructions[1]
        make_func = top_level_func.instructions[2]
        self.assertEqual('LOAD_CONST', load_code_inst.opname)
        self.assertEqual(1, load_code_inst.arg)

        self.assertEqual('MAKE_FUNCTION', make_func.opname)
        self.assertEqual(None, make_func.arg)

        # This function defines a nested function. That looks like a code object in the constant
        # pool.
        #
        # This one has no variable captures (or locals)
        nested_func = self.expect_defined_code_object(top_level_func, 'nested_function')
        self.assertEqual(0, nested_func.argcount)
        self.assertEqual(0, nested_func.num_locals)
        self.assertEqual(0, len(nested_func.free_variables))
        self.assertEqual(0, len(nested_func.names))

    def test_nested_function_capture(self):
        '''Explore what nested functions look like when they do not capture anything in the environment'''
        w = bytepype.CodeObjectWrapper.from_file('test/resources/site-packages-1/nested_function_capture.py')
        obj = w.translate()

        top_level_func = self.expect_defined_code_object(obj, 'top_level_func')
        # The top-level function has one cell variable, as that is captured by the nested function (arg1)
        self.assertEqual(1, len(top_level_func.cell_variables))
        self.assertIn('arg1', top_level_func.cell_variables)

        # The nested function is defined as a constant code object in the top-level function scope
        nested_func = self.expect_defined_code_object(top_level_func, 'nested_function')
        # The nested function captures one variable from the enclosing scope
        self.assertEqual(1, len(nested_func.free_variables))
        self.assertIn('arg1', nested_func.free_variables)

        # The nested function also references one global
        self.assertEqual(1, len(nested_func.names))
        self.assertIn('const', nested_func.names)

        # NOTE: This seems to be true for Python 3.13. Earlier versions used a different encoding
        #
        # To create the closure object, Python creates a cell object (to represent the captured
        # variable) and includes it in a tuple of the environment that is passed to the
        # function. The environment is attached to the function using the SET_FUNCTION_ATTRIBUTE
        # instruction.
        make_cell = top_level_func.instructions[0]
        build_tuple = top_level_func.instructions[3]
        set_attr = top_level_func.instructions[6]
        self.assertEqual('MAKE_CELL', make_cell.opname)
        self.assertEqual('BUILD_TUPLE', build_tuple.opname)
        self.assertEqual('SET_FUNCTION_ATTRIBUTE', set_attr.opname)

    def test_example_lambda(self):
        '''Explore how Python lambda functions work.'''
        w = bytepype.CodeObjectWrapper.from_file('test/resources/site-packages-1/example_lambda.py')
        obj = w.translate()

        top_level_func = self.expect_defined_code_object(obj, 'top_level_func')

        # Unsurprisingly, lambdas are represented exactly the same as normal nested functions - just
        # without a name attached to the code object. Do note that all lambdas have the same name
        # (even when the bodies are different)
        #
        # Note that constant 0 here happens to be None, which this test isn't really looking at
        lam1 = top_level_func.constants[1]
        lam2 = top_level_func.constants[2]

        self.assertIsInstance(lam1, bytepype.CodeObject)
        self.assertIsInstance(lam2, bytepype.CodeObject)

        self.assertEqual(lam1.name, '<lambda>')
        self.assertEqual(lam2.name, '<lambda>')

    def test_class_repr(self):
        '''Explore how classes are represented.'''

        # They actually are just code objects and don't appear to be (very) different from
        # functions. It seems like the way to distinguish them is that the scope that defines the
        # class (often the top level for a module):
        #
        # 1. Uses LOAD_BUILD_CLASS to push builtins.__build_class__ onto the stack
        #
        # 2. Eventually uses CALL to invoke that method to construct a class object from the code object
        w = bytepype.CodeObjectWrapper.from_file('test/resources/site-packages-1/point.py')
        obj = w.translate()
        point_class = self.expect_defined_code_object(obj, 'Point')
        point_methods = ['__init__', '__str__', 'get_x']
        for m in point_methods:
            self.expect_defined_code_object(point_class, m)

    @staticmethod
    def count_instructions_of_type(obj: bytepype.CodeObject, name: str) -> int:
        return len([i for i in obj.instructions if i.opname == name])

    def expect_defined_code_object(self, obj: bytepype.CodeObject, name: str) -> bytepype.CodeObject:
        for c in obj.constants:
            if type(c) is bytepype.CodeObject:
                if c.name == name:
                    return c

        self.fail(f"Constant has unexpected type `{name}`")

    @classmethod
    def setUpClass(cls):
        '''Write out disassembled versions of each input file.

        This is abusing the test framework since this isn't really required for the tests to run,
        but it is useful for writing and understanding tests.
        '''
        for (dirpath, _, filenames) in os.walk('test/resources', followlinks=True):
            for filename in filenames:
                (basename, ext) = os.path.splitext(filename)
                if ext != '.py':
                    continue

                python_file = os.path.join(dirpath, filename)
                w = bytepype.CodeObjectWrapper.from_file(python_file)
                obj = w.translate()
                dis_file = os.path.join(dirpath, basename + '.json')
                with open(dis_file, 'w') as f:
                    json.dump(obj, f, indent=2, default=bytepype.trivial_object_encoder)
