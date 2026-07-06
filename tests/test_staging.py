"""Tests for the deterministic TNM-9 staging engine."""

import pytest

from nsclc_agent.staging import stage_from_strings, StagingError
from nsclc_agent.staging.selftest import EXPECTATIONS, run_selftest


@pytest.mark.parametrize("t,n,m,expected", EXPECTATIONS)
def test_stage_table(t, n, m, expected):
    assert stage_from_strings(t, n, m).stage_group == expected


def test_selftest_all_pass():
    passed, total, failures = run_selftest()
    assert failures == []
    assert passed == total


def test_migration_notes_surface():
    r = stage_from_strings("T2b", "N2b", "M0")
    assert r.stage_group == "IIIB"
    assert any("upstaged" in note for note in r.migration_notes)


def test_t1n2a_downstage_note():
    r = stage_from_strings("T1c", "N2a", "M0")
    assert r.stage_group == "IIB"
    assert any("N2 mediastinal" in n for n in r.migration_notes)


def test_m1c_split():
    assert stage_from_strings("T1a", "N0", "M1c1").stage_group == "IVB"
    assert stage_from_strings("T1a", "N0", "M1c2").stage_group == "IVB"


def test_ambiguous_n2_rejected():
    with pytest.raises(StagingError) as exc:
        stage_from_strings("T2a", "N2", "M0")
    assert "N2a" in str(exc.value)


def test_ambiguous_m1c_rejected():
    with pytest.raises(StagingError) as exc:
        stage_from_strings("T1a", "N0", "M1c")
    assert "M1c1" in str(exc.value)


def test_ambiguous_t2_rejected():
    with pytest.raises(StagingError):
        stage_from_strings("T2", "N0", "M0")


def test_nx_rejected_for_m0():
    with pytest.raises(StagingError):
        stage_from_strings("T1a", "NX", "M0")


def test_case_insensitive_and_whitespace():
    assert stage_from_strings(" t2B ", "n2A", "m0").stage_group == "IIIA"


def test_ia_substaging():
    assert stage_from_strings("T1a", "N0", "M0").stage_group == "IA1"
    assert stage_from_strings("T1b", "N0", "M0").stage_group == "IA2"
    assert stage_from_strings("T1c", "N0", "M0").stage_group == "IA3"
