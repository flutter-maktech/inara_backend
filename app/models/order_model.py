from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from prisma.enums import OrderStatus


# ============================================
# ORDER MODELS (Admin/Manager manages orders)
# ============================================

class OrderItemBrief(BaseModel):
    """Brief order item info"""
    productId: str
    productName: str
    quantity: int
    price: float
    total: float


class OrderBrief(BaseModel):
    """
    One row in the Order List table.
    Shows: Product Name, Ordered By, Price, Quantity, Order Date, Progress
    """
    id: str
    orderNumber: str
    productName: str  # First product in order (or "Multiple items")
    orderedBy: str    # User name
    orderedByEmail: str
    price: float      # Order total
    quantity: int     # Total quantity across all items
    orderDate: datetime
    progress: str     # Maps from OrderStatus: "Paid", "Processing", "Picked Up", etc.
    status: OrderStatus

    class Config:
        from_attributes = True


class OrderListResponse(BaseModel):
    """Paginated order list"""
    orders: List[OrderBrief]
    total: int
    page: int
    pageSize: int
    totalPages: int


class OrderDetailResponse(BaseModel):
    """Full order details"""
    id: str
    orderNumber: str
    userId: str
    orderType: str

    # Customer info
    customerName: str
    customerEmail: str
    customerPhone: Optional[str] = None

    # Items
    items: List[OrderItemBrief]

    # Pricing
    subtotal: float
    discount: float
    tax: float
    total: float

    # Payment
    status: OrderStatus
    paymentMethod: str
    transactionId: Optional[str] = None
    paidAt: Optional[datetime] = None

    # Pickup (no shipping)
    pickupNote: Optional[str] = None

    notes: Optional[str] = None
    createdAt: datetime
    updatedAt: datetime

    class Config:
        from_attributes = True


class UpdateOrderProgress(BaseModel):
    """Update order progress/status"""
    # Valid transitions: PAID → PROCESSING → PICKEDUP
    status: OrderStatus


# ============================================
# PRODUCT LIST (For Order Management)
# ============================================

class ProductSalesBrief(BaseModel):
    """
    Product sales summary for Order > Product List tab.
    Shows: Product Name, Available Product, Total Sell, Total Revenue
    """
    id: str
    productName: str
    availableProduct: int   # stockQuantity
    totalSell: int          # salesCount
    totalRevenue: float     # Computed from OrderItems

    class Config:
        from_attributes = True


class ProductSalesListResponse(BaseModel):
    """Paginated product sales list"""
    products: List[ProductSalesBrief]
    total: int
    page: int
    pageSize: int
    totalPages: int


# ============================================
# SEARCH & FILTER MODELS
# ============================================
class OrderSearchParams(BaseModel):
    """Search and filter parameters for orders"""
    search: Optional[str] = None  # Search by product name or customer name
    status: Optional[OrderStatus] = None
    page: int = Field(1, ge=1)
    pageSize: int = Field(10, ge=1, le=100)
    sortBy: str = "createdAt"
    sortOrder: str = "desc"


class ProductSalesSearchParams(BaseModel):
    """Search parameters for product sales list"""
    search: Optional[str] = None
    page: int = Field(1, ge=1)
    pageSize: int = Field(10, ge=1, le=100)
    sortBy: str = "salesCount"  # Valid: salesCount, stockQuantity, name, price, createdAt
    sortOrder: str = "desc"