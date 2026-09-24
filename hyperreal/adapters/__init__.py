"""Gate adapters. One per way of invoking a gate.

Tonight there is exactly one: :mod:`hyperreal.adapters.subprocess_gate`, which
covers every ``{"type": "command"}`` PreToolUse hook. Prompt-type hooks
(``{"type": "prompt"}``) are evaluated by the agent itself rather than by a
process we can spawn, and are an open question - docs/architecture.md S8.
"""
