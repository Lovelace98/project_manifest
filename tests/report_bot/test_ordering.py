from __future__ import annotations

from report_bot.domain import Participant
from report_bot.ordering import (
    has_expected_primary_role,
    order_participants,
    role_combination_warnings,
)


def participant(role: str, order: int) -> Participant:
    return Participant(
        sample_number=f"SAMPLE{order}",
        role=role,
        full_name=f"PERSON {order}",
        date_of_birth="-",
        source_order=order,
    )


def roles(test_type: str, values: list[Participant]) -> list[str]:
    return [item.role for item in order_participants(test_type, values)]


def test_paternity_places_alleged_father_then_mother_then_stable_children() -> None:
    source = [
        participant("CHILD", 0),
        participant("MOTHER", 1),
        participant("CHILD", 2),
        participant("ALLEGED FATHER", 3),
    ]
    assert roles("PATERNITY", source) == [
        "ALLEGED FATHER",
        "MOTHER",
        "CHILD",
        "CHILD",
    ]
    assert [item.source_order for item in order_participants("PATERNITY", source)][2:] == [0, 2]


def test_maternity_places_alleged_mother_then_father() -> None:
    source = [
        participant("DAUGHTER", 0),
        participant("FATHER", 1),
        participant("ALLEGED MOTHER", 2),
    ]
    assert roles("MATERNITY", source) == ["ALLEGED MOTHER", "FATHER", "DAUGHTER"]


def test_avuncular_and_grandparentage_primary_adults_are_first() -> None:
    avuncular = [
        participant("CHILD", 0),
        participant("MOTHER", 1),
        participant("ALLEGED AUNT", 2),
        participant("FATHER", 3),
    ]
    grandparent = [
        participant("SON", 0),
        participant("MOTHER", 1),
        participant("ALLEGED GRANDFATHER", 2),
    ]
    assert roles("AVUNCULAR", avuncular) == [
        "ALLEGED AUNT",
        "MOTHER",
        "FATHER",
        "CHILD",
    ]
    assert roles("GRANDPARENTAGE", grandparent) == [
        "ALLEGED GRANDFATHER",
        "MOTHER",
        "SON",
    ]


def test_other_relatives_precede_children_and_unknown_roles_remain_stable() -> None:
    source = [
        participant("UNKNOWN A", 0),
        participant("CHILD", 1),
        participant("BROTHER", 2),
        participant("UNKNOWN B", 3),
        participant("ALLEGED FATHER", 4),
    ]
    assert roles("PATERNITY", source) == [
        "ALLEGED FATHER",
        "BROTHER",
        "CHILD",
        "UNKNOWN A",
        "UNKNOWN B",
    ]


def test_primary_role_is_strict_and_role_anomalies_warn() -> None:
    plain_father = [participant("FATHER", 0), participant("CHILD", 1)]
    assert not has_expected_primary_role("PATERNITY", plain_father)

    ambiguous = [
        participant("ALLEGED FATHER", 0),
        participant("ALLEGED FATHER", 1),
        participant("UNRECOGNIZED_STRANGE_ROLE", 2),
    ]
    warnings = role_combination_warnings("PATERNITY", ambiguous)
    assert "Multiple participants match" in warnings[0]
    assert any("No child role" in warning for warning in warnings)
    assert any("unrecognized" in warning for warning in warnings)


def test_ychromosome_and_siblingship_ordering_and_warnings() -> None:
    y_source = [
        participant("ALLEGED MALE RELATIVE", 0),
        participant("ALLEGED MALE RELATIVE", 1),
    ]
    assert has_expected_primary_role("Y-CHROMOSOME", y_source)
    y_warnings = role_combination_warnings("Y-CHROMOSOME", y_source)
    assert not y_warnings
    assert roles("Y-CHROMOSOME", y_source) == [
        "ALLEGED MALE RELATIVE",
        "ALLEGED MALE RELATIVE",
    ]

    sib_source = [
        participant("ALLEGED SIBLING", 0),
        participant("ALLEGED SIBLING", 1),
    ]
    assert has_expected_primary_role("SIBLINGSHIP", sib_source)
    sib_warnings = role_combination_warnings("SIBLINGSHIP", sib_source)
    assert not sib_warnings


def test_avuncular_with_niece_and_nephew_recognizes_child_role() -> None:
    source = [
        participant("NEPHEW", 0),
        participant("MOTHER", 1),
        participant("ALLEGED UNCLE", 2),
    ]
    assert has_expected_primary_role("AVUNCULAR", source)
    warnings = role_combination_warnings("AVUNCULAR", source)
    assert not any("No child role" in w for w in warnings)
    assert not any("unrecognized" in w for w in warnings)
    assert roles("AVUNCULAR", source) == [
        "ALLEGED UNCLE",
        "MOTHER",
        "NEPHEW",
    ]


def test_grandparentage_with_grandchild_recognizes_child_role() -> None:
    source = [
        participant("GRANDDAUGHTER", 0),
        participant("MOTHER", 1),
        participant("ALLEGED GRANDMOTHER", 2),
    ]
    assert has_expected_primary_role("GRANDPARENTAGE", source)
    warnings = role_combination_warnings("GRANDPARENTAGE", source)
    assert not any("No child role" in w for w in warnings)
    assert not any("unrecognized" in w for w in warnings)
    assert roles("GRANDPARENTAGE", source) == [
        "ALLEGED GRANDMOTHER",
        "MOTHER",
        "GRANDDAUGHTER",
    ]
