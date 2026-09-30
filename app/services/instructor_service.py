"""
app/services/instructor_service.py
====================================
Business logic for the Instructors section.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any
from io import BytesIO

import pandas as pd
from fastapi import HTTPException, status

from app.db.db_client import prisma
from app.models.instructor_model import (
    InstructorBrief,
    InstructorListResponse,
    InstructorProfile,
    InstructorUpdate,
    UpcomingClassBrief,
    UpcomingCourseBrief,
)
from prisma.enums import UserRole, ClassStatus, CourseStatus
from app.core.permissions import is_owner_or_allowed as _is_owner_or_allowed, Resource as _Resource
from app.core.account_deletion_service import purge_and_delete_user


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class InstructorService:

    # ─────────────────────────────────────────
    # GUARD: admin / manager only
    # ─────────────────────────────────────────

    @staticmethod
    async def _require_admin_or_manager(user_id: str) -> None:
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user or user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin or Manager access required",
            )

    @staticmethod
    async def _require_admin(user_id: str) -> None:
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user or user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )

    # ─────────────────────────────────────────
    # HELPER: compute all stats for one instructor
    # Single source of truth — shared by list, profile, and export.
    # ─────────────────────────────────────────

    @staticmethod
    async def _compute_instructor_stats(instructor_id: str) -> Dict[str, Any]:
        """
        Returns:
          total_classes, total_courses,
          favoured_by (union of class + course wishlist users),
          total_students (union of class + course booking users)
        """
        # ── Classes ─────────────────────────────────────────────────
        total_classes = await prisma.classes.count(
            where={"instructorId": instructor_id}
        )
        instructor_classes = await prisma.classes.find_many(
            where={"instructorId": instructor_id},
            include={"bookings": True, "wishlists": True},
        )
        class_student_ids: set = set()
        class_wishlist_ids: set = set()
        for cls in instructor_classes:
            for b in cls.bookings:
                class_student_ids.add(b.userId)
            for w in cls.wishlists:
                class_wishlist_ids.add(w.userId)

        # ── Courses ──────────────────────────────────────────────────
        total_courses = await prisma.course.count(
            where={"instructorId": instructor_id}
        )
        instructor_courses = await prisma.course.find_many(
            where={"instructorId": instructor_id},
            include={"bookings": True, "wishlists": True},
        )
        course_student_ids: set = set()
        course_wishlist_ids: set = set()
        for course in instructor_courses:
            for b in course.bookings:
                course_student_ids.add(b.userId)
            for w in course.wishlists:
                course_wishlist_ids.add(w.userId)

        return {
            "total_classes":  total_classes,
            "total_courses":  total_courses,
            "favoured_by":    len(class_wishlist_ids | course_wishlist_ids),
            "total_students": len(class_student_ids  | course_student_ids),
        }

    # ─────────────────────────────────────────
    # HELPER: build one InstructorBrief row
    # ─────────────────────────────────────────

    @staticmethod
    async def _build_instructor_brief(user) -> InstructorBrief:
        stats = await InstructorService._compute_instructor_stats(user.id)
        return InstructorBrief(
            id=user.id,
            name=user.name,
            email=user.email,
            phone=user.phone,
            avatar=user.avatar,
            backgroundImage=user.address,   
            speciality=user.speciality,     
            bio=user.bio,                   
            totalClasses=stats["total_classes"],
            totalCourses=stats["total_courses"],
            favouredBy=stats["favoured_by"],
            totalStudents=stats["total_students"],
            joinedAt=user.createdAt,
        )

    # ─────────────────────────────────────────
    # 1. GET ALL INSTRUCTORS
    # ─────────────────────────────────────────

    @staticmethod
    async def get_all_instructors(
        caller_id: str,
        search: Optional[str],
        page: int,
        page_size: int,
    ) -> InstructorListResponse:
        """
        Paginated instructor list.
        Every field — including totalClasses, totalCourses,
        favouredBy, totalStudents — is returned for every row.
        """
        await InstructorService._require_admin_or_manager(caller_id)

        where: Dict[str, Any] = {"role": UserRole.INSTRUCTOR, "isActive": True}
        if search:
            where["OR"] = [
                {"name":  {"contains": search, "mode": "insensitive"}},
                {"email": {"contains": search, "mode": "insensitive"}},
            ]

        total       = await prisma.user.count(where=where)
        skip        = (page - 1) * page_size
        total_pages = max(1, (total + page_size - 1) // page_size)

        users = await prisma.user.find_many(
            where=where,
            skip=skip,
            take=page_size,
            order={"createdAt": "desc"},
        )

        instructors = [
            await InstructorService._build_instructor_brief(u)
            for u in users
        ]

        return InstructorListResponse(
            instructors=instructors,
            total=total,
            page=page,
            pageSize=page_size,
            totalPages=total_pages,
        )

    # ─────────────────────────────────────────
    # 2. GET INSTRUCTOR PROFILE BY ID
    # ─────────────────────────────────────────

    @staticmethod
    async def get_instructor_profile(instructor_id: str) -> InstructorProfile:
        """
        Full profile: stats + upcoming Classes list + upcoming Courses list.
        No admin guard — any authenticated user can view.
        """
        user = await prisma.user.find_unique(where={"id": instructor_id})
        if not user or user.role != UserRole.INSTRUCTOR:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Instructor not found",
            )

        now   = _now_utc()
        stats = await InstructorService._compute_instructor_stats(instructor_id)

        # ── Upcoming Classes ─────────────────────────────────────────
        upcoming_classes_raw = await prisma.classes.find_many(
            where={
                "instructorId": instructor_id,
                "status": ClassStatus.SCHEDULED,
                "scheduledAt": {"gt": now},
            },
            include={"bookings": True},
            order={"scheduledAt": "asc"},
            take=10,
        )

        upcoming_classes = []
        for cls in upcoming_classes_raw:
            confirmed = sum(
                1 for b in cls.bookings
                if b.status in ("CONFIRMED", "PENDING")
            )
            upcoming_classes.append(
                UpcomingClassBrief(
                    id=cls.id,
                    title=cls.title,
                    difficulty=cls.difficulty,
                    scheduledAt=cls.scheduledAt,
                    duration=cls.duration,
                    price=cls.price or 0.0,
                    gender=cls.gender,
                    availableSpots=max(0, (cls.maxParticipants or 0) - confirmed),
                    totalSpots=cls.maxParticipants,
                    instructorName=user.name,
                    instructorAvatar=user.avatar,
                )
            )

        # ── Upcoming Courses ─────────────────────────────────────────
        upcoming_courses_raw = await prisma.course.find_many(
            where={
                "instructorId": instructor_id,
                "status": CourseStatus.SCHEDULED,
                "scheduledAt": {"gt": now},
            },
            include={"bookings": True},
            order={"scheduledAt": "asc"},
            take=10,
        )

        upcoming_courses = []
        for course in upcoming_courses_raw:
            confirmed = sum(
                1 for b in course.bookings
                if b.status in ("CONFIRMED", "PENDING")
            )
            upcoming_courses.append(
                UpcomingCourseBrief(
                    id=course.id,
                    title=course.title,
                    difficulty=course.difficulty,
                    scheduledAt=course.scheduledAt,
                    duration=course.duration,
                    price=course.price or 0.0,
                    gender=course.gender,
                    availableSpots=max(0, (course.maxParticipants or 0) - confirmed),
                    totalSpots=course.maxParticipants,
                    instructorName=user.name,
                    instructorAvatar=user.avatar,
                )
            )

        return InstructorProfile(
            id=user.id,
            name=user.name,
            email=user.email,
            phone=user.phone,
            avatar=user.avatar,
            backgroundImage=user.address,   # backgroundImage → user.address (unchanged)
            speciality=user.speciality,     # speciality → user.speciality (dedicated column)
            bio=user.bio,                   # bio → user.bio (dedicated column)
            joinedAt=user.createdAt,
            totalClasses=stats["total_classes"],
            totalCourses=stats["total_courses"],
            favouredBy=stats["favoured_by"],
            totalStudents=stats["total_students"],
            upcomingClasses=upcoming_classes,
            upcomingCourses=upcoming_courses,
        )

    # ─────────────────────────────────────────
    # 3. UPDATE INSTRUCTOR
    # ─────────────────────────────────────────

    @staticmethod
    async def update_instructor(
        caller_id: str,
        instructor_id: str,
        data: InstructorUpdate,
    ) -> InstructorProfile:
        """
        PATCH instructor details.

        Access:
          • ADMIN, MANAGER  → may edit any instructor.
          • INSTRUCTOR      → may edit ONLY their own profile (ownership exception).
          • USER            → denied.

        Enforced by the central RBAC matrix (`Resource.INSTRUCTOR` + ownership).
        """
        # Authorize caller — matrix-allowed OR caller is the instructor row owner.
        caller = await prisma.user.find_unique(where={"id": caller_id})
        if not caller:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Authentication required",
            )
        if not _is_owner_or_allowed(
            user_role=str(caller.role),
            user_id=caller_id,
            resource_owner_id=instructor_id,
            resource=_Resource.INSTRUCTOR,
            action="update",
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to edit this instructor",
            )

        instructor = await prisma.user.find_unique(where={"id": instructor_id})
        if not instructor or instructor.role != UserRole.INSTRUCTOR:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Instructor not found",
            )

        update_dict: Dict[str, Any] = {}

        if data.name is not None:
            update_dict["name"] = data.name

        if data.phone is not None:
            update_dict["phone"] = data.phone

        if data.email is not None:
            existing = await prisma.user.find_unique(where={"email": data.email})
            if existing and existing.id != instructor_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already in use by another account",
                )
            update_dict["email"] = data.email

        # Each field maps to its own dedicated column — no aliasing, no fallback logic
        if data.speciality is not None:
            update_dict["speciality"] = data.speciality

        if data.bio is not None:
            update_dict["bio"] = data.bio

        if data.avatar is not None:
            update_dict["avatar"] = data.avatar

        if data.backgroundImage is not None:
            update_dict["address"] = data.backgroundImage 

        if not update_dict:
            return await InstructorService.get_instructor_profile(instructor_id)

        await prisma.user.update(where={"id": instructor_id}, data=update_dict)
        return await InstructorService.get_instructor_profile(instructor_id)

    # ─────────────────────────────────────────
    # 4. EXPORT TO EXCEL
    # ─────────────────────────────────────────

    @staticmethod
    async def export_instructors_to_excel(
        caller_id: str,
        search: Optional[str] = None,
    ) -> BytesIO:
        """Export full instructor list to .xlsx — Admin / Manager only."""
        await InstructorService._require_admin_or_manager(caller_id)

        where: Dict[str, Any] = {"role": UserRole.INSTRUCTOR, "isActive": True}
        if search:
            where["OR"] = [
                {"name":  {"contains": search, "mode": "insensitive"}},
                {"email": {"contains": search, "mode": "insensitive"}},
            ]

        users = await prisma.user.find_many(where=where, order={"createdAt": "desc"})

        export_data = []
        for user in users:
            stats = await InstructorService._compute_instructor_stats(user.id)
            export_data.append({
                "Name":           user.name,
                "Email":          user.email,
                "Phone":          user.phone or "N/A",
                "Speciality":     user.speciality or "N/A",   
                "Bio":            user.bio or "N/A",           
                "Total Classes":  stats["total_classes"],
                "Total Courses":  stats["total_courses"],
                "Favoured By":    stats["favoured_by"],
                "Total Students": stats["total_students"],
                "Joined":         user.createdAt.strftime("%Y-%m-%d"),
            })

        df = pd.DataFrame(export_data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Instructors", index=False)
            ws = writer.sheets["Instructors"]
            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).apply(len).max(), len(col)) + 2
                ws.column_dimensions[chr(65 + idx)].width = min(max_len, 50)
        output.seek(0)
        return output

    # ─────────────────────────────────────────
    # 5. DELETE INSTRUCTOR  (Admin only)
    # ─────────────────────────────────────────

    @staticmethod
    async def delete_instructor(
        caller_id: str,
        instructor_id: str,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Two-path delete — the same "Enterprise Standard" strategy already
        used for Products (see product_service.py::delete_product).

        WHY two paths — the FK reality:
          courses.instructor_id  → users.id   ON DELETE RESTRICT
          classes.instructor_id  → users.id   ON DELETE RESTRICT
        Postgres refuses to delete a User row while ANY class or course
        still references them as instructor — regardless of that class/
        course's status. An instructor with teaching history can't be
        hard-deleted without either (a) the delete failing outright, or
        (b) cascading their classes/courses away, which would silently
        wipe out OTHER members' booking/review/wishlist history for those
        sessions. Neither is acceptable as a silent default, so:

        ── PATH A  force=False (default) — Soft-delete ────────────────────
        Deactivates the account (isActive=False). Always safe, always
        works, regardless of teaching history — the instructor disappears
        from active listings (get_all_instructors already filters on
        isActive=True) and can no longer log in, while every class/course
        they ever taught, and everyone's bookings/reviews for those, stay
        fully intact. Idempotent — already-inactive instructors return
        200 immediately without modifying anything.

        ── PATH B  force=True — Hard-delete ────────────────────────────────
        Only proceeds when the instructor has ZERO classes and ZERO
        courses on record (e.g. one invited by mistake who never taught
        anything). Otherwise raises 409 with the exact counts so the
        caller understands why, and can fall back to the safe default.
        When safe, purges the instructor's own owned rows (bookings,
        wallet, cart, etc. — in case they also used the app as a
        participant) and deletes the account, via the same shared,
        transaction-safe helper used for self-delete and manager-delete.
        """
        await InstructorService._require_admin(caller_id)

        instructor = await prisma.user.find_unique(where={"id": instructor_id})
        if not instructor or instructor.role != UserRole.INSTRUCTOR:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Instructor not found",
            )

        # ═════════════════════════════════════════════════════════════
        # PATH A — SOFT DELETE (default)
        # ═════════════════════════════════════════════════════════════
        if not force:
            if not instructor.isActive:
                return {
                    "message": "Instructor is already deactivated",
                    "instructorId": instructor_id,
                    "action": "soft_delete",
                }

            await prisma.user.update(
                where={"id": instructor_id},
                data={"isActive": False},
            )
            return {
                "message": "Instructor deactivated successfully",
                "instructorId": instructor_id,
                "action": "soft_delete",
            }

        # ═════════════════════════════════════════════════════════════
        # PATH B — HARD DELETE (force=True)
        # ═════════════════════════════════════════════════════════════
        stats = await InstructorService._compute_instructor_stats(instructor_id)
        if stats["total_classes"] > 0 or stats["total_courses"] > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot permanently delete this instructor: they have "
                    f"{stats['total_classes']} class(es) and {stats['total_courses']} "
                    f"course(s) on record, which other members may have booked, "
                    f"reviewed, or wishlisted. Use the default (non-force) delete "
                    f"to deactivate instead, or reassign/remove those classes and "
                    f"courses first if a permanent delete is truly required."
                ),
            )

        removed = await purge_and_delete_user(instructor_id)
        return {
            "message": "Instructor permanently deleted",
            "instructorId": instructor_id,
            "action": "hard_delete",
            **removed,
        }