"""Parts of ao's large modules, moved out whole and run in their module's namespace (#44).

A part is not importable on its own: it is loaded by `_part(name, globals())` in the
module it came from, so its names stay that module's. See docs/slices.md.
"""
