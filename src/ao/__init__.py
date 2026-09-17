"""agent-orchestrator — attach to the coding agent you are already running.

Standard library only, deliberately. This tool watches agents on machines it does
not control, and a dependency is a thing that can be missing exactly there.
"""
# The one place the version is written: pyproject.toml reads it, and so does `ao --version`.
__version__ = "0.4.0"
