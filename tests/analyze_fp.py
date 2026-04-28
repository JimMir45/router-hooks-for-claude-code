#!/usr/bin/env python3
"""Analyze the false positive for case_075."""
import re

# Current regex in runtime-guard.py
rx = re.compile(r'\brm\s+-[rRfF]+\b.*?(/|~|\$HOME|\*)')

test_cases = [
    ("rm -rf /tmp/data/*",        True,  "should block: rf + wildcard"),
    ("rm -Rf /var/log/old/",      True,  "should block: Rf + path"),
    ("rm -fr /tmp/cache/*",       True,  "should block: fr + wildcard"),
    ("rm -rF ~/backup/",          True,  "should block: rF + home"),
    ("rm -rf $HOME/foo",          True,  "should block: rf + $HOME"),
    ("rm config.backup",          False, "should allow: no flags"),
    ("rm -f /tmp/tempfile.txt",   False, "CASE_075: should allow but is blocked"),
]

print("=== Current regex behavior ===")
for cmd, expected, note in test_cases:
    actual = bool(rx.search(cmd))
    status = "OK" if actual == expected else "FAIL"
    print(f"[{status}] cmd={cmd!r:45s}  expected={expected}  actual={actual}  # {note}")

print()
print("=== Root cause ===")
print("Pattern: [rRfF]+  matches any combo of r/R/f/F letters")
print("So '-f' alone (only f, no r) matches [rRfF]+")
print("Combined with '/' in the absolute path '/tmp/tempfile.txt' -> false positive")

print()
# Proposed fix: require 'r' (recursive) to be present in the flags
rx_fixed = re.compile(r'\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*\b.*?(/|~|\$HOME|\*)')
print("=== Fixed regex (requires r or R in flags) ===")
for cmd, expected, note in test_cases:
    actual = bool(rx_fixed.search(cmd))
    status = "OK" if actual == expected else "FAIL"
    print(f"[{status}] cmd={cmd!r:45s}  expected={expected}  actual={actual}  # {note}")
