"""
Order API Router (Order Management)
====================================
Order = Admin/Manager views and manages product orders placed by users.

Endpoints:
  GET   /order/orders                  → Order List (product orders with brief details)
  GET   /order/orders/{id}             → Get order details by ID
  PATCH /order/orders/{id}/progress    → Update order progress (Completed/In Progress)
  GET   /order/orders/export/excel     → Export orders to Excel
  GET   /order/products                → Product List (sales statistics)
  GET   /order/products/export/excel   → Export product sales to Excel
"""

from fastapi import APIRouter, Depends, Response, status
from typing import Optional

from app.models.order_model import (
    OrderListResponse,
    OrderDetailResponse,
    UpdateOrderProgress,
    ProductSalesListResponse,
    OrderSearchParams,
    ProductSalesSearchParams,
)
from app.services.order_service import OrderService
from app.api.v1.dependencies import get_current_active_user
from prisma.enums import OrderStatus

router = APIRouter()


# ─────────────────────────────────────────────────────────────────
# ORDER LIST
# ─────────────────────────────────────────────────────────────────

@router.get("/orders", response_model=OrderListResponse)
async def get_all_orders(
    search: Optional[str] = None,
    status: Optional[OrderStatus] = None,
    page: int = 1,
    pageSize: int = 10,
    sortBy: str = "createdAt",
    sortOrder: str = "desc",
    current_user=Depends(get_current_active_user)
):
    """
    Get all product orders with brief details.

    **Columns:** Product Name, Ordered By, Price, Quantity, Order Date, Progress
    **Features:** Search by product name or customer name
    **UI Reference:** Order List table (Image 4, top section)
    **Access:** Admin / Manager only
    """
    params = OrderSearchParams(
        search=search,
        status=status,
        page=page,
        pageSize=pageSize,
        sortBy=sortBy,
        sortOrder=sortOrder
    )
    return await OrderService.get_all_orders(current_user.id, params)


# ─────────────────────────────────────────────────────────────────
# EXPORT ORDERS — must be before /{order_id} to avoid routing conflict
# ─────────────────────────────────────────────────────────────────

@router.get("/orders/export/excel")
async def export_orders_to_excel(
    search: Optional[str] = None,
    status: Optional[OrderStatus] = None,
    current_user=Depends(get_current_active_user)
):
    """
    Export orders to Excel (.xlsx).

    **UI Reference:** Download button on Order List (Image 4)
    **Access:** Admin / Manager only
    """
    excel_file = await OrderService.export_orders_to_excel(
        user_id=current_user.id,
        search=search,
        status=status
    )
    return Response(
        content=excel_file.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=orders_export.xlsx"}
    )


# ─────────────────────────────────────────────────────────────────
# GET ORDER BY ID
# ─────────────────────────────────────────────────────────────────

@router.get("/orders/{order_id}", response_model=OrderDetailResponse)
async def get_order_by_id(
    order_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Get full order details by ID.

    **Returns:**
    - Customer info
    - Order items with product details
    - Pricing breakdown
    - Shipping info
    - Payment info

    **Access:** Admin / Manager only
    """
    return await OrderService.get_order_by_id(order_id, current_user.id)


# ─────────────────────────────────────────────────────────────────
# UPDATE ORDER PROGRESS
# ─────────────────────────────────────────────────────────────────

@router.patch("/orders/{order_id}/progress", response_model=OrderDetailResponse)
async def update_order_progress(
    order_id: str,
    data: UpdateOrderProgress,
    current_user=Depends(get_current_active_user)
):
    """
    Update order progress/status.

    **UI Options:**
    - "Completed" → OrderStatus.COMPLETED
    - "In Progress" → OrderStatus.PROCESSING

    **Request body:**
    ```json
    {
      "status": "COMPLETED"
    }
    ```

    **UI Reference:** Progress dropdown in Order List (Image 4)
    **Access:** Admin / Manager only
    """
    return await OrderService.update_order_progress(order_id, data, current_user.id)


# ─────────────────────────────────────────────────────────────────
# PRODUCT LIST (Sales Statistics)
# ─────────────────────────────────────────────────────────────────

@router.get("/products", response_model=ProductSalesListResponse)
async def get_product_sales_list(
    search: Optional[str] = None,
    page: int = 1,
    pageSize: int = 10,
    sortBy: str = "salesCount",  # DB field name, not UI field name
    sortOrder: str = "desc",
    current_user=Depends(get_current_active_user)
):
    """
    Get all products with sales statistics.

    **Columns:** Product Name, Available Product, Total Sell, Total Revenue
    **Features:** Search by product name
    **UI Reference:** Product List table (Image 4, bottom section)
    **Access:** Admin / Manager only
    
    **Valid sortBy values:**
    - salesCount (default) - sorts by Total Sell
    - stockQuantity - sorts by Available Product
    - name - sorts by Product Name
    - price - sorts by price
    - createdAt - sorts by creation date
    """
    params = ProductSalesSearchParams(
        search=search,
        page=page,
        pageSize=pageSize,
        sortBy=sortBy,
        sortOrder=sortOrder
    )
    return await OrderService.get_product_sales_list(current_user.id, params)


# ─────────────────────────────────────────────────────────────────
# EXPORT PRODUCT SALES
# ─────────────────────────────────────────────────────────────────

@router.get("/products/export/excel")
async def export_product_sales_to_excel(
    search: Optional[str] = None,
    current_user=Depends(get_current_active_user)
):
    """
    Export product sales list to Excel (.xlsx).

    **UI Reference:** Download button on Product List (Image 4)
    **Access:** Admin / Manager only
    """
    excel_file = await OrderService.export_products_sales_to_excel(
        user_id=current_user.id,
        search=search
    )
    return Response(
        content=excel_file.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=product_sales_export.xlsx"}
    )