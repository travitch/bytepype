This tool, `bytepype`, is a Python disassembler that interacts with a calling process via a pipe (e.g., standard input and standard output).

`bytepype` communicates with a calling process over a pipe using a simple text-based protocol: JSON-L (line-oriented JSON).  The protocol is simple and documented below.

The tool is implemented in Python and uses the built-in Python disassembler. This means that it should be easy to keep up-to-date as Python evolves. It is designed to have no dependencies beyond what is included with the Python standard library.

# Usage

Run `bytepype` as follows:

```
bytepype --python-path 'DIR1:DIR2:DIR3'
```

It reads requests one line at a time from standard input. It writes each response on one line on standard output.

## Protocol

To ask `bytepype` to disassemble a module, send a request:

```
{
  "@type": "DisassembleModule",
  "module_name": "<name>"
}
```

Module names are exactly as they appear in Python `import` statements. `bytepype` will respond with a disassembly of the module (or an error).

# Semantics Exploration

The test suite in this package contains a number of small example Python programs along with their disassemblies.  The test suite includes assertions and notes highlighting how various Python program features are represented in bytecode.
