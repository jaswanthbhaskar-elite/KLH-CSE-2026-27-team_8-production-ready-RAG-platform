"""
Development identity and document-management authorization.

PROTOTYPE ONLY: user IDs are trusted as sent. There is no password or token.
This module exists so that main.py (the /query endpoint) and the documents
router share ONE user-to-role table instead of each keeping its own copy.
"""

# Temporary role assignment for development (replace with real auth later)
USER_ROLES = {
    "student001": "student",
    "researcher001": "researcher",
    "admin001": "admin",
}

# Roles allowed to upload, list, view and delete documents.
DOCUMENT_ADMIN_ROLES = {"admin"}


def get_role(user_id: str) -> str | None:
    """Return the role for a user ID, or None if the user is unknown."""
    return USER_ROLES.get(user_id)


def check_document_admin(user_id: str) -> dict:
    """
    Decide whether a user may manage documents (upload, list, view, delete).

    Returns a dict:
      {"allowed": bool, "role": str | None, "stage": str, "reason": str}

    stage is "identity" when the user is unknown (caller should answer 401)
    or "role" when the user is known but is not an admin (caller answers 403).
    """
    role = get_role(user_id)

    if role is None:
        return {
            "allowed": False,
            "role": None,
            "stage": "identity",
            "reason": "User identity is not recognized.",
        }

    if role not in DOCUMENT_ADMIN_ROLES:
        return {
            "allowed": False,
            "role": role,
            "stage": "role",
            "reason": f"Role '{role}' is not permitted to manage documents.",
        }

    return {"allowed": True, "role": role, "stage": "role", "reason": "Access granted."}
