from __future__ import annotations

from report_bot.domain import Participant


def _is_father(role: str) -> bool:
    return "FATHER" in role


def _is_mother(role: str) -> bool:
    return "MOTHER" in role


def _is_child(role: str) -> bool:
    return role in {"CHILD", "SON", "DAUGHTER"} or "CHILD" in role


def _is_child_for_test(test_type: str, role: str) -> bool:
    normalized = role.upper()
    if _is_child(normalized):
        return True
    test = test_type.upper()
    if "AVUNCULAR" in test and any(token in normalized for token in ("NIECE", "NEPHEW")):
        return True
    if "GRANDPARENT" in test and any(
        token in normalized for token in ("GRANDCHILD", "GRANDSON", "GRANDDAUGHTER")
    ):
        return True
    return False


def _is_avuncular(role: str) -> bool:
    return any(token in role for token in ("UNCLE", "AUNT", "AVUNCULAR"))


def _is_grandparent(role: str) -> bool:
    return any(token in role for token in ("GRANDFATHER", "GRANDMOTHER", "GRANDPARENT"))


def _is_adult_relative(role: str) -> bool:
    return any(
        token in role
        for token in (
            "FATHER",
            "MOTHER",
            "UNCLE",
            "AUNT",
            "GRAND",
            "BROTHER",
            "SISTER",
            "SIBLING",
            "RELATIVE",
            "COUSIN",
            "MALE",
            "FEMALE",
        )
    )


def _is_primary_role(test_type: str, role: str) -> bool:
    test = test_type.upper()
    normalized_role = role.upper()
    if "PATERNITY" in test:
        return "ALLEGED" in normalized_role and _is_father(normalized_role)
    if "MATERNITY" in test:
        return "ALLEGED" in normalized_role and _is_mother(normalized_role)
    if "AVUNCULAR" in test:
        return "ALLEGED" in normalized_role and _is_avuncular(normalized_role)
    if "GRANDPARENT" in test:
        return "ALLEGED" in normalized_role and _is_grandparent(normalized_role)
    return False


def participant_priority(test_type: str, role: str) -> int:
    test = test_type.upper()
    normalized_role = role.upper()

    if "PATERNITY" in test:
        if _is_primary_role(test, normalized_role):
            return 0
        if _is_mother(normalized_role):
            return 1
    elif "MATERNITY" in test:
        if _is_primary_role(test, normalized_role):
            return 0
        if _is_father(normalized_role):
            return 1
    elif "AVUNCULAR" in test:
        if _is_primary_role(test, normalized_role):
            return 0
        if _is_father(normalized_role) or _is_mother(normalized_role):
            return 1
        if any(token in normalized_role for token in ("NIECE", "NEPHEW")):
            return 3
    elif "GRANDPARENT" in test:
        if _is_primary_role(test, normalized_role):
            return 0
        if _is_father(normalized_role) or _is_mother(normalized_role):
            return 1
        if any(token in normalized_role for token in ("GRANDCHILD", "GRANDSON", "GRANDDAUGHTER")):
            return 3
    elif any(
        token in test
        for token in (
            "SIBLING",
            "Y-CHROMOSOME",
            "ZYGOSITY",
            "MITOCHONDRIAL",
            "GENDER",
            "SEX",
            "PROFILE",
            "PERSONAL",
        )
    ):
        return 0

    if _is_child(normalized_role):
        return 3
    if _is_adult_relative(normalized_role):
        return 2
    return 4


def order_participants(test_type: str, participants: list[Participant]) -> list[Participant]:
    return sorted(
        participants,
        key=lambda participant: (
            participant_priority(test_type, participant.role),
            participant.source_order,
        ),
    )


def has_expected_primary_role(test_type: str, participants: list[Participant]) -> bool:
    if not participants:
        return False
    test = test_type.upper()
    if not any(token in test for token in ("PATERNITY", "MATERNITY", "AVUNCULAR", "GRANDPARENT")):
        return True
    return any(_is_primary_role(test, participant.role) for participant in participants)


def role_combination_warnings(
    test_type: str,
    participants: list[Participant],
) -> list[str]:
    warnings: list[str] = []
    primary_count = sum(
        _is_primary_role(test_type, participant.role) for participant in participants
    )
    if primary_count > 1:
        warnings.append("Multiple participants match the primary tested adult role")

    if (
        "PRENATAL" not in test_type.upper()
        and "GENDER" not in test_type.upper()
        and "SEX" not in test_type.upper()
        and "PROFILE" not in test_type.upper()
        and "PERSONAL" not in test_type.upper()
        and not any(
            _is_child_for_test(test_type, participant.role) for participant in participants
        )
    ):
        if any(
            token in test_type.upper()
            for token in ("PATERNITY", "MATERNITY", "AVUNCULAR", "GRANDPARENT")
        ):
            warnings.append("No child role was found for the inferred relationship test")

    recognized_tokens = (
        "FATHER",
        "MOTHER",
        "CHILD",
        "SON",
        "DAUGHTER",
        "UNCLE",
        "AUNT",
        "GRAND",
        "BROTHER",
        "SISTER",
        "SIBLING",
        "TWIN",
        "RELATIVE",
        "MALE",
        "FEMALE",
        "NIECE",
        "NEPHEW",
        "COUSIN",
        "DONOR",
        "PARTICIPANT",
        "INDIVIDUAL",
    )
    if any(
        not any(token in participant.role.upper() for token in recognized_tokens)
        for participant in participants
    ):
        warnings.append("One or more participant roles are unrecognized")
    return warnings
