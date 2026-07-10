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


# --- 9th-edition guardrails added in the safety hardening pass -------------

def test_empty_m_becomes_mx_and_is_refused():
    # empty/unknown M is not defaulted to M0
    with pytest.raises(StagingError):
        stage_from_strings("T1a", "N0", "")


def test_mx_refused():
    with pytest.raises(StagingError):
        stage_from_strings("T2a", "N0", "MX")


def test_t0_refused():
    with pytest.raises(StagingError):
        stage_from_strings("T0", "N0", "M0")


def test_t1mi_preserves_original_descriptor():
    r = stage_from_strings("T1mi", "N0", "M0")
    assert r.stage_group == "IA1"                      # staged in T1a family
    assert r.to_dict()["original_descriptors"]["t"] == "T1mi"
    assert any("T1mi" in note for note in r.descriptor_notes)


def test_staging_basis_prefix_recorded():
    r = stage_from_strings("cT2a", "cN0", "cM0")
    assert r.tnm.basis == "clinical (cTNM)"


def test_mixed_basis_flagged():
    r = stage_from_strings("pT2a", "cN2b", "pM0")
    assert r.tnm.basis == "mixed"
    assert any("MIXED" in note for note in r.descriptor_notes)


def test_edition_guard():
    from nsclc_agent.staging import normalize_edition
    assert normalize_edition("AJCC9") == "AJCC/UICC 9th edition"
    assert normalize_edition(None) == "AJCC/UICC 9th edition"
    with pytest.raises(StagingError):
        normalize_edition("AJCC8")
    with pytest.raises(StagingError):
        normalize_edition("7th")
