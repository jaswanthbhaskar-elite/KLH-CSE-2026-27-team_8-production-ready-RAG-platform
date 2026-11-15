"""Role-based access control (RBAC) for knowledge scopes."""

# Roles and their permitted knowledge scopes
ROLE_PERMISSIONS = {
    "student": ["academic_regulations", "course_materials"],
    "researcher": ["research_documents", "course_materials"],
    "admin": [
        "academic_regulations",
        "course_materials",
        "research_documents",
        "administrative_documents",
    ],
}


def check_access(role: str, requested_scope: list[str]) -> dict:
    """
    Check whether a role can access every requested knowledge scope.

    Returns a dict: {"allowed": bool, "reason": str}
    """
    if role not in ROLE_PERMISSIONS:
        return {"allowed": False, "reason": "Unknown user role."}

    if not requested_scope:
        return {
            "allowed": False,
            "reason": "At least one knowledge scope must be requested.",
        }

    permitted_scopes = ROLE_PERMISSIONS[role]

    # dict.fromkeys removes duplicates while preserving order
    denied_scopes = [
        scope
        for scope in dict.fromkeys(requested_scope)
        if scope not in permitted_scopes
    ]

    if denied_scopes:
        return {
            "allowed": False,
            "reason": (
                f"Access denied for role '{role}' to scope(s): "
                f"{', '.join(denied_scopes)}."
            ),
        }

    return {"allowed": True, "reason": "Access granted."}