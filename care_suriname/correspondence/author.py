class InvalidVerifiedAuthorError(ValueError):
    pass


def verified_author_snapshot(*, user, membership, role, facility, department) -> dict:
    first_name = (user.first_name or "").strip()
    last_name = (user.last_name or "").strip()
    role_name = (role.name or "").strip()
    if (
        user.deleted
        or not user.is_active
        or not user.verified
        or user.is_service_account
        or not first_name
        or not last_name
        or membership.deleted
        or membership.user_id != user.id
        or membership.organization_id != department.id
        or role.deleted
        or role.is_archived
        or membership.role_id != role.id
        or not role_name
        or department.deleted
        or not department.active
        or department.facility_id != facility.id
        or facility.deleted
        or not facility.is_active
    ):
        raise InvalidVerifiedAuthorError(
            "Authenticated author identity or department membership is unverified"
        )
    return {
        "department": {
            "id": str(department.external_id),
            "name": department.name,
        },
        "display": f"{first_name} {last_name}",
        "facility": {
            "id": str(facility.external_id),
            "name": facility.name,
        },
        "id": str(user.external_id),
        "membership": str(membership.external_id),
        "professional_role": role_name,
        "qualification": (user.qualification or "").strip() or None,
        "registration": (user.doctor_medical_council_registration or "").strip()
        or None,
        "verified": True,
    }
