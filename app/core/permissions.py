"""
app/core/permissions.py
========================
Production-grade Role-Based Access Control (RBAC) for the INARA Platform.

This module is the **single authoritative source** for who can do what.
Every service that holds a `check_*_permission` / `_require_*` helper
now delegates its policy decisions to the matrix defined here.

────────────────────────────────────────────────────────────────────────────
POLICY (latest revision)
────────────────────────────────────────────────────────────────────────────

  ADMIN
     Full CRUD on every resource.

  MANAGER
     • View everything (subject to Role-Matrix visibility).
     • Create & Update everything EXCEPT Memberships and Packages
       (managers can still VIEW the membership / package landing pages
       but cannot create, edit or delete those entities).
     • Cannot DELETE anything anywhere.

  INSTRUCTOR
     • View everything (subject to Role-Matrix visibility).
     • No create, no edit, no delete on shared resources.
     • CAN edit their own user profile (handled by the `is_owner_or_allowed`
       check inside InstructorService.update_instructor and the existing
       /users/update endpoint).

  USER
     • Self-service only — own bookings, own orders, own memberships,
       own profile. View public/discovery resources.

The matrix below is deliberately verbose and explicit. There is no
inheritance or wildcarding by design — every (resource, action) tuple
returns a deterministic answer that maps 1:1 to the policy above.

────────────────────────────────────────────────────────────────────────────
PUBLIC API
────────────────────────────────────────────────────────────────────────────
  can(role, resource, action)              -> bool   ← matrix lookup
  is_owner_or_allowed(role, uid, owner_id, resource, action) -> bool

  require_roles([roles])                   FastAPI dependency
  require_permission(resource, action)     FastAPI dependency (matrix-driven)
  require_admin / require_admin_or_manager / require_staff
  check_ownership(user, owner_id)          legacy helper, still supported
"""

from __future__ import annotations

from typing import Callable, List
from fastapi import HTTPException, status, Depends
from app.models.user import UserResponse
from app.api.v1.dependencies import get_current_active_user


# ════════════════════════════════════════════════════════════════════════════
# ROLE & RESOURCE CONSTANTS
# ════════════════════════════════════════════════════════════════════════════

class Roles:
    """Central role definitions — avoid magic strings."""
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    INSTRUCTOR = "INSTRUCTOR"
    USER = "USER"


class Resource:
    """Logical resource identifiers used by the access matrix."""
    CLASS        = "class"
    COURSE       = "course"
    MEMBERSHIP   = "membership"
    PACKAGE      = "package"
    PRODUCT      = "product"
    ORDER        = "order"
    NEWS         = "news"
    ABOUT_US     = "about_us"
    INSTRUCTOR   = "instructor"   # instructor-as-resource
    MEMBER       = "member"       # USER-role accounts
    MANAGER      = "manager"      # MANAGER-role accounts (admin-only oversight)
    ROLE_MATRIX  = "role_matrix"
    LEGAL        = "legal"        # terms / privacy / faq


class Action:
    """The four canonical CRUD verbs."""
    VIEW    = "view"
    CREATE  = "create"
    UPDATE  = "update"
    DELETE  = "delete"


# Aliases used by existing services ("edit" / "modify" / "patch" → "update")
_ACTION_ALIASES = {
    "edit":   Action.UPDATE,
    "modify": Action.UPDATE,
    "patch":  Action.UPDATE,
    "cancel": Action.UPDATE,   # cancel = soft-update of status
    "notify": Action.UPDATE,   # sending a notification is an update-like op
}


def _normalize_action(action: str) -> str:
    """Map alias verbs onto the canonical CRUD action."""
    if not action:
        return Action.VIEW
    a = action.strip().lower()
    return _ACTION_ALIASES.get(a, a)


# ════════════════════════════════════════════════════════════════════════════
# THE ACCESS MATRIX
# ════════════════════════════════════════════════════════════════════════════
#
# `_MATRIX[resource][action]` returns the set of roles allowed to perform
# `action` on `resource`. Membership in this set is the SOLE criterion —
# resource-ownership exceptions (e.g. "instructor edits own profile") are
# handled separately by `is_owner_or_allowed()`.
# ────────────────────────────────────────────────────────────────────────────

_ADMIN_ONLY: set    = {Roles.ADMIN}
_ADMIN_AND_MGR: set = {Roles.ADMIN, Roles.MANAGER}
_ALL_STAFF: set     = {Roles.ADMIN, Roles.MANAGER, Roles.INSTRUCTOR}
_EVERYONE: set      = {Roles.ADMIN, Roles.MANAGER, Roles.INSTRUCTOR, Roles.USER}


_MATRIX: dict[str, dict[str, set]] = {

    Resource.CLASS: {
        Action.VIEW:   _EVERYONE,
        Action.CREATE: _ADMIN_AND_MGR,
        Action.UPDATE: _ADMIN_AND_MGR,
        Action.DELETE: _ADMIN_ONLY,
    },

    Resource.COURSE: {
        Action.VIEW:   _EVERYONE,
        Action.CREATE: _ADMIN_AND_MGR,
        Action.UPDATE: _ADMIN_AND_MGR,
        Action.DELETE: _ADMIN_ONLY,
    },

    # Manager restricted from edit & delete on Memberships
    Resource.MEMBERSHIP: {
        Action.VIEW:   _EVERYONE,
        Action.CREATE: _ADMIN_ONLY,
        Action.UPDATE: _ADMIN_ONLY,
        Action.DELETE: _ADMIN_ONLY,
    },

    # Manager restricted from edit & delete on Packages
    Resource.PACKAGE: {
        Action.VIEW:   _EVERYONE,
        Action.CREATE: _ADMIN_ONLY,
        Action.UPDATE: _ADMIN_ONLY,
        Action.DELETE: _ADMIN_ONLY,
    },

    Resource.PRODUCT: {
        Action.VIEW:   _EVERYONE,
        Action.CREATE: _ADMIN_AND_MGR,
        Action.UPDATE: _ADMIN_AND_MGR,
        Action.DELETE: _ADMIN_ONLY,
    },

    # Orders are user-created; staff updates status; only admin deletes
    Resource.ORDER: {
        Action.VIEW:   _ADMIN_AND_MGR,
        Action.CREATE: _EVERYONE,
        Action.UPDATE: _ADMIN_AND_MGR,
        Action.DELETE: _ADMIN_ONLY,
    },

    Resource.NEWS: {
        Action.VIEW:   _EVERYONE,
        Action.CREATE: _ADMIN_AND_MGR,
        Action.UPDATE: _ADMIN_AND_MGR,
        Action.DELETE: _ADMIN_ONLY,
    },

    Resource.ABOUT_US: {
        Action.VIEW:   _EVERYONE,
        Action.CREATE: _ADMIN_AND_MGR,
        Action.UPDATE: _ADMIN_AND_MGR,
        Action.DELETE: _ADMIN_ONLY,
    },

    Resource.INSTRUCTOR: {
        Action.VIEW:   _EVERYONE,
        Action.CREATE: _ADMIN_ONLY,         # via Role-Matrix invitations
        Action.UPDATE: _ADMIN_AND_MGR,      # ownership lets instructors self-edit
        Action.DELETE: _ADMIN_ONLY,
    },

    Resource.MEMBER: {
        Action.VIEW:   _ADMIN_AND_MGR,
        Action.CREATE: _ADMIN_AND_MGR,
        Action.UPDATE: _ADMIN_AND_MGR,
        Action.DELETE: _ADMIN_ONLY,
    },

    # Managers are staff accounts — oversight of them is Admin-only across
    # the board (a Manager cannot view/edit/delete peer Manager accounts).
    # Managers themselves still self-service their own profile via the
    # existing /users/me + /users/update endpoints (role-agnostic).
    Resource.MANAGER: {
        Action.VIEW:   _ADMIN_ONLY,
        Action.CREATE: _ADMIN_ONLY,
        Action.UPDATE: _ADMIN_ONLY,
        Action.DELETE: _ADMIN_ONLY,
    },

    Resource.ROLE_MATRIX: {
        Action.VIEW:   _ADMIN_ONLY,
        Action.CREATE: _ADMIN_ONLY,
        Action.UPDATE: _ADMIN_ONLY,
        Action.DELETE: _ADMIN_ONLY,
    },

    Resource.LEGAL: {
        Action.VIEW:   _EVERYONE,
        Action.CREATE: _ADMIN_AND_MGR,
        Action.UPDATE: _ADMIN_AND_MGR,
        Action.DELETE: _ADMIN_ONLY,
    },
}


# ════════════════════════════════════════════════════════════════════════════
# PUBLIC MATRIX API
# ════════════════════════════════════════════════════════════════════════════

def can(role: str, resource: str, action: str) -> bool:
    """
    Return True iff `role` is permitted to perform `action` on `resource`.

    O(1) lookup. Unknown resources/actions are deny-by-default.
    Action aliases ("edit", "modify", "patch", "cancel", "notify") are
    normalised to the canonical CRUD verb.

    Examples
    --------
        can("MANAGER", Resource.MEMBERSHIP, Action.UPDATE)  # False
        can("MANAGER", Resource.MEMBERSHIP, Action.VIEW)    # True
        can("INSTRUCTOR", Resource.CLASS,    Action.CREATE) # False
        can("ADMIN",      Resource.ORDER,    Action.DELETE) # True
    """
    if not role:
        return False
    role = str(role).upper()
    resource_map = _MATRIX.get(resource)
    if not resource_map:
        return False
    allowed = resource_map.get(_normalize_action(action))
    if allowed is None:
        return False
    return role in allowed


def is_owner_or_allowed(
    user_role: str,
    user_id: str,
    resource_owner_id: str,
    resource: str,
    action: str,
) -> bool:
    """
    Permit either policy-allowed callers OR the resource owner.

    Used by services where a user must be able to mutate their own row even
    when the matrix says it requires elevated rights — e.g. an instructor
    editing their own user profile.
    """
    if can(user_role, resource, action):
        return True
    return bool(user_id and resource_owner_id and user_id == resource_owner_id)


def describe_policy(resource: str) -> dict[str, List[str]]:
    """
    Introspection — return the policy for one resource as a JSON-safe dict.
    Useful for diagnostic endpoints / UI gating.
    """
    return {
        action_name: sorted(role_set)
        for action_name, role_set in (_MATRIX.get(resource) or {}).items()
    }


# ════════════════════════════════════════════════════════════════════════════
# FASTAPI DEPENDENCY HELPERS  (backwards-compatible)
# ════════════════════════════════════════════════════════════════════════════

def require_roles(allowed_roles: List[str]) -> Callable:
    """
    Require the caller to have one of the specified roles.
    Existing routes that used this helper continue to work unchanged.
    """
    async def check_role(current_user: UserResponse = Depends(get_current_active_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error":          "Insufficient permissions",
                    "message":        f"Access denied. Required roles: {', '.join(allowed_roles)}",
                    "your_role":      current_user.role,
                    "required_roles": allowed_roles,
                },
            )
        return current_user
    return check_role


def require_permission(resource: str, action: str) -> Callable:
    """
    Declarative permission decorator built on top of the matrix.

    Example
    -------
        @router.delete("/packages/{id}")
        async def delete_pkg(
            id: str,
            user = Depends(require_permission(Resource.PACKAGE, Action.DELETE)),
        ):
            ...
    """
    async def check(current_user: UserResponse = Depends(get_current_active_user)):
        if not can(current_user.role, resource, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error":     "Insufficient permissions",
                    "message":   f"Your role ({current_user.role}) cannot {action} {resource}.",
                    "resource":  resource,
                    "action":    action,
                    "your_role": current_user.role,
                },
            )
        return current_user
    return check


def require_admin() -> Callable:
    """Admin-only access."""
    return require_roles([Roles.ADMIN])


def require_admin_or_manager() -> Callable:
    """Admin or Manager access."""
    return require_roles([Roles.ADMIN, Roles.MANAGER])


def require_staff() -> Callable:
    """Staff access: Admin, Manager, or Instructor (read-only dashboards)."""
    return require_roles([Roles.ADMIN, Roles.MANAGER, Roles.INSTRUCTOR])


# ════════════════════════════════════════════════════════════════════════════
# OWNERSHIP HELPER (backwards-compatible)
# ════════════════════════════════════════════════════════════════════════════

def check_ownership(user: UserResponse, resource_owner_id: str) -> bool:
    """Admins & managers always pass; everyone else must be the owner."""
    if user.role in [Roles.ADMIN, Roles.MANAGER]:
        return True
    return user.id == resource_owner_id


__all__ = [
    "Roles", "Resource", "Action",
    "can", "is_owner_or_allowed", "describe_policy",
    "require_roles", "require_permission",
    "require_admin", "require_admin_or_manager", "require_staff",
    "check_ownership",
]