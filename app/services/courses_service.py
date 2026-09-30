"""
app/services/courses_service.py
=================================
Service layer for the standalone Course entity.
Courses have NO relation to Classes — they are fully independent.
Bookings reference courseId directly.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from app.db.db_client import prisma
from app.utils.location_utils import resolve_map_link
from app.models.courses_model import (
    CourseCreate, CourseUpdate, CourseResponse, CourseWithDetails,
    CourseListResponse, CourseSearchParams, SendCourseNotification, CancelCourse,
)
from app.core.notification_service import dispatch_push_notification, resolve_channels
from app.core.permissions import can as _policy_can, Resource as _Resource
from prisma.enums import CourseStatus, UserRole, BookingStatus
from fastapi import HTTPException, status
import pandas as pd
from io import BytesIO


class CoursesService:
    """
    Enterprise-grade service layer for the Course entity.

    Courses are a standalone entity — they carry their own instructor,
    location, pricing, and schedule data and are NOT parents of any Class.
    Bookings are attached directly to a Course via courseId.
    """

    # ============================================
    # ROLE-BASED ACCESS CONTROL
    # ============================================

    @staticmethod
    async def check_course_permission(
        user_id: str,
        course_id: Optional[str] = None,
        action: str = "view",
    ) -> bool:
        """
        Delegates to the central RBAC matrix in app.core.permissions.

        Policy (resource = "course"):
          ADMIN       : view, create, update, delete
          MANAGER     : view, create, update    (NO delete)
          INSTRUCTOR  : view only                (NO create / update / delete)
          USER        : view only
        """
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        return _policy_can(str(user.role), _Resource.COURSE, action)

    # ============================================
    # CREATE COURSE
    # ============================================

    @staticmethod
    async def create_course(data: CourseCreate, created_by_user_id: str) -> CourseResponse:
        has_permission = await CoursesService.check_course_permission(
            created_by_user_id, action="create"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to create courses",
            )

        instructor = await prisma.user.find_unique(where={"id": data.instructorId})
        if not instructor:
            raise HTTPException(status_code=404, detail="Instructor not found")
        if instructor.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN, UserRole.MANAGER]:
            raise HTTPException(status_code=400, detail="Selected user is not an instructor")

        course_data = data.model_dump(exclude_none=True)
        # Auto-generate map link from location if none was provided
        course_data["locationMapLink"] = resolve_map_link(
            course_data["location"], course_data.get("locationMapLink")
        )
        # endDate is an optional field from CourseBase — if provided it is
        # already present in course_data via model_dump(exclude_none=True)
        course_data["instructor"] = {"connect": {"id": course_data.pop("instructorId")}}

        course = await prisma.course.create(data=course_data)
        return await CoursesService._build_course_response(course.id)

    # ============================================
    # GET ALL COURSES
    # ============================================

    @staticmethod
    async def get_all_courses(user_id: str, params: CourseSearchParams) -> CourseListResponse:
        where_clause: Dict[str, Any] = {}

        user = await prisma.user.find_unique(where={"id": user_id})
        if user and user.role == UserRole.INSTRUCTOR:
            where_clause["instructorId"] = user_id

        if params.search:
            where_clause["OR"] = [
                {"title": {"contains": params.search, "mode": "insensitive"}},
                {"description": {"contains": params.search, "mode": "insensitive"}},
                {"instructor": {"is": {"name": {"contains": params.search, "mode": "insensitive"}}}},
            ]

        if params.instructorId:
            where_clause["instructorId"] = params.instructorId
        if params.difficulty:
            where_clause["difficulty"] = params.difficulty
        if params.gender:
            where_clause["gender"] = params.gender
        if params.minPrice is not None:
            where_clause.setdefault("price", {})["gte"] = params.minPrice
        if params.maxPrice is not None:
            where_clause.setdefault("price", {})["lte"] = params.maxPrice

        total = await prisma.course.count(where=where_clause)
        skip = (params.page - 1) * params.pageSize
        total_pages = max(1, (total + params.pageSize - 1) // params.pageSize)

        courses = await prisma.course.find_many(
            where=where_clause,
            include={"instructor": True, "bookings": True},
            skip=skip,
            take=params.pageSize,
            order={params.sortBy: params.sortOrder},
        )

        courses_response = []
        for course in courses:
            confirmed = [
                b for b in course.bookings
                if b.status in [BookingStatus.CONFIRMED, BookingStatus.ATTENDED]
            ]
            instructor_info = {
                "id":     course.instructor.id,
                "name":   course.instructor.name,
                "email":  course.instructor.email,
                "avatar": course.instructor.avatar,
            }
            course_dict = course.model_dump(exclude={"instructor", "bookings"})
            courses_response.append(
                CourseResponse(
                    **course_dict,
                    bookedSeats=len(confirmed),
                    instructor=instructor_info,
                )
            )

        return CourseListResponse(
            courses=courses_response,
            total=total,
            page=params.page,
            pageSize=params.pageSize,
            totalPages=total_pages,
        )

    # ============================================
    # GET COURSE BY ID
    # ============================================

    @staticmethod
    async def get_course_by_id(course_id: str, user_id: str) -> CourseWithDetails:
        has_permission = await CoursesService.check_course_permission(
            user_id, course_id, action="view"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to view this course",
            )

        course = await prisma.course.find_unique(
            where={"id": course_id},
            include={
                "instructor": True,
                "bookings": {
                    "include": {"user": True},
                },
            },
        )
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        # Build bookings list
        bookings_list = [
            {
                "id":       b.id,
                "userId":   b.userId,
                "userName": b.user.name or "Unknown",
                "userEmail": b.user.email,
                "userPhone": b.user.phone,
                "bookedAt":  b.bookedAt,
                "status":    b.status if isinstance(b.status, str) else b.status.value,
            }
            for b in course.bookings
        ]

        confirmed_count = sum(
            1 for b in course.bookings
            if b.status in [BookingStatus.CONFIRMED, BookingStatus.ATTENDED]
        )

        instructor_info = {
            "id":     course.instructor.id,
            "name":   course.instructor.name,
            "email":  course.instructor.email,
            "avatar": course.instructor.avatar,
        }

        course_dict = course.model_dump(exclude={"instructor", "bookings"})
        return CourseWithDetails(
            **course_dict,
            bookedSeats=confirmed_count,
            instructor=instructor_info,
            bookings=bookings_list,
        )

    # ============================================
    # UPDATE COURSE
    # ============================================

    @staticmethod
    async def update_course(course_id: str, data: CourseUpdate, user_id: str) -> CourseResponse:
        has_permission = await CoursesService.check_course_permission(
            user_id, course_id, action="edit"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to edit this course",
            )

        course = await prisma.course.find_unique(where={"id": course_id})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        update_data = data.model_dump(exclude_unset=True, exclude_none=True)

        # If location or map link is being updated, re-resolve the map link
        if "location" in update_data or "locationMapLink" in update_data:
            new_location = update_data.get("location", course.location)
            new_map_link = update_data.get("locationMapLink", None)
            update_data["locationMapLink"] = resolve_map_link(new_location, new_map_link)

        if "instructorId" in update_data:
            instructor = await prisma.user.find_unique(where={"id": update_data["instructorId"]})
            if not instructor:
                raise HTTPException(status_code=404, detail="Instructor not found")
            if instructor.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN, UserRole.MANAGER]:
                raise HTTPException(status_code=400, detail="Selected user is not an instructor")
            update_data["instructor"] = {"connect": {"id": update_data.pop("instructorId")}}

        await prisma.course.update(where={"id": course_id}, data=update_data)
        return await CoursesService._build_course_response(course_id)

    # ============================================
    # DELETE COURSE
    # ============================================

    @staticmethod
    async def delete_course(course_id: str, user_id: str):
        has_permission = await CoursesService.check_course_permission(
            user_id, course_id, action="delete"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to delete this course",
            )

        course = await prisma.course.find_unique(
            where={"id": course_id}, include={"bookings": True}
        )
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        active_bookings = [
            b for b in course.bookings
            if b.status in [BookingStatus.CONFIRMED, BookingStatus.PENDING]
        ]
        if active_bookings:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot delete course with {len(active_bookings)} active bookings. "
                    "Please cancel the course instead."
                ),
            )

        await prisma.course.delete(where={"id": course_id})
        return {"message": "Course deleted successfully"}

    # ============================================
    # CANCEL COURSE
    # ============================================

    @staticmethod
    async def cancel_course(data: CancelCourse, user_id: str):
        has_permission = await CoursesService.check_course_permission(
            user_id, data.courseId, action="cancel"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to cancel this course",
            )

        course = await prisma.course.find_unique(
            where={"id": data.courseId},
            include={
                "bookings": {
                    "where": {"status": {"in": [BookingStatus.CONFIRMED, BookingStatus.PENDING]}},
                    "include": {"user": True},
                }
            },
        )
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        # Cancel the course record
        await prisma.course.update(
            where={"id": data.courseId},
            data={
                "status":             CourseStatus.CANCELLED,
                "cancelledAt":        datetime.utcnow(),
                "cancellationReason": data.reason,
            },
        )

        # Cancel all active bookings
        booking_ids = [b.id for b in course.bookings]
        if booking_ids:
            await prisma.booking.update_many(
                where={"id": {"in": booking_ids}},
                data={"status": BookingStatus.CANCELLED},
            )

        # Notify participants — fan-out concurrently across in-app + WhatsApp
        enrolled_users = {b.user.id: b.user for b in course.bookings}
        if data.notifyParticipants and enrolled_users:
            msg = f"Course '{course.title}' has been cancelled. Reason: {data.reason}"
            await dispatch_push_notification(
                users=list(enrolled_users.values()),
                title="Course Cancelled",
                message=msg,
                notification_type="WARNING",
                send_via_app=True,
                send_via_whatsapp=True,
            )

        return {
            "message":        "Course cancelled successfully",
            "studentsNotified": len(enrolled_users) if data.notifyParticipants else 0,
            "refundPolicy":   data.refundPolicy,
        }

    # ============================================
    # SEND NOTIFICATION
    # ============================================

    @staticmethod
    async def send_notification(data: SendCourseNotification, user_id: str):
        """
        Send a push notification to all confirmed enrollments of a course.

        Channels are resolved from the request flags:
          • sendViaApp / sendViaWhatsApp / sendViaBoth

        User-level preferences (appNotificationsEnabled,
        whatsappNotificationsEnabled) are respected — participants who have
        opted out of a channel are counted in the skipped totals rather than
        skipped silently.
        """
        has_permission = await CoursesService.check_course_permission(
            user_id, data.courseId, action="notify"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to send notifications for this course",
            )

        course = await prisma.course.find_unique(
            where={"id": data.courseId},
            include={
                "bookings": {
                    "where": {"status": {"in": [BookingStatus.CONFIRMED, BookingStatus.ATTENDED]}},
                    "include": {"user": True},
                }
            },
        )
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        # Deduplicate — one user may have multiple confirmed bookings
        enrolled_users = {b.user.id: b.user for b in course.bookings if b.user}
        if not enrolled_users:
            return {
                "message":          "No active enrollments to notify",
                "studentsNotified": 0,
                "sentViaApp":       0,
                "sentViaWhatsApp":  0,
                "skippedApp":       0,
                "skippedWhatsApp":  0,
                "failed":           0,
            }

        # Resolve which channels to use
        use_app, use_whatsapp = resolve_channels(
            send_via_app=data.sendViaApp,
            send_via_whatsapp=data.sendViaWhatsApp,
            send_via_both=data.sendViaBoth,
        )

        # Fall back to a descriptive title if the caller did not supply one
        notification_title = data.title or f"Update: {course.title}"

        result = await dispatch_push_notification(
            users=list(enrolled_users.values()),
            title=notification_title,
            message=data.message,
            notification_type=data.notificationType,
            send_via_app=use_app,
            send_via_whatsapp=use_whatsapp,
        )

        return {
            "message":          "Notifications dispatched successfully",
            "studentsNotified": len(enrolled_users),
            **result,
        }

    # ============================================
    # EXPORT TO EXCEL
    # ============================================

    @staticmethod
    async def export_courses_to_excel(
        user_id: str,
        search: Optional[str] = None,
        instructor_id: Optional[str] = None,
        difficulty: Optional[str] = None,
        gender: Optional[str] = None,
    ) -> BytesIO:
        where_clause: Dict[str, Any] = {}

        user = await prisma.user.find_unique(where={"id": user_id})
        if user and user.role == UserRole.INSTRUCTOR:
            where_clause["instructorId"] = user_id

        if search:
            where_clause["OR"] = [
                {"title": {"contains": search, "mode": "insensitive"}},
                {"description": {"contains": search, "mode": "insensitive"}},
            ]
        if instructor_id:
            where_clause["instructorId"] = instructor_id
        if difficulty:
            where_clause["difficulty"] = difficulty
        if gender:
            where_clause["gender"] = gender

        courses = await prisma.course.find_many(
            where=where_clause,
            include={"instructor": True, "bookings": True},
            order={"scheduledAt": "asc"},
        )

        records = []
        for course in courses:
            confirmed = [
                b for b in course.bookings
                if b.status in ["CONFIRMED", "ATTENDED"]
            ]
            avail = (course.maxParticipants or 0) - len(confirmed)
            records.append({
                "Course Title":    course.title,
                "Instructor":      course.instructor.name if course.instructor else "Unknown",
                "Difficulty":      course.difficulty or "N/A",
                "Gender":          course.gender or "N/A",
                "Price (QAR)":     course.price,
                "Is Free":         course.isFree,
                "Max Participants": course.maxParticipants,
                "Booked Seats":    len(confirmed),
                "Available Seats": avail,
                "Start Date":      course.scheduledAt.strftime("%Y-%m-%d %H:%M") if course.scheduledAt else "",
                "End Date":        course.endDate.strftime("%Y-%m-%d %H:%M") if course.endDate else "",
                "Duration":        course.duration or "N/A",
                "Location":        course.location or "N/A",
                "Status":          course.status,
                "Created Date":    course.createdAt.strftime("%Y-%m-%d"),
            })

        df = pd.DataFrame(records)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Courses", index=False)
            ws = writer.sheets["Courses"]
            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).apply(len).max(), len(col)) + 2
                ws.column_dimensions[chr(65 + idx)].width = min(max_len, 50)
        output.seek(0)
        return output

    # ============================================
    # GET COURSE ENROLLMENTS (bookings)
    # ============================================

    @staticmethod
    async def get_course_enrollments(course_id: str, user_id: str) -> List[Dict[str, Any]]:
        has_permission = await CoursesService.check_course_permission(
            user_id, course_id, action="view"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to view enrollments for this course",
            )

        bookings = await prisma.booking.find_many(
            where={"courseId": course_id},
            include={"user": True},
            order={"bookedAt": "desc"},
        )

        return [
            {
                "id":       b.id,
                "userId":   b.userId,
                "userName": b.user.name or "Unknown",
                "userEmail": b.user.email,
                "userPhone": b.user.phone,
                "bookedAt":  b.bookedAt,
                "status":    b.status if isinstance(b.status, str) else b.status.value,
            }
            for b in bookings
        ]

    # ============================================
    # GET AVAILABLE INSTRUCTORS
    # ============================================

    @staticmethod
    async def get_available_instructors() -> List[Dict[str, Any]]:
        instructors = await prisma.user.find_many(
            where={"role": UserRole.INSTRUCTOR, "isActive": True},
            order={"name": "asc"},
        )
        return [
            {
                "id":     i.id,
                "name":   i.name or "Unknown",
                "email":  i.email,
                "avatar": i.avatar,
                "role":   i.role,
            }
            for i in instructors
        ]

    # ============================================
    # INTERNAL HELPER
    # ============================================

    @staticmethod
    async def _build_course_response(course_id: str) -> CourseResponse:
        """Fetch course with instructor and return a CourseResponse."""
        course = await prisma.course.find_unique(
            where={"id": course_id},
            include={"instructor": True, "bookings": True},
        )
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        confirmed = [
            b for b in course.bookings
            if b.status in [BookingStatus.CONFIRMED, BookingStatus.ATTENDED]
        ]
        instructor_info = {
            "id":     course.instructor.id,
            "name":   course.instructor.name,
            "email":  course.instructor.email,
            "avatar": course.instructor.avatar,
        }
        course_dict = course.model_dump(exclude={"instructor", "bookings"})
        return CourseResponse(**course_dict, bookedSeats=len(confirmed), instructor=instructor_info)