"""Gate registrations: who is being measured, and how they are invoked.

``registry`` holds the record type and the rules about when a row may be
published at all. ``installed`` holds the entrants that exist on this machine,
each with the measurement that proved how it answers.
"""

from gatebench.gates.registry import (
    Applicability,
    GateRegistration,
    Readiness,
    RegisteredRun,
    from_plugin_hooks,
    screen_echoed_input,
)

__all__ = [
    "Applicability",
    "GateRegistration",
    "Readiness",
    "RegisteredRun",
    "from_plugin_hooks",
    "screen_echoed_input",
]
