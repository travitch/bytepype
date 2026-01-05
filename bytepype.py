#!/usr/bin/env python3
#
# bytepype is a tool for incremental disassembly of Python bytecode. It communicates with its parent
# process over a pipe (stdin and stdout), accepting commands to disassemble Python modules into
# bytecode. It is incremental in that the parent process asks for what it needs and bytepype
# disassembles on demand so that it never needs to read everything into memory at once.
#
# bytepype uses the jsonl format (line-oriented JSON) where each object is on a single line (both
# for the input and output channels).
import abc
import argparse
import dis
import importlib.machinery
import json
import sys
from typing import Dict, IO, List, Optional

# Protocol

## Protocol Input Message Types
##
## Messages sent from the parent to bytepype

class ProtocolInputMessage(abc.ABC):
    pass

class DisassembleModule(ProtocolInputMessage):
    '''Return a list of all of the modules defined in the source tree.
    '''
    tag = 'DisassembleModule'

    module_name: str

    def __init__(self, name: str):
        self.module_name = name

class ExitBytepype(ProtocolInputMessage):
    tag = 'ExitBytepype'

## Protocol Output Message Types
##
## Messages sent from bytepype to the parent

class BytecodeInstruction:
    '''A simplified version of Python's built-in bytecode instruction.  This removes some redundant
    information, but ensures that the caller has enough information to understand the instruction.

    Bytecode instruction behaviors are documented at https://docs.python.org/3/library/dis.html
    '''

    # The string representation of the opcode
    #
    # This is more stable than opcode numbers
    opname: str

    # The (optional) numeric argument to the opcode
    #
    # These are often indexes into various tables or stacks
    arg: Optional[int]

    # The start index of the operation within the bytecode sequence. This is required to compute
    # jump targets, which are specified as relative offsets into the bytecode sequence.
    offset: int

    # The line number that the bytecode operation corresponds to
    #
    # This is optional, but present for any code that was derived from a source file
    line_number: Optional[int]

    def __init__(self, opname, arg, offset, line_number):
        self.opname = opname
        self.arg = arg
        self.offset = offset
        self.line_number = line_number

    def __str__(self) -> str:
        if self.arg is None:
            return self.opname
        else:
            return f"{self.opname} {self.arg}"

    @staticmethod
    def from_dis(i: dis.Instruction) -> "BytecodeInstruction":
        return BytecodeInstruction(i.opname, i.arg, i.offset, i.line_number)

class Constant(abc.ABC):
    pass

class ConstantInt(Constant):
    type: str
    value: int

    def __init__(self, v: int):
        self.value = v
        self.type = 'constant_int'

class ConstantString(Constant):
    value: str

    def __init__(self, s: str):
        self.value = s
        self.type = 'constant_string'

class ConstantBool(Constant):
    value: bool

    def __init__(self, b: bool):
        self.value = b
        self.type = 'constant_bool'

class ConstantFloat(Constant):
    value: float

    def __init__(self, f: float):
        self.value = f
        self.type = 'constant_float'

class ConstantTuple(Constant):
    items: List[Constant]

    def __init__(self, t: tuple):
        self.value = [CodeObjectWrapper._translate_constant(c) for c in t]
        self.type = 'constant_tuple'

class ConstantNone(Constant):
    pass

class CodeObject(Constant):
    '''A disassembled code object.

    Note that a code object is essentially metadata combined with a sequence of bytecode
    instructions.  A top-level code object contains other definitions (like classes and functions)
    of code objects.

    These are the code objects returned to the caller.

    '''

    name: str
    qualified_name: str
    filename: str
    # Constant table for the module (instructions will reference it)
    #
    # This contains normal constants as well as other code objects
    constants: List[Constant]
    # Global and built-in names referenced in the instructions for this code object
    names: List[str]
    # Variables captured from the environment in which this code object was defined
    free_variables: List[str]
    # Local variables referenced by a nested function
    cell_variables: List[str]
    local_variables: List[str]

    # These enable us to interpret the locals list to determine how many are arguments vs true local
    # definitions and to distinguish posonly args and kwonly args.

    num_locals: int
    argcount: int
    posonlyargcount: int
    kwonlyargcount: int

    instructions: List[BytecodeInstruction]

    def __init__(self, code: types.CodeType):
        self.name = code.co_name
        self.qualified_name = code.co_qualname
        self.filename = code.co_filename
        self.constants = [CodeObjectWrapper._translate_constant(c) for c in code.co_consts]
        self.names = list(code.co_names)
        self.free_variables = list(code.co_freevars)
        self.cell_variables = list(code.co_cellvars)
        self.local_variables = list(code.co_varnames)
        self.num_locals = code.co_nlocals
        self.argcount = code.co_argcount
        self.posonlyargcount = code.co_posonlyargcount
        self.kwonlyargcount = code.co_kwonlyargcount
        self.instructions = [BytecodeInstruction.from_dis(i) for i in dis.get_instructions(code)]

    def get_constants(self) -> List[Constant]:
        return self.constants


class ProtocolResponseMessage(abc.ABC):
    pass

class DisassembledModule(ProtocolResponseMessage):

    # The disassembled top-level code object of the module
    #
    # This (generally) contains nested code definitions
    module: CodeObject

# Implementation

class CodeObjectWrapper:

    '''A wrapper around Python code objects to recursively disassemble them.
    '''

    # The code object being wrapped
    wrapped_code: types.CodeType

    # An index of (unqualified) names to code objects
    definitions: Dict[str, "CodeObjectWrapper"]

    def __init__(self, code: types.CodeType):
        self.wrapped_code = code
        self.definitions = dict()

        for const in self.wrapped_code.co_consts:
            if not type(const) is types.CodeType:
                continue

            code_name = const.co_name
            self.definitions[code_name] = CodeObjectWrapper.from_code(const)

    def definition(self, name: str) -> Optional['CodeObjectWrapper']:
        return self.definitions.get(name, None)

    def get_wrapped(self) -> types.CodeType:
        return self.wrapped_code

    def translate(self) -> CodeObject:
        return CodeObject(self.wrapped_code)

    @staticmethod
    def _translate_constant(c) -> Constant:
        if type(c) is types.CodeType:
            return CodeObjectWrapper.from_code(c).translate()
        elif type(c) is types.NoneType:
            return ConstantNone()
        elif type(c) is int:
            return ConstantInt(c)
        elif type(c) is str:
            return ConstantString(c)
        elif type(c) is bool:
            return ConstantBool(c)
        elif type(c) is float:
            return ConstantFloat(c)
        elif type(c) is tuple:
            return ConstantTuple(c)

        raise Exception(f"Unsupported constant type `{type(c)}`")

    @staticmethod
    def from_file(filename: str) -> "CodeObjectWrapper":
        '''Construct a code object, and wrap it with this wrapper, from a file on disk'''

        with open(filename, 'rb') as source_file:
            prog = compile(source_file.read(), filename, 'exec')
            return CodeObjectWrapper(prog)

    @staticmethod
    def from_string(filename: str, content: str) -> "CodeObjectWrapper":
        prog = compile(content, filename, 'exec')
        return CodeObjectWrapper(prog)

    @staticmethod
    def from_code(c: types.CodeType) -> "CodeObjectWrapper":
        '''Wrap an existing code object'''

        return CodeObjectWrapper(c)


def decode_message(msg: Dict) -> ProtocolInputMessage:
    if not '@type' in msg:
        raise Exception("Unexpected message format without `@type` property", msg)

    tag = msg['@type']
    if tag == DisassembleModule.tag:
        return DisassembleModule(msg['module_name'])
    elif tag == ExitBytepype.tag:
        return ExitBytepype()

    raise Exception(f"Unrecognized protocol message {tag}")


def lookup_module(fullname: str, path: Optional[str] = None) -> Optional[importlib.machinery.ModuleSpec]:
    '''Look up the module with the given `fullname` given the (optional) context of the parent module's path.

    A `fullname` is the name of a module provided to an import, e.g.,
    - import foo          => fullname = foo
    - import foo.bar      => fullname = foo.bar
    - from foo import bar => fullname = foo

    This uses the default Python path analysis chain. It determines the package search path from
    sys.path, as the standard import system does.

    '''
    for path_finder in sys.meta_path:
        spec = path_finder.find_spec(fullname, path)
        if spec is None or spec.origin == 'frozen' or spec.origin == 'built-in':
            continue

        return spec

    return None

def trivial_object_encoder(obj):
    json_result = {}

    for k, v in obj.__dict__.items():
        json_result[k] = v

    return json_result

def handle_message(msg: ProtocolInputMessage, out_channel: IO) -> bool:
    '''Handle a single message, (generally) writing a response to the output channel.

    Returns True when the system should shut down, and False otherwise.
    '''

    if type(msg) is DisassembleModule:
        disassemble_request: DisassembleModule = msg
        mod_spec = lookup_module(disassemble_request.module_name)
        if mod_spec is None:
            raise Exception(f"Module `{disassemble_request.module_name}` not found")

        if mod_spec.origin is None:
            raise Exception(f"Could not determine the origin of module {disassemble_request.module_name}")

        filename = mod_spec.origin
        obj = CodeObjectWrapper.from_file(filename)

        out_channel.write(json.dumps(obj.translate(), default=trivial_object_encoder))
        out_channel.write('\n')

        return False

    elif type(msg) is ExitBytepype:
        return True
    else:
        raise Exception("Unrecognized message type", msg)

def setup_path(path: str):
    '''Add the given path elements (colon-separated) into sys.path to make them available for lookup in the
    standard import machinery.
    '''

    new_paths = path.split(':')
    sys.path = new_paths + sys.path

def main(args):
    setup_path(args.python_path)

    while True:
        line = sys.stdin.readline()
        if line == '':
            break

        line = line.strip()
        msg = json.loads(line, object_hook=decode_message)
        should_exit = handle_message(msg, sys.stdout)
        if should_exit:
            break

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        prog='bytepype',
        description='A Python bytecode disassembly interface')

    parser.add_argument('--python-path', type=str,
                        help='The colon-separated list of directories to search for Python packages')


    args = parser.parse_args()
    main(args)
