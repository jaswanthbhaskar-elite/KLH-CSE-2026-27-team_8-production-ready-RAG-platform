"""Purpose limitation: each role may only use the system for approved purposes."""

ROLE_PURPOSES = {
    "student": ["academic", "general"],
    "researcher": ["academic", "research", "general"],
    "admin": ["academic", "research", "general"],
}


def check_purpose(role: str, purpose: str) -> dict:
    """
    Check whether a role may use the system for the stated purpose.

    Returns a dict: {"allowed": bool, "reason": str}
    """
    if role not in ROLE_PURPOSES:
        return {"allowed": False, "reason": "Unknown user role."}

    if purpose not in ROLE_PURPOSES[role]:
        return {
            "allowed": False,
            "reason": (
                f"Purpose '{purpose}' is not permitted for role '{role}'."
            ),
        }

    return {"allowed": True, "reason": "Purpose approved."}