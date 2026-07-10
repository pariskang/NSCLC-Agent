"""Authoritative TNM-9 → stage-group expectations, used as a self-test.

This table encodes the AJCC/UICC 9th-edition stage groups and is the single
source of truth for both the CLI ``selftest`` command and the pytest suite.
Every 8th→9th migration relevant to the shipped modules is represented.
"""

from __future__ import annotations

from .tnm import stage_from_strings

# (T, N, M) -> expected stage group
EXPECTATIONS: list[tuple[str, str, str, str]] = [
    # --- Stage 0 / I (N0, M0) ---
    ("Tis", "N0", "M0", "0"),
    ("T1a", "N0", "M0", "IA1"),
    ("T1b", "N0", "M0", "IA2"),
    ("T1c", "N0", "M0", "IA3"),
    ("T2a", "N0", "M0", "IB"),
    ("T2b", "N0", "M0", "IIA"),
    ("T3", "N0", "M0", "IIB"),
    ("T4", "N0", "M0", "IIIA"),
    # --- N1 (note T1N1 downstaged to IIA) ---
    ("T1a", "N1", "M0", "IIA"),
    ("T1c", "N1", "M0", "IIA"),
    ("T2a", "N1", "M0", "IIB"),
    ("T2b", "N1", "M0", "IIB"),
    ("T3", "N1", "M0", "IIIA"),
    ("T4", "N1", "M0", "IIIA"),
    # --- N2a single-station (T1N2a downstaged to IIB; T3N2a downstaged IIIA) ---
    ("T1a", "N2a", "M0", "IIB"),
    ("T1c", "N2a", "M0", "IIB"),
    ("T2a", "N2a", "M0", "IIIA"),
    ("T2b", "N2a", "M0", "IIIA"),
    ("T3", "N2a", "M0", "IIIA"),
    ("T4", "N2a", "M0", "IIIB"),
    # --- N2b multi-station (T2N2b upstaged to IIIB) ---
    ("T1a", "N2b", "M0", "IIIA"),
    ("T2a", "N2b", "M0", "IIIB"),
    ("T2b", "N2b", "M0", "IIIB"),
    ("T3", "N2b", "M0", "IIIB"),
    ("T4", "N2b", "M0", "IIIB"),
    # --- N3 (T1-2N3 = IIIB, T3-4N3 = IIIC) ---
    ("T1a", "N3", "M0", "IIIB"),
    ("T2b", "N3", "M0", "IIIB"),
    ("T3", "N3", "M0", "IIIC"),
    ("T4", "N3", "M0", "IIIC"),
    # --- Metastatic ---
    ("T2a", "N0", "M1a", "IVA"),
    ("T3", "N2b", "M1b", "IVA"),
    ("T1a", "N0", "M1c1", "IVB"),
    ("T4", "N3", "M1c2", "IVB"),
    # --- Occult ---
    ("TX", "N0", "M0", "Occult"),
]


def run_selftest() -> tuple[int, int, list[str]]:
    """Return (passed, total, failure_messages)."""
    failures: list[str] = []
    for t, n, m, expected in EXPECTATIONS:
        try:
            got = stage_from_strings(t, n, m).stage_group
        except Exception as exc:  # noqa: BLE001 - report as a failure
            failures.append(f"{t}{n}{m}: raised {exc!r} (expected {expected})")
            continue
        if got != expected:
            failures.append(f"{t}{n}{m}: got {got}, expected {expected}")
    total = len(EXPECTATIONS)
    return total - len(failures), total, failures
