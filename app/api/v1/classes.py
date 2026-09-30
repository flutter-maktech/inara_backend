from fastapi import APIRouter, Depends, Query, status, HTTPException, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from app.core.cloudinary_service import upload_image
from typing import Optional
from datetime import datetime
from app.models.classes_model import (
    ClassCreate, ClassUpdate, ClassResponse, ClassWithBookings,
    ClassListResponse, ClassSearchParams, SendNotification, CancelClass,
)
from app.models.user import UserResponse
from app.services.classes_service import ClassesService
from app.api.v1.dependencies import get_current_active_user
from prisma.enums import ClassStatus


router = APIRouter()


# ============================================
# CREATE CLASS
# ============================================

@router.post(
    "/",
    response_model=ClassResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create New Class",
    description="""
    **Create a new standalone class.**

    Classes are independent entities — they are NOT linked to any Course.
    Each class carries its own instructor, schedule, location, and pricing.

    **Required fields:** title, instructorId, scheduledAt, duration,
    maxParticipants, difficulty, gender, location, phone, imageUrl

    **Permissions:**
    - ADMIN, MANAGER: Can create any class
    - INSTRUCTOR: Can create classes (will be assigned as instructor)
    """
)
async def create_class(
    # ── Required ─────────────────────────────────────────
    title: str = Form(...),
    instructorId: str = Form(...),
    scheduledAt: datetime = Form(...),
    duration: str = Form(...),              # e.g. "8:00 AM to 10:00 AM"
    maxParticipants: int = Form(...),
    difficulty: str = Form(...),
    gender: str = Form(...),
    location: str = Form(...),
    phone: str = Form(...),

    # ── Optional ─────────────────────────────────────────
    description: Optional[str] = Form(None),
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
        image_url = await upload_image(imageUrl, folder="classes")

    class_obj = ClassCreate(
        title=title,
        instructorId=instructorId,
        scheduledAt=scheduledAt,
        duration=duration,
        maxParticipants=maxParticipants,
        difficulty=difficulty,
        gender=gender,
        location=location,
        phone=phone,
        description=description,
        price=price,
        isFree=isFree,
        availableSeat=availableSeat,
        locationMapLink=locationMapLink,
        latitude=latitude,
        longitude=longitude,
        order=order,
        imageUrl=image_url or "",
    )

    return await ClassesService.create_class(class_obj, current_user.id)


# ============================================
# GET ALL CLASSES
# ============================================

@router.get(
    "/",
    response_model=ClassListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get All Classes",
    description="""
    **Get all classes** with powerful search and filtering.

    **Search:** class name or instructor name

    **Filters:** instructorId, difficulty, gender, status, dateFrom/dateTo,
    scheduledAt (exact day: "2-24-2026" / "02/24/2026" / "2026-02-24")

    **Sorting:** scheduledAt, title, createdAt  |  asc / desc

    **Permissions:**
    - ADMIN, MANAGER: all classes
    - INSTRUCTOR: own classes only
    - USER: all classes
    """
)
async def get_all_classes(
    search: Optional[str] = Query(None, description="Search by class name or instructor"),
    instructorId: Optional[str] = Query(None),
    difficulty: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    status: Optional[ClassStatus] = Query(None),
    dateFrom: Optional[datetime] = Query(None),
    dateTo: Optional[datetime] = Query(None),
    scheduledAt: Optional[str] = Query(
        None,
        description='Exact-day filter. e.g. "2-24-2026", "02/24/2026", "2026-02-24"',
    ),
    page: int = Query(1, ge=1),
    pageSize: int = Query(10, ge=1, le=100),
    sortBy: str = Query("scheduledAt"),
    sortOrder: str = Query("asc"),
    current_user: UserResponse = Depends(get_current_active_user),
):
    params = ClassSearchParams(
        search=search,
        instructorId=instructorId,
        difficulty=difficulty,
        gender=gender,
        status=status,
        dateFrom=dateFrom,
        dateTo=dateTo,
        scheduledAt=scheduledAt,
        page=page,
        pageSize=pageSize,
        sortBy=sortBy,
        sortOrder=sortOrder,
    )
    return await ClassesService.get_all_classes(current_user.id, params)


# ============================================
# GET CLASS BY ID
# ============================================

@router.get(
    "/{class_id}",
    response_model=ClassWithBookings,
    status_code=status.HTTP_200_OK,
    summary="Get Class Details",
)
async def get_class_by_id(
    class_id: str,
    current_user: UserResponse = Depends(get_current_active_user),
):
    return await ClassesService.get_class_by_id(class_id, current_user.id, include_bookings=True)


# ============================================
# UPDATE CLASS
# ============================================

@router.patch(
    "/{class_id}",
    response_model=ClassResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Class",
)
async def update_class(
    class_id: str,
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    status: Optional[ClassStatus] = Form(None),
    duration: Optional[str] = Form(None),
    scheduledAt: Optional[datetime] = Form(None),
    maxParticipants: Optional[int] = Form(None),
    availableSeat: Optional[int] = Form(None),
    instructorId: Optional[str] = Form(None),
    difficulty: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    price: Optional[float] = Form(None),
    isFree: Optional[bool] = Form(None),
    location: Optional[str] = Form(None),
    locationMapLink: Optional[str] = Form(None),
    latitude: Optional[float] = Form(None, description="GPS latitude coordinate"),
    longitude: Optional[float] = Form(None, description="GPS longitude coordinate"),
    phone: Optional[str] = Form(None),
    order: Optional[int] = Form(None),
    imageUrl: UploadFile = File(default=None),
    current_user: UserResponse = Depends(get_current_active_user),
):
    image_url = None
    if imageUrl and imageUrl.filename:
        image_url = await upload_image(imageUrl, folder="classes")

    update_obj = ClassUpdate(
        title=title,
        description=description,
        status=status,
        duration=duration,
        scheduledAt=scheduledAt,
        maxParticipants=maxParticipants,
        availableSeat=availableSeat,
        instructorId=instructorId,
        difficulty=difficulty,
        gender=gender,
        price=price,
        isFree=isFree,
        location=location,
        locationMapLink=locationMapLink,
        latitude=latitude,
        longitude=longitude,
        phone=phone,
        order=order,
        imageUrl=image_url,
    )

    return await ClassesService.update_class(class_id, update_obj, current_user.id)


# ============================================
# DELETE CLASS
# ============================================

@router.delete(
    "/{class_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Class",
    description="Hard delete. Blocked if active bookings exist — cancel first.",
)
async def delete_class(
    class_id: str,
    current_user: UserResponse = Depends(get_current_active_user),
):
    return await ClassesService.delete_class(class_id, current_user.id)


# ============================================
# CANCEL CLASS
# ============================================

@router.post(
    "/cancel",
    status_code=status.HTTP_200_OK,
    summary="Cancel Class",
)
async def cancel_class(
    data: CancelClass,
    current_user: UserResponse = Depends(get_current_active_user),
):
    return await ClassesService.cancel_class(data, current_user.id)


# ============================================
# SEND NOTIFICATION
# ============================================

@router.post(
    "/notify",
    status_code=status.HTTP_200_OK,
    summary="Send Notification to Class Participants",
)
async def send_notification(
    data: SendNotification,
    current_user: UserResponse = Depends(get_current_active_user),
):
    return await ClassesService.send_notification_to_participants(data, current_user.id)


# ============================================
# EXPORT TO EXCEL
# ============================================

@router.get(
    "/export/excel",
    status_code=status.HTTP_200_OK,
    summary="Export Classes to Excel",
)
async def export_classes(
    search: Optional[str] = Query(None),
    instructorId: Optional[str] = Query(None),
    difficulty: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    status: Optional[ClassStatus] = Query(None),
    dateFrom: Optional[datetime] = Query(None),
    dateTo: Optional[datetime] = Query(None),
    current_user: UserResponse = Depends(get_current_active_user),
):
    excel_file = await ClassesService.export_classes_to_excel(
        user_id=current_user.id,
        search=search,
        instructor_id=instructorId,
        difficulty=difficulty,
        gender=gender,
        status_filter=status,
        date_from=dateFrom,
        date_to=dateTo,
    )
    filename = f"classes_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
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
    return await ClassesService.get_available_instructors()


# ============================================
# GET CLASS BOOKINGS
# ============================================

@router.get(
    "/{class_id}/bookings",
    status_code=status.HTTP_200_OK,
    summary="Get Class Bookings",
)
async def get_class_bookings(
    class_id: str,
    current_user: UserResponse = Depends(get_current_active_user),
):
    return await ClassesService.get_class_bookings(class_id, current_user.id)


# ============================================
# CONTACT INFO & MAP
# ============================================

@router.get("/{class_id}/contact-info", status_code=status.HTTP_200_OK, summary="Get Class Contact Info")
async def get_class_contact_info(
    class_id: str,
    current_user: UserResponse = Depends(get_current_active_user),
):
    return await ClassesService.get_class_contact_info(class_id, current_user.id)


@router.get("/{class_id}/map-info", status_code=status.HTTP_200_OK, summary="Get Map Info")
async def get_map_info(
    class_id: str,
    current_user: UserResponse = Depends(get_current_active_user),
):
    return await ClassesService.get_map_info(class_id, current_user.id)