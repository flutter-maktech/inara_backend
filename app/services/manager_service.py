"""
app/services/manager_service.py
=================================
Business logic for the Managers section.

Managers = Users with role == MANAGER. Every action here is Admin-only —
Managers do not get oversight of peer Manager accounts (see the MANAGER
entry in app/core/permissions.py), unlike Members/Instructors where the
Manager role can view/edit. Managers still self-service their OWN profile
through the existing, role-agnostic /api/v1/users/me + /users/update
endpoints — this service is purely the Admin-facing oversight panel.
"""

from typing import Any, Dict, Optional
from io import BytesIO

import pandas as pd
from fastapi import HTTPException, status

from app.db.db_client import prisma
from app.core.account_deletion_service import purge_and_delete_user
from app.models.manager_model import (
    ManagerBrief,
    ManagerListResponse,
    ManagerProfile,
    UpdateManagerRequest,
    UpdateManagerResponse,
    UpdatedManagerSnapshot,
)
from prisma.enums import UserRole


class ManagerService:
    """Admin-only service for viewing, editing, exporting, and deleting MANAGER-role accounts."""

    # ─────────────────────────────────────────
    # GUARD: admin only
    # ─────────────────────────────────────────

    @staticmethod
    async def _require_admin(user_id: str) -> None:
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user or user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )

    # ─────────────────────────────────────────
    # HELPER: build one ManagerBrief row
    # ─────────────────────────────────────────

    @staticmethod
    def _build_manager_brief(user) -> ManagerBrief:
        return ManagerBrief(
            id=user.id,
            name=user.name,
            email=user.email,
            phone=user.phone,
            avatar=user.avatar,
            gender=user.gender,
            bio=getattr(user, "bio", None),
            dateOfBirth=getattr(user, "dateOfBirth", None),
            isActive=user.isActive,
            joinedAt=user.createdAt,
        )

    # ─────────────────────────────────────────
    # 1. GET ALL MANAGERS
    # ─────────────────────────────────────────

    @staticmethod
    async def get_all_managers(
        caller_id: str,
        search: Optional[str],
        page: int,
        page_size: int,
    ) -> ManagerListResponse:
        """Paginated manager list for the Managers table. Admin only."""
        await ManagerService._require_admin(caller_id)

        where_clause: Dict[str, Any] = {
            "role": UserRole.MANAGER,
            "isActive": True,
        }
        if search:
            where_clause["AND"] = [
                {"OR": [
                    {"name":  {"contains": search, "mode": "insensitive"}},
                    {"email": {"contains": search, "mode": "insensitive"}},
                ]}
            ]

        total = await prisma.user.count(where=where_clause)
        skip = (page - 1) * page_size
        total_pages = max(1, (total + page_size - 1) // page_size)

        users = await prisma.user.find_many(
            where=where_clause,
            skip=skip,
            take=page_size,
            order={"createdAt": "desc"},
        )

        managers = [ManagerService._build_manager_brief(u) for u in users]

        return ManagerListResponse(
            managers=managers,
            total=total,
            page=page,
            pageSize=page_size,
            totalPages=total_pages,
        )

    # ─────────────────────────────────────────
    # 2. GET MANAGER PROFILE BY ID
    # ─────────────────────────────────────────

    @staticmethod
    async def get_manager_profile(caller_id: str, manager_id: str) -> ManagerProfile:
        """Full manager profile (eye icon on manager row). Admin only."""
        await ManagerService._require_admin(caller_id)

        user = await prisma.user.find_unique(where={"id": manager_id})
        if not user or user.role != UserRole.MANAGER:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Manager not found",
            )

        return ManagerProfile(
            id=user.id,
            name=user.name,
            email=user.email,
            phone=user.phone,
            avatar=user.avatar,
            gender=user.gender,
            bio=getattr(user, "bio", None),
            dateOfBirth=getattr(user, "dateOfBirth", None),
            isActive=user.isActive,
            isVerified=user.isVerified,
            joinedAt=user.createdAt,
        )

    # ─────────────────────────────────────────
    # 3. UPDATE MANAGER (Admin only)
    # ─────────────────────────────────────────

    @staticmethod
    async def update_manager(
        caller_id: str,
        manager_id: str,
        data: UpdateManagerRequest,
    ) -> UpdateManagerResponse:
        """Edit a manager's profile. Only changed fields are written to DB."""
        await ManagerService._require_admin(caller_id)

        manager = await prisma.user.find_unique(where={"id": manager_id})
        if not manager or manager.role != UserRole.MANAGER:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Manager not found",
            )

        update_data = data.model_dump(exclude_unset=True, exclude_none=True)
        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields provided to update",
            )

        if "email" in update_data:
            existing = await prisma.user.find_unique(where={"email": update_data["email"]})
            if existing and existing.id != manager_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already in use by another account",
                )

        updated_manager = await prisma.user.update(
            where={"id": manager_id},
            data=update_data,
        )

        return UpdateManagerResponse(
            message="Manager profile updated successfully",
            managerId=manager_id,
            updatedFields=list(update_data.keys()),
            manager=UpdatedManagerSnapshot(
                id=updated_manager.id,
                email=updated_manager.email,
                name=updated_manager.name,
                phone=updated_manager.phone,
                gender=updated_manager.gender,
                avatar=updated_manager.avatar,
                bio=getattr(updated_manager, "bio", None),
                dateOfBirth=getattr(updated_manager, "dateOfBirth", None),
                isActive=updated_manager.isActive,
                joinedAt=updated_manager.createdAt,
            ),
        )

    # ─────────────────────────────────────────
    # 4. DELETE MANAGER (Admin only)
    # ─────────────────────────────────────────

    @staticmethod
    async def delete_manager(caller_id: str, manager_id: str) -> Dict[str, Any]:
        """
        Permanently delete a manager's account.

        Unlike Instructors, Managers carry no classes/courses — there's no
        `instructor_id`-style RESTRICT entanglement with other members'
        booking/review history — so a single, straightforward hard delete
        is safe here (no soft-delete path needed). Every RESTRICT-blocked
        row the manager personally owns (bookings, wallet, cart, orders,
        etc. — in case they ever used the app themselves) is purged first,
        inside one atomic transaction. See app/core/account_deletion_service.py.
        """
        await ManagerService._require_admin(caller_id)

        manager = await prisma.user.find_unique(where={"id": manager_id})
        if not manager or manager.role != UserRole.MANAGER:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Manager not found",
            )

        # Prevent accidental self-deletion (mirrors MemberService.delete_member)
        if caller_id == manager_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot delete your own account through this endpoint",
            )

        removed = await purge_and_delete_user(manager_id)
        return {
            "message": "Manager account deleted successfully",
            "managerId": manager_id,
            **removed,
        }

    # ─────────────────────────────────────────
    # 5. EXPORT MANAGERS TO EXCEL
    # ─────────────────────────────────────────

    @staticmethod
    async def export_managers_to_excel(
        caller_id: str,
        search: Optional[str] = None,
    ) -> BytesIO:
        """Export the full manager list to .xlsx. Admin only."""
        await ManagerService._require_admin(caller_id)

        where_clause: Dict[str, Any] = {"role": UserRole.MANAGER}
        if search:
            where_clause["OR"] = [
                {"name":  {"contains": search, "mode": "insensitive"}},
                {"email": {"contains": search, "mode": "insensitive"}},
            ]

        users = await prisma.user.find_many(where=where_clause, order={"createdAt": "desc"})

        export_data = [
            {
                "Name":   u.name,
                "Email":  u.email,
                "Phone":  u.phone or "N/A",
                "Gender": u.gender or "N/A",
                "Bio":    getattr(u, "bio", None) or "N/A",
                "Status": "Active" if u.isActive else "Deactivated",
                "Joined": u.createdAt.strftime("%Y-%m-%d"),
            }
            for u in users
        ]

        df = pd.DataFrame(export_data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Managers", index=False)
            ws = writer.sheets["Managers"]
            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).apply(len).max(), len(col)) + 2
                ws.column_dimensions[chr(65 + idx)].width = min(max_len, 50)
        output.seek(0)
        return output