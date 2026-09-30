from fastapi import APIRouter, Depends, Query, status, HTTPException
from datetime import datetime
from typing import Optional
from app.models.dashboard_model import DashboardAnalytics, RevenueMetrics
from app.services.dashboard_service import AnalyticsService
from app.api.v1.dependencies import get_current_active_user
from app.models.user import UserResponse
from app.core.permissions import require_staff, require_admin
import re
from dateutil import parser as date_parser

router = APIRouter()

# ============================================
# HELPER FUNCTIONS
# ============================================

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_DAY_FIRST_RE = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$")


def _date_format_error(date_str: str, reason: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "error": "Invalid date format",
            "message": f"Could not parse date: '{date_str}' ({reason})",
            "supported_formats": [
                "ISO 8601 (recommended): 2026-07-01 or 2026-07-01T00:00:00",
                "Day-first: 01-07-2026, 01/07/2026 or 01.07.2026 (DD-MM-YYYY)",
                "Month name: 1 July 2026, July 1, 2026",
            ],
            "example": "Try: 2026-07-01",
        },
    )


def parse_flexible_date(date_str: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    """
    Parse a date string STRICTLY, with no ambiguity.

    Resolution order (first match wins):
      1. ISO 8601  YYYY-MM-DD[THH:MM[:SS][Z|+HH:MM]]  -> always year-month-day
      2. DD-MM-YYYY / DD/MM/YYYY / DD.MM.YYYY         -> always day-first
      3. Text with a month NAME ("1 July 2026")      -> unambiguous by definition
    Anything else is rejected with 422, never guessed.

    FIX (Sep 2026): the previous implementation passed every string to
    dateutil with dayfirst=True, which silently swapped day and month on ISO
    dates whose day was 12 or lower (2026-07-01 -> 7 January 2026).

    end_of_day=True: a date given WITHOUT a time is extended to 23:59:59.999999
    so that `to_date` is inclusive of the whole final day. (Previously
    to_date=2026-07-31 meant 31 July 00:00 and excluded that entire day.)
    """
    if not date_str:
        return None
    text = date_str.strip()
    has_time = False

    if _ISO_DATE_RE.match(text):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as e:
            raise _date_format_error(date_str, f"invalid ISO 8601 date: {e}")
        has_time = len(text) > 10
    else:
        m = _DAY_FIRST_RE.match(text)
        if m:
            day, month, year = (int(g) for g in m.groups())
            try:
                parsed = datetime(year, month, day)
            except ValueError as e:
                raise _date_format_error(date_str, f"read as DD-MM-YYYY: {e}")
        elif re.search(r"[A-Za-z]{3,}", text):
            try:
                parsed = date_parser.parse(text, dayfirst=True)
            except (ValueError, TypeError, OverflowError) as e:
                raise _date_format_error(date_str, str(e))
            has_time = parsed.time() != datetime.min.time()
        else:
            raise _date_format_error(date_str, "unrecognised format")

    if end_of_day and not has_time:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _ensure_valid_range(from_dt: Optional[datetime], to_dt: Optional[datetime]) -> None:
    """Reject ranges where from_date is after to_date instead of returning empty figures."""
    if from_dt and to_dt:
        a = from_dt.replace(tzinfo=None) if from_dt.tzinfo else from_dt
        b = to_dt.replace(tzinfo=None) if to_dt.tzinfo else to_dt
        if a > b:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "Invalid date range", "message": "from_date must be on or before to_date"},
            )


# ============================================
# MAIN COMPREHENSIVE ENDPOINT
# ============================================
@router.get(
    "/overview",
    response_model=DashboardAnalytics,
    status_code=status.HTTP_200_OK,
    summary="Get Complete Dashboard Analytics",
    description="""
    **ONE COMPREHENSIVE API** that returns ALL dashboard analytics:
    - All 6 revenue metric cards
    - Revenue overview chart (12 months)
    - Peak traffic chart (hourly)
    - Membership trends chart
    - New memberships & members chart
    - Top 7 classes by bookings
    - Top 7 courses by bookings
    - Simple metrics (total/male/female counts)
    
    **Date Formats Accepted (strict, never guessed):**
    - ISO 8601 (recommended): `2026-07-01` or `2026-07-01T00:00:00`
    - Day-first: `01-07-2026`, `01/07/2026` or `01.07.2026` (DD-MM-YYYY)
    - Month name: `1 July 2026`
    - `to_date` given without a time includes the whole of that day
    
    This is optimized for dashboard initial load. Use focused endpoints
    for real-time updates of specific sections.
    """
)
async def get_dashboard_overview(
    from_date: Optional[str] = Query(
        None,
        description="Start date in various formats (e.g., '2026-01-01' or '01-01-2026')",
        example="2026-01-01"
    ),
    to_date: Optional[str] = Query(
        None,
        description="End date in various formats (e.g., '2026-12-31' or '31-12-2026')",
        example="2026-12-31"
    ),
    compare_to_previous: bool = Query(
        True,
        description="Calculate percentage changes vs previous period"
    ),
    current_user: UserResponse = Depends(require_staff()) 
):
    """
    **MAIN ENDPOINT**: Get complete dashboard analytics
    
    **PERMISSIONS:** Admin, Manager, or Instructor only
    - Returns: All metrics, charts, and data points
    - Performance: Optimized with single database connection
    - Accepts: Multiple date formats for better UX
    """
    
    # Parse dates with flexible format support
    parsed_from_date = parse_flexible_date(from_date)
    parsed_to_date = parse_flexible_date(to_date, end_of_day=True)
    _ensure_valid_range(parsed_from_date, parsed_to_date)
    
    return await AnalyticsService.get_dashboard_analytics(
        from_date=parsed_from_date,
        to_date=parsed_to_date,
        compare_to_previous=compare_to_previous
    )


# ============================================
# FOCUSED ENDPOINTS (for specific sections)
# ============================================

@router.get(
    "/revenue",
    response_model=RevenueMetrics,
    status_code=status.HTTP_200_OK,
    summary="Get Revenue Metrics Only",
    description="Focused endpoint returning just the 6 revenue cards"
)
async def get_revenue_metrics(
    from_date: Optional[str] = Query(None, example="2026-01-01"),
    to_date: Optional[str] = Query(None, example="2026-12-31"),
    compare_to_previous: bool = Query(True),
    current_user: UserResponse = Depends(require_staff())  # ✅ ROLE CHECK
):
    """Get only revenue metrics (6 cards) - useful for real-time updates"""
    # Parse dates
    parsed_from_date = parse_flexible_date(from_date)
    parsed_to_date = parse_flexible_date(to_date, end_of_day=True)
    _ensure_valid_range(parsed_from_date, parsed_to_date)
    
    # Get date range with defaults
    parsed_from_date, parsed_to_date = await AnalyticsService._get_date_range(
        parsed_from_date, parsed_to_date
    )
    
    return await AnalyticsService.get_revenue_metrics(
        from_date=parsed_from_date,
        to_date=parsed_to_date,
        compare_to_previous=compare_to_previous
    )


@router.get(
    "/traffic",
    status_code=status.HTTP_200_OK,
    summary="Get Peak Traffic Data",
    description="Focused endpoint for hourly traffic analysis"
)
async def get_peak_traffic(
    from_date: Optional[str] = Query(None, example="2026-01-01"),
    to_date: Optional[str] = Query(None, example="2026-12-31"),
    current_user: UserResponse = Depends(require_staff())  # ✅ ROLE CHECK
):
    """Get peak traffic hours - useful for scheduling optimization"""
    # Parse dates
    parsed_from_date = parse_flexible_date(from_date)
    parsed_to_date = parse_flexible_date(to_date, end_of_day=True)
    _ensure_valid_range(parsed_from_date, parsed_to_date)
    
    # Get date range with defaults
    parsed_from_date, parsed_to_date = await AnalyticsService._get_date_range(
        parsed_from_date, parsed_to_date
    )
    
    return await AnalyticsService.get_peak_traffic(parsed_from_date, parsed_to_date)


@router.get(
    "/bookings",
    status_code=status.HTTP_200_OK,
    summary="Get Booking Details",
    description="Focused endpoint for classes and courses booking statistics"
)
async def get_booking_details(
    from_date: Optional[str] = Query(None, example="2026-01-01"),
    to_date: Optional[str] = Query(None, example="2026-12-31"),
    limit: int = Query(7, ge=1, le=20, description="Number of top items to return"),
    current_user: UserResponse = Depends(require_staff())  # ✅ ROLE CHECK
):
    """Get top classes and courses by booking count"""
    # Parse dates
    parsed_from_date = parse_flexible_date(from_date)
    parsed_to_date = parse_flexible_date(to_date, end_of_day=True)
    _ensure_valid_range(parsed_from_date, parsed_to_date)
    
    # Get date range with defaults
    parsed_from_date, parsed_to_date = await AnalyticsService._get_date_range(
        parsed_from_date, parsed_to_date
    )
    
    classes = await AnalyticsService.get_classes_booking_details(
        parsed_from_date, parsed_to_date, limit
    )
    courses = await AnalyticsService.get_courses_booking_details(
        parsed_from_date, parsed_to_date, limit
    )
    
    return {
        "classes": classes,
        "courses": courses
    }


# ============================================
# ADMIN-ONLY ENDPOINTS (with role check)
# ============================================

@router.get(
    "/admin/full-report",
    response_model=DashboardAnalytics,
    status_code=status.HTTP_200_OK,
    summary="[ADMIN] Full Analytics Report",
    description="Complete analytics with extended date ranges - Admin only"
)
async def get_admin_full_report(
    from_date: Optional[str] = Query(None, example="2026-01-01"),
    to_date: Optional[str] = Query(None, example="2026-12-31"),
    current_user: UserResponse = Depends(require_admin())  # ✅ ADMIN ONLY
):
    """
    **ADMIN-ONLY** endpoint for comprehensive reports
    
    **PERMISSIONS:** Admin only
    """
    
    # Parse dates
    parsed_from_date = parse_flexible_date(from_date)
    parsed_to_date = parse_flexible_date(to_date, end_of_day=True)
    _ensure_valid_range(parsed_from_date, parsed_to_date)
    
    return await AnalyticsService.get_dashboard_analytics(
        from_date=parsed_from_date,
        to_date=parsed_to_date,
        compare_to_previous=True
    )


# ============================================
# UTILITY ENDPOINTS
# ============================================

@router.get(
    "/date-formats",
    status_code=status.HTTP_200_OK,
    summary="Get Supported Date Formats",
    description="Returns information about supported date formats"
)
async def get_supported_date_formats():
    """Get list of supported date formats for the analytics API"""
    return {
        "supported_formats": [
            {
                "format": "ISO 8601",
                "examples": ["2026-07-01", "2026-07-01T00:00:00", "2026-07-01T10:30:00Z"],
                "recommended": True,
                "description": "Always read as year-month-day. Recommended for all integrations."
            },
            {
                "format": "Day-first (DD-MM-YYYY)",
                "examples": ["01-07-2026", "01/07/2026", "01.07.2026"],
                "recommended": False,
                "description": "Always read as day-month-year. Supported for dashboard convenience."
            },
            {
                "format": "Month name",
                "examples": ["1 July 2026", "July 1, 2026", "1 Jul 2026"],
                "recommended": False,
                "description": "Unambiguous because the month is written out."
            }
        ],
        "usage_tips": [
            "Use ISO 8601 (YYYY-MM-DD) for all automated integrations",
            "MM/DD/YYYY (American) is NOT supported; 07/01/2026 is read as 7 January 2026",
            "to_date without a time covers the whole day (until 23:59:59)",
            "Any string that matches none of the formats is rejected with 422, never guessed"
        ],
        "api_behavior": "Strict parsing: ISO 8601 is always year-month-day; numeric non-ISO dates are always day-first"
    }


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Analytics Service Health Check"
)
async def analytics_health_check():
    """Check if analytics service is operational"""
    return {
        "status": "healthy",
        "service": "analytics",
        "timestamp": datetime.utcnow(),
        "version": "2.0.0",
        "features": {
            "flexible_date_parsing": True,
            "comprehensive_dashboard": True,
            "real_time_updates": True
        }
    }