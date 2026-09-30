"""
OrderService — Business logic for Order Management.

Orders = Admin/Manager views and manages product orders placed by users.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any
from io import BytesIO

import pandas as pd
from fastapi import HTTPException, status

from app.db.db_client import prisma
from app.models.order_model import (
    OrderBrief,
    OrderListResponse,
    OrderDetailResponse,
    OrderItemBrief,
    UpdateOrderProgress,
    ProductSalesBrief,
    ProductSalesListResponse,
    OrderSearchParams,
    ProductSalesSearchParams,
)
from prisma.enums import UserRole, OrderStatus
from app.core.notification_service import notify_user
import logging

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class OrderService:
    """
    Enterprise-grade Order management service.
    
    Features:
    - View all orders with search/filter
    - Update order progress (status)
    - View product sales statistics
    - Excel exports for orders and products
    
    Access: Admin/Manager only.
    """
    
    # ─────────────────────────────────────────
    # HELPER: admin/manager guard
    # ─────────────────────────────────────────
    
    @staticmethod
    async def _require_admin_or_manager(user_id: str) -> None:
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user or user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin or Manager access required"
            )
    
    # ─────────────────────────────────────────
    # HELPER: map OrderStatus to UI "Progress"
    # ─────────────────────────────────────────
    
    @staticmethod
    def _map_status_to_progress(status: OrderStatus) -> str:
        """
        UI shows "Completed" or "In Progress".
        Map from OrderStatus enum.
        """
        if status == OrderStatus.COMPLETED:
            return "Completed"
        else:
            # PENDING, PROCESSING, FAILED, REFUNDED, CANCELLED all show as "In Progress"
            return "In Progress"
    
    # ─────────────────────────────────────────
    # GET ALL ORDERS
    # ─────────────────────────────────────────
    
    @staticmethod
    async def get_all_orders(
        user_id: str,
        params: OrderSearchParams
    ) -> OrderListResponse:
        """
        Get all orders with search and filters.
        Returns brief order info for the Order List table.
        """
        await OrderService._require_admin_or_manager(user_id)
        
        where_clause: Dict[str, Any] = {
            "orderType": "PRODUCT"  # Only product orders (not course/package)
        }
        
        # Search by product name or customer name
        # We need to join through items -> product and user
        # Prisma doesn't support nested OR search directly, so we'll filter in Python after fetch
        # For production, consider using raw SQL or improving schema with search fields
        
        # Filter by status
        if params.status:
            where_clause["status"] = params.status
        
        # Count total
        total = await prisma.order.count(where=where_clause)
        
        # Pagination
        skip = (params.page - 1) * params.pageSize
        total_pages = max(1, (total + params.pageSize - 1) // params.pageSize)
        
        # Fetch orders with relations
        orders = await prisma.order.find_many(
            where=where_clause,
            include={
                "user": True,
                "items": {
                    "include": {"product": True}
                }
            },
            skip=skip,
            take=params.pageSize,
            order={params.sortBy: params.sortOrder}
        )
        
        # Build brief responses
        orders_brief = []
        for order in orders:
            # Get first product name (or "Multiple items")
            if order.items:
                product_name = order.items[0].product.name if order.items[0].product else "Unknown"
                if len(order.items) > 1:
                    product_name += f" (+{len(order.items) - 1} more)"
            else:
                product_name = "No items"
            
            # Calculate total quantity
            total_quantity = sum(item.quantity for item in order.items)
            
            # Map status to progress
            progress = OrderService._map_status_to_progress(order.status)
            
            orders_brief.append(
                OrderBrief(
                    id=order.id,
                    orderNumber=order.orderNumber,
                    productName=product_name,
                    orderedBy=order.user.name if order.user else "Unknown",
                    orderedByEmail=order.user.email if order.user else "N/A",
                    price=order.total,
                    quantity=total_quantity,
                    orderDate=order.createdAt,
                    progress=progress,
                    status=order.status
                )
            )
        
        # Apply search filter if provided (client-side filtering for now)
        if params.search:
            search_lower = params.search.lower()
            orders_brief = [
                o for o in orders_brief
                if search_lower in o.productName.lower() or search_lower in o.orderedBy.lower()
            ]
            total = len(orders_brief)
            total_pages = max(1, (total + params.pageSize - 1) // params.pageSize)
        
        return OrderListResponse(
            orders=orders_brief,
            total=total,
            page=params.page,
            pageSize=params.pageSize,
            totalPages=total_pages
        )
    
    # ─────────────────────────────────────────
    # GET ORDER BY ID
    # ─────────────────────────────────────────
    
    @staticmethod
    async def get_order_by_id(
        order_id: str,
        user_id: str
    ) -> OrderDetailResponse:
        """Get full order details by ID."""
        await OrderService._require_admin_or_manager(user_id)
        
        order = await prisma.order.find_unique(
            where={"id": order_id},
            include={
                "user": True,
                "items": {
                    "include": {"product": True}
                }
            }
        )
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        # Build items list
        items = [
            OrderItemBrief(
                productId=item.productId,
                productName=item.product.name if item.product else "Unknown",
                quantity=item.quantity,
                price=item.price,
                total=item.total
            )
            for item in order.items
        ]
        
        return OrderDetailResponse(
            id=order.id,
            orderNumber=order.orderNumber,
            userId=order.userId,
            orderType=order.orderType,
            customerName=order.user.name if order.user else "Unknown",
            customerEmail=order.user.email if order.user else "N/A",
            customerPhone=order.user.phone if order.user else None,
            items=items,
            subtotal=order.subtotal,
            discount=order.discount,
            tax=order.tax,
            shippingCost=order.shippingCost,
            total=order.total,
            status=order.status,
            paymentMethod=order.paymentMethod,
            transactionId=order.transactionId,
            paidAt=order.paidAt,
            shippingAddress=order.shippingAddress,
            shippingCity=order.shippingCity,
            shippingCountry=order.shippingCountry,
            shippingZip=order.shippingZip,
            shippingStatus=order.shippingStatus,
            trackingNumber=order.trackingNumber,
            notes=order.notes,
            createdAt=order.createdAt,
            updatedAt=order.updatedAt
        )
    
    # ─────────────────────────────────────────
    # UPDATE ORDER PROGRESS/STATUS
    # ─────────────────────────────────────────
    
    @staticmethod
    async def update_order_progress(
        order_id: str,
        data: UpdateOrderProgress,
        updated_by_user_id: str
    ) -> OrderDetailResponse:
        """
        Update order status (progress).
        UI allows: "Completed" → COMPLETED, "In Progress" → PROCESSING
        """
        await OrderService._require_admin_or_manager(updated_by_user_id)
        
        # Check order exists
        order = await prisma.order.find_unique(where={"id": order_id})
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )

        old_status = str(order.status)
        new_status = str(data.status)

        # Update status
        updated_order = await prisma.order.update(
            where={"id": order_id},
            data={"status": data.status}
        )

        # ── Notify the order's owner (in-app + WhatsApp, best-effort) ──
        # Skip if the status hasn't actually changed (idempotent calls).
        if old_status != new_status:
            try:
                # Build a friendly status label + appropriate notification type
                status_labels = {
                    "PROCESSING": ("Order In Progress",  "INFO"),
                    "COMPLETED":  ("Order Completed",    "SUCCESS"),
                    "PICKEDUP":   ("Order Picked Up",    "SUCCESS"),
                    "CANCELLED":  ("Order Cancelled",    "WARNING"),
                    "REFUNDED":   ("Order Refunded",     "INFO"),
                    "FAILED":     ("Order Failed",       "ERROR"),
                }
                title, ntype = status_labels.get(
                    new_status,
                    (f"Order Status: {new_status}", "INFO"),
                )
                await notify_user(
                    user_id=order.userId,
                    title=title,
                    message=(
                        f"Order #{order.orderNumber} is now {new_status.lower()}. "
                        f"Total: QAR {order.total:.2f}."
                    ),
                    notification_type=ntype,
                    send_via_app=True,
                    send_via_whatsapp=True,
                )
            except Exception as exc:
                logger.warning("Order status-change notification failed (non-fatal): %s", exc)

        # Return full details
        return await OrderService.get_order_by_id(order_id, updated_by_user_id)
    
    # ─────────────────────────────────────────
    # GET PRODUCT SALES LIST
    # ─────────────────────────────────────────
    
    @staticmethod
    async def get_product_sales_list(
        user_id: str,
        params: ProductSalesSearchParams
    ) -> ProductSalesListResponse:
        """
        Get all products with sales statistics.
        Shows: Product Name, Available Product (stock), Total Sell, Total Revenue.
        """
        await OrderService._require_admin_or_manager(user_id)
        
        where_clause: Dict[str, Any] = {}
        
        # Search by product name
        if params.search:
            where_clause["name"] = {"contains": params.search, "mode": "insensitive"}
        
        # Count total
        total = await prisma.product.count(where=where_clause)
        
        # Pagination
        skip = (params.page - 1) * params.pageSize
        total_pages = max(1, (total + params.pageSize - 1) // params.pageSize)
        
        # Map sortBy field: totalSell (UI/API field) -> salesCount (DB field)
        # Other valid fields: name, stockQuantity, price, createdAt
        sort_field_mapping = {
            "totalSell": "salesCount",
            "availableProduct": "stockQuantity",
            "productName": "name"
        }
        sort_field = sort_field_mapping.get(params.sortBy, params.sortBy)
        
        # Fetch products
        products = await prisma.product.find_many(
            where=where_clause,
            include={"orderItems": True},
            skip=skip,
            take=params.pageSize,
            order={sort_field: params.sortOrder}
        )
        
        # Build sales summary
        products_sales = []
        for product in products:
            # Calculate total revenue from order items
            total_revenue = sum(item.total for item in product.orderItems)
            
            products_sales.append(
                ProductSalesBrief(
                    id=product.id,
                    productName=product.name,
                    availableProduct=product.stockQuantity,
                    totalSell=product.salesCount,
                    totalRevenue=total_revenue
                )
            )
        
        return ProductSalesListResponse(
            products=products_sales,
            total=total,
            page=params.page,
            pageSize=params.pageSize,
            totalPages=total_pages
        )
    
    # ─────────────────────────────────────────
    # EXPORT ORDERS TO EXCEL
    # ─────────────────────────────────────────
    
    @staticmethod
    async def export_orders_to_excel(
        user_id: str,
        search: Optional[str] = None,
        status: Optional[OrderStatus] = None
    ) -> BytesIO:
        """Export orders list to Excel."""
        await OrderService._require_admin_or_manager(user_id)
        
        where_clause: Dict[str, Any] = {
            "orderType": "PRODUCT"
        }
        
        if status:
            where_clause["status"] = status
        
        # Fetch all orders
        orders = await prisma.order.find_many(
            where=where_clause,
            include={
                "user": True,
                "items": {
                    "include": {"product": True}
                }
            },
            order={"createdAt": "desc"}
        )
        
        # Prepare data for Excel
        export_data = []
        for order in orders:
            # Get product names
            product_names = ", ".join([
                item.product.name if item.product else "Unknown"
                for item in order.items
            ])
            
            total_quantity = sum(item.quantity for item in order.items)
            progress = OrderService._map_status_to_progress(order.status)
            
            export_data.append({
                "Order Number": order.orderNumber,
                "Product Name": product_names,
                "Ordered By": order.user.name if order.user else "Unknown",
                "Customer Email": order.user.email if order.user else "N/A",
                "Price (QAR)": order.total,
                "Quantity": total_quantity,
                "Order Date": order.createdAt.strftime("%Y-%m-%d"),
                "Progress": progress,
                "Status": order.status
            })
        
        # Apply search filter if provided
        if search:
            search_lower = search.lower()
            export_data = [
                row for row in export_data
                if search_lower in row["Product Name"].lower() or search_lower in row["Ordered By"].lower()
            ]
        
        # Create DataFrame
        df = pd.DataFrame(export_data)
        
        # Create Excel file in memory
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Orders', index=False)
            
            # Auto-adjust column widths
            worksheet = writer.sheets['Orders']
            for idx, col in enumerate(df.columns):
                max_length = max(df[col].astype(str).apply(len).max(), len(col)) + 2
                col_letter = chr(65 + idx) if idx < 26 else chr(65 + idx // 26 - 1) + chr(65 + idx % 26)
                worksheet.column_dimensions[col_letter].width = min(max_length, 50)
        
        output.seek(0)
        return output
    
    # ─────────────────────────────────────────
    # EXPORT PRODUCTS TO EXCEL (Sales view)
    # ─────────────────────────────────────────
    
    @staticmethod
    async def export_products_sales_to_excel(
        user_id: str,
        search: Optional[str] = None
    ) -> BytesIO:
        """Export product sales list to Excel."""
        await OrderService._require_admin_or_manager(user_id)
        
        where_clause: Dict[str, Any] = {}
        
        if search:
            where_clause["name"] = {"contains": search, "mode": "insensitive"}
        
        # Fetch all products
        products = await prisma.product.find_many(
            where=where_clause,
            include={"orderItems": True},
            order={"salesCount": "desc"}
        )
        
        # Prepare data for Excel
        export_data = []
        for product in products:
            total_revenue = sum(item.total for item in product.orderItems)
            
            export_data.append({
                "Product Name": product.name,
                "Available Product": product.stockQuantity,
                "Total Sell": product.salesCount,
                "Total Revenue (QAR)": total_revenue
            })
        
        # Create DataFrame
        df = pd.DataFrame(export_data)
        
        # Create Excel file in memory
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Product Sales', index=False)
            
            # Auto-adjust column widths
            worksheet = writer.sheets['Product Sales']
            for idx, col in enumerate(df.columns):
                max_length = max(df[col].astype(str).apply(len).max(), len(col)) + 2
                col_letter = chr(65 + idx) if idx < 26 else chr(65 + idx // 26 - 1) + chr(65 + idx % 26)
                worksheet.column_dimensions[col_letter].width = min(max_length, 50)
        
        output.seek(0)
        return output