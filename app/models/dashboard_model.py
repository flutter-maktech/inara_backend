from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

# ============================================
# DASHBOARD CARD MODELS (REDESIGNED)
# ============================================

class MetricCard(BaseModel):
    """Universal metric card - supports both revenue and counts"""
    label: str
    value: float  # Can be revenue amount OR count
    currency: Optional[str] = None  # "QAR" for revenue, None for counts
    change_percentage: Optional[float] = None  # e.g., +7.5%, -3.2%
    period: str = "30 days"  # "30 days", "current month", etc.
    metric_type: str = "revenue"  # "revenue" or "count"
    # ── Comparison transparency (additive, Sep 2026) ──
    previous_value: Optional[float] = None      # same metric for the previous period of equal length
    comparison_from: Optional[datetime] = None  # previous period start (inclusive)
    comparison_to: Optional[datetime] = None    # previous period end (exclusive)
    change_note: Optional[str] = None           # why change_percentage is null, when it is

class DashboardMetrics(BaseModel):
    """All 6 dashboard metric cards - CORRECTED"""
    # Revenue cards
    total_revenue_30days: MetricCard          # Classes + Courses revenue
    store_revenue_30days: MetricCard          # Store only revenue
    course_revenue_30days: MetricCard         # Courses only revenue
    
    # Count cards
    total_class_bookings_30days: MetricCard   # Total class bookings (count)
    total_orders_30days: MetricCard           # Total store orders (count)
    total_course_bookings_30days: MetricCard  # Total course bookings (count)

# Keep old model for backward compatibility (deprecated)
class RevenueCard(BaseModel):
    """Individual revenue metric card - DEPRECATED"""
    label: str
    amount: float
    currency: str = "QAR"
    change_percentage: Optional[float] = None
    period: str = "current"

class RevenueMetrics(BaseModel):
    """All revenue metrics for dashboard cards - DEPRECATED"""
    in_class_revenue: RevenueCard
    in_class_store_revenue: RevenueCard
    online_class_revenue: RevenueCard
    online_store_revenue: RevenueCard
    in_person_course_revenue: RevenueCard
    in_person_booking_revenue: RevenueCard

# ============================================
# CHART DATA MODELS
# ============================================

class MonthlyRevenuePoint(BaseModel):
    """Single data point for monthly revenue chart"""
    month: str  # "Jan", "Feb", etc.
    classes: float
    store: float
    courses: float

class PeakTrafficPoint(BaseModel):
    """Single data point for hourly traffic"""
    hour: str  # "6 am", "8 am", "10 am", etc.
    bookings: int

class MembershipTrendPoint(BaseModel):
    """Monthly membership statistics"""
    month: str
    memberships: int
    members: int

class BookingDetail(BaseModel):
    """Booking detail for horizontal bar chart"""
    label: str  # "Morning Flow Yoga", etc.
    bookings: int
    date: Optional[datetime] = None

# ============================================
# SIMPLE METRICS
# ============================================

class SimpleMetric(BaseModel):
    """Generic metric card (total members, male, female, etc.)"""
    label: str
    value: int
    change_percentage: Optional[float] = None

# ============================================
# COMPREHENSIVE DASHBOARD RESPONSE
# ============================================

class DashboardAnalytics(BaseModel):
    """Complete analytics dashboard response"""
    
    # Dashboard metrics (6 cards) - NEW
    metrics: DashboardMetrics
    
    # Charts data
    revenue_overview: List[MonthlyRevenuePoint]
    peak_traffic: List[PeakTrafficPoint]
    membership_trends: List[MembershipTrendPoint]
    new_memberships_and_members: List[MembershipTrendPoint]
    
    # Booking details
    classes_booking_details: List[BookingDetail]
    courses_booking_details: List[BookingDetail]
    
    # Simple metrics
    total_members: SimpleMetric
    male_count: SimpleMetric
    female_count: SimpleMetric
    
    # Metadata
    generated_at: datetime
    date_range_from: datetime
    date_range_to: datetime
    
    # DEPRECATED: Keep for backward compatibility
    revenue: Optional[RevenueMetrics] = None

# ============================================
# QUERY PARAMETERS
# ============================================

class AnalyticsQueryParams(BaseModel):
    """Query parameters for filtering analytics"""
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    compare_to_previous: bool = True  # Calculate percentage changes