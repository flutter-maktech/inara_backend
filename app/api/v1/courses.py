from fastapi import APIRouter, Depends, Query, status, HTTPException, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from app.core.cloudinary_service import upload_image
from typing import Optional
from datetime import datetime
from app.models.courses_model import (
    CourseCreate, CourseUpdate, CourseResponse, CourseWithDetails,
    CourseListResponse, CourseSearchParams, SendCourseNotification, CancelCourse,
)
from app.models.user import UserResponse
from app.services.courses_service import CoursesService
from app.api.v1.dependencies import get_current_active_user


router = APIRouter()


# ============================================
# CREATE COURSE
# ============================================

@router.post(
    "/",
    response_model=CourseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create New Course",
    description="""
    **Create a new standalone course.**

    Courses are independent entities — they are NOT linked to any Class.
    Each course carries its own instructor, schedule, location, and pricing.

    **Required fields:** title, description, instructorId, scheduledAt, duration,
    maxParticipants, difficulty, gender, location, phone, imageUrl

    **Optional fields:** endDate (course end date), price, isFree, availableSeat,
    locationMapLink, latitude, longitude, order

    **Permissions:**
    - ADMIN, MANAGER: Can create any course
    - INSTRUCTOR: Can create courses (will be assigned as instructor)
    """
)
async def create_course(
    # ── Required ─────────────────────────────────────────
    title: str = Form(...),
    description: str = Form(...),
    instructorId: str = Form(...),
    scheduledAt: datetime = Form(..., description="Course start date/time"),
    duration: str = Form(...),              # e.g. "8:00 AM to 10:00 AM"
    maxParticipants: int = Form(...),
    difficulty: str = Form(...),
    gender: str = Form(...),
    location: str = Form(...),
    phone: str = Form(...),

    # ── Optional ─────────────────────────────────────────
    endDate: Optional[datetime] = Form(None, description="Course end date/time"),  # Issue #3
    price: float = Form(0.0),
    isFree: bool = Form(False),
    availableSeat: Optional[int] = Form(None),
    locationMapLink: Optional[str] = Form(None),
    latitude: Optional[float] = Form(None, description="GPS latitude coordinate"),
    longitude: Optional[float] = Form(None, description="GPS longitude coordinate"),
    order: int = Form(0),

    # ── Image ─────────────────────────────────────────────
    imageUrl: UploadFile = File(default=None),

    current_user: UserResponse = Depends(get_current_active_user),
):
    image_url = None
    if imageUrl and imageUrl.filename:
        image_url = await upload_image(imageUrl, folder="courses")

    course_obj = CourseCreate(
        title=title,
        description=description,
        instructorId=instructorId,
        scheduledAt=scheduledAt,
        endDate=endDate,
        duration=duration,
        maxParticipants=maxParticipants,
        difficulty=difficulty,
        gender=gender,
        location=location,
        phone=phone,
        price=price,
        isFree=isFree,
        availableSeat=availableSeat,
        locationMapLink=locationMapLink,
        latitude=latitude,
        longitude=longitude,
        order=order,
        imageUrl=image_url or "",
    )

    return await CoursesService.create_course(course_obj, current_user.id)


# ============================================
# GET ALL COURSES
# ============================================

@router.get(
    "/",
    response_model=CourseListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get All Courses",
    description="""
    **Get all courses** with powerful search and filtering.

    **Search:** course title, description, or instructor name

    **Filters:** instructorId, difficulty, gender, minPrice/maxPrice

    **Sorting:** scheduledAt, title, price, createdAt  |  asc / desc

    **Permissions:**
    - ADMIN, MANAGER: all courses
    - INSTRUCTOR: own courses only
    - USER: all courses
    """
)
async def get_all_courses(
    search: Optional[str] = Query(None, description="Search by title, description, or instructor"),
    instructorId: Optional[str] = Query(None),
    difficulty: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    minPrice: Optional[float] = Query(None),
    maxPrice: Optional[float] = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(10, ge=1, le=100),
    sortBy: str = Query("scheduledAt"),
    sortOrder: str = Query("asc"),
    current_user: UserResponse = Depends(get_current_active_user),
):
    params = CourseSearchParams(
        search=search,
        instructorId=instructorId,
        difficulty=difficulty,
        gender=gender,
        minPrice=minPrice,
        maxPrice=maxPrice,
        page=page,
        pageSize=pageSize,
        sortBy=sortBy,
        sortOrder=sortOrder,
    )
    return await CoursesService.get_all_courses(current_user.id, params)


# ============================================
# GET COURSE BY ID
# ============================================

@router.get(
    "/{course_id}",
    response_model=CourseWithDetails,
    status_code=status.HTTP_200_OK,
    summary="Get Course Details",
)
async def get_course(
    course_id: str,
    current_user: UserResponse = Depends(get_current_active_user),
):
    return await CoursesService.get_course_by_id(course_id, current_user.id)


# ============================================
# UPDATE COURSE
# ============================================

@router.patch(
    "/{course_id}",
    response_model=CourseResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Course",
)
async def update_course(
    course_id: str,
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    difficulty: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    price: Optional[float] = Form(None),
    isFree: Optional[bool] = Form(None),
    duration: Optional[str] = Form(None),
    scheduledAt: Optional[datetime] = Form(None, description="Course start date/time"),
    endDate: Optional[datetime] = Form(None, description="Course end date/time"),  # Issue #3
    maxParticipants: Optional[int] = Form(None),
    availableSeat: Optional[int] = Form(None),
    location: Optional[str] = Form(None),
    locationMapLink: Optional[str] = Form(None),
    latitude: Optional[float] = Form(None, description="GPS latitude coordinate"),
    longitude: Optional[float] = Form(None, description="GPS longitude coordinate"),
    phone: Optional[str] = Form(None),
    instructorId: Optional[str] = Form(None),
    order: Optional[int] = Form(None),
    imageUrl: UploadFile = File(default=None),
    current_user: UserResponse = Depends(get_current_active_user),
):
    image_url = None
    if imageUrl and imageUrl.filename:
        image_url = await upload_image(imageUrl, folder="courses")

    update_obj = CourseUpdate(
        title=title,
        description=description,
        difficulty=difficulty,
        gender=gender,
        price=price,
        isFree=isFree,
        duration=duration,
        scheduledAt=scheduledAt,
        endDate=endDate,
        maxParticipants=maxParticipants,
        availableSeat=availableSeat,
        location=location,
        locationMapLink=locationMapLink,
        latitude=latitude,
        longitude=longitude,
        phone=phone,
        instructorId=instructorId,
        order=order,
        imageUrl=image_url,
    )

    return await CoursesService.update_course(course_id, update_obj, current_user.id)


# ============================================
# DELETE COURSE
# ============================================

@router.delete(
    "/{course_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Course",
    description="Hard delete. Blocked if active bookings exist — cancel first.",
)
async def delete_course(
    course_id: str,
    current_user: UserResponse = Depends(get_current_active_user),
):
    return await CoursesService.delete_course(course_id, current_user.id)


# ============================================
# CANCEL COURSE
# ============================================

@router.post(
    "/cancel",
    status_code=status.HTTP_200_OK,
    summary="Cancel Course",
)
async def cancel_course(
    data: CancelCourse,
    current_user: UserResponse = Depends(get_current_active_user),
):
    return await CoursesService.cancel_course(data, current_user.id)


# ============================================
# SEND NOTIFICATION
# ============================================

@router.post(
    "/notify",
    status_code=status.HTTP_200_OK,
    summary="Send Notification to Course Participants",
)
async def send_notification(
    data: SendCourseNotification,
    current_user: UserResponse = Depends(get_current_active_user),
):
    return await CoursesService.send_notification(data, current_user.id)


# ============================================
# EXPORT TO EXCEL
# ============================================

@router.get(
    "/export/excel",
    status_code=status.HTTP_200_OK,
    summary="Export Courses to Excel",
)
async def export_courses(
    search: Optional[str] = Query(None),
    instructorId: Optional[str] = Query(None),
    difficulty: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    current_user: UserResponse = Depends(get_current_active_user),
):
    excel_file = await CoursesService.export_courses_to_excel(
        user_id=current_user.id,
        search=search,
        instructor_id=instructorId,
        difficulty=difficulty,
        gender=gender,
    )
    filename = f"courses_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        excel_file,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ============================================
# GET AVAILABLE INSTRUCTORS
# ============================================

@router.get(
    "/instructors/available",
    status_code=status.HTTP_200_OK,
    summary="Get Available Instructors",
)
async def get_available_instructors(
    current_user: UserResponse = Depends(get_current_active_user),
):
    return await CoursesService.get_available_instructors()


# ============================================
# GET COURSE ENROLLMENTS (bookings)
# ============================================

@router.get(
    "/{course_id}/enrollments",
    status_code=status.HTTP_200_OK,
    summary="Get Course Enrollments",
    description="""
    **Get the full list of bookings** for a specific course.

    Returns student name, email, phone, booking status, and date.
    Use for the Bookings table, Call Studio, and WhatsApp features.
    """,
)
async def get_course_enrollments(
    course_id: str,
    current_user: UserResponse = Depends(get_current_active_user),
):
    return await CoursesService.get_course_enrollments(course_id, current_user.id)