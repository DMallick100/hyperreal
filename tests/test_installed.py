#!/usr/bin/env python3
"""Tests for the installed-gate entrants.

Two kinds of check live here, and the runner keeps them apart on purpose:

* **Always-run** checks over the readiness logic, which is pure and needs no
  plugin installed.
* **Wiring** checks that need a specific third-party plugin on disk. These are
  reported as `SKIPPED` **by name and with a count**, never passed over in
  silence. A suite that quietly shrinks on a machine where the vendor is absent
  reports green for checks it did not run (`CLAUDE.md` 8.0 #3).
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hyperreal.gates import installed  # noqa: E402
from hyperreal.gates.registry import Readiness  # noqa: E402

TESTS = []
SKIPS = []
ASSERTIONS = 0


def test(fn):
    TESTS.append(fn)
    return fn


def check(condition, message):
    global ASSERTIONS
    ASSERTIONS += 1
    if not condition:
        raise AssertionError(message)


class Skip(Exception):
    pass


def require(path, what):
    if not os.path.exists(path):
        raise Skip(f"{what} is not installed at {path}")


# -- readiness logic (always runs) -------------------------------------------


@test
def test_hookify_with_no_rule_files_is_unconfigured():
    with tempfile.TemporaryDirectory() as tmp:
        state, detail = installed._hookify_ready(tmp)
        check(state is Readiness.UNCONFIGURED, f"expected UNCONFIGURED, got {state}")
        check("hookify.*.local.md" in detail, "detail should name the glob it ran")


@test
def test_hookify_finds_rules_where_hookify_itself_looks():
    """The glob is relative to cwd, per hookify's own config_loader.py."""
    with tempfile.TemporaryDirectory() as tmp:
        claude_dir = os.path.join(tmp, ".claude")
        os.makedirs(claude_dir)
        with open(os.path.join(claude_dir, "hookify.rules.local.md"), "w") as handle:
            handle.write("# a rule")
        state, detail = installed._hookify_ready(tmp)
        check(state is Readiness.READY, f"expected READY, got {state}")
        check("1 rule file" in detail, f"detail should count them: {detail!r}")


@test
def test_hookify_does_not_read_the_home_claude_directory():
    """A probe that guessed at ~/.claude would report the wrong state on every
    machine where the operator has hookify rules there and nowhere else."""
    with tempfile.TemporaryDirectory() as tmp:
        state, _ = installed._hookify_ready(tmp)
        check(
            state is Readiness.UNCONFIGURED,
            "an empty workspace is unconfigured regardless of what ~/.claude holds",
        )


# -- wiring (needs the plugin on disk) ---------------------------------------


@test
def test_hookify_cwd_is_the_workspace_not_the_plugin_root():
    """The defect this pins: rules are globbed relative to cwd, so a cwd of the
    plugin root makes the gate permanently unconfigurable."""
    require(installed.HOOKIFY_ROOT, "hookify")
    with tempfile.TemporaryDirectory() as tmp:
        reg = installed.hookify(workspace=tmp)
        check(reg.cwd == os.path.abspath(tmp), f"cwd should be the workspace, got {reg.cwd}")
        check(reg.cwd != installed.HOOKIFY_ROOT, "cwd must not be the plugin root")


@test
def test_validate_write_readiness_names_both_prerequisites():
    require(installed.VALIDATE_WRITE, "validate-write.sh")
    state, detail = installed._validate_write_ready()
    check(state is Readiness.READY, f"expected READY on this machine, got {state}: {detail}")


@test
def test_ecc_pre_bash_carries_its_matcher_from_ecc_config():
    require(installed.ECC_HOOKS_JSON, "ecc")
    reg = installed.ecc_pre_bash()
    check(reg.matcher == "Bash", f"matcher should come from ecc's config, got {reg.matcher!r}")
    check(installed.ECC_HOOKS_JSON in reg.source, "source must name ecc's own hooks.json")


@test
def test_discover_never_raises_for_an_absent_gate():
    entrants = installed.discover()
    check(len(entrants) == 3, f"three entrants are registered, got {len(entrants)}")
    for reg in entrants:
        state, detail = reg.readiness()
        check(isinstance(state, Readiness), f"{reg.name} must report a readiness state")
        check(bool(detail), f"{reg.name} readiness must say why")


def main():
    failures = []
    for fn in TESTS:
        try:
            fn()
        except Skip as exc:
            SKIPS.append((fn.__name__, exc))
        except Exception as exc:  # broad: one test must never abort the run
            failures.append((fn.__name__, exc))
    for name, exc in SKIPS:
        print(f"SKIP {name}: {exc}")
    for name, exc in failures:
        print(f"FAIL {name}: {exc}")
    ran = len(TESTS) - len(SKIPS)
    print(
        f"{ran} of {len(TESTS)} tests ran, {ASSERTIONS} assertions, "
        f"{len(SKIPS)} skipped - ",
        end="",
    )
    print("all checks passed" if not failures else f"{len(failures)} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
