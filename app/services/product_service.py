"""
ProductService — Business logic for the Store (Product Management).

Store = Admin/Manager creates and manages products for sale.
Users can browse, add to cart, and purchase these products.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DELETE STRATEGY  (Enterprise Standard)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  DEFAULT (force=False) — Soft-delete  [Admin only]
    • Marks product DISCONTINUED, zeroes stock.
    • Removes product from every active cart (satisfies cart_items FK RESTRICT).
    • Product record KEPT — order history, invoices, analytics intact.

  FORCE   (force=True)  — Hard-delete  [Admin only]
    • Works on a product in ANY status (AVAILABLE or DISCONTINUED).
    • Purges ALL three RESTRICT FK relations in a single atomic transaction:
        1. cart_items   (cart_items.product_id  → products.id  RESTRICT)
        2. order_items  (order_items.product_id → products.id  RESTRICT)
        3. reviews      (reviews.product_id     → products.id  CASCADE  — auto, but included for clarity)
    • Then hard-deletes the product row.
    • Irreversible. Use only for products created by mistake.

  Policy reference: see app/core/permissions.py — Manager cannot DELETE.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ROOT CAUSE OF PERSISTENT 500 (this final fix):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  The schema has THREE tables with FK references to `products`:

    model CartItem   → product  Product  @relation(...)          ← RESTRICT (no onDelete clause)
    model OrderItem  → product  Product  @relation(...)          ← RESTRICT (no onDelete clause)
    model Review     → product  Product? @relation(..., onDelete: Cascade)  ← auto-handled

  Previous fix only cleaned cart_items before deleting the product.
  When a DISCONTINUED product had order_items rows (which it always does if
  it was ever purchased), Postgres raised FK RESTRICT on order_items.product_id
  → unhandled DataError → 500 Internal Server Error.

  Fix: The hard-delete transaction now explicitly deletes BOTH cart_items AND
  order_items before deleting the product. Reviews are CASCADE so Postgres
  handles them automatically, but we include them explicitly for safety.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any
from io import BytesIO

import pandas as pd
from fastapi import HTTPException, status

from app.db.db_client import prisma
from app.models.product_model import (
    ProductCreate,
    ProductUpdate,
    ProductBrief,
    ProductResponse,
    ProductListResponse,
    ProductSearchParams,
)
from prisma.enums import UserRole, ProductStatus
from app.core.permissions import can as _policy_can, Resource as _Resource


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class ProductService:
    """
    Enterprise-grade Product/Store service.

    Features:
    - Full CRUD for products
    - Soft-delete (DISCONTINUED) with atomic cart cleanup
    - Hard-delete (Admin only): resolves ALL FK constraints before deleting
    - Search, filtering & pagination
    - Excel export

    Access: Admin/Manager for writes, all authenticated users for reads.
    """

    # ─────────────────────────────────────────
    # ROLE GUARDS
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
        """Admin-only gate — used for irreversible / destructive operations."""
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user or user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required for force-delete",
            )

    @staticmethod
    async def _require_authenticated(user_id: str) -> None:
        """Any authenticated user (ADMIN, MANAGER, INSTRUCTOR, USER)."""
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Authentication required",
            )

    # ─────────────────────────────────────────
    # CREATE PRODUCT
    # ─────────────────────────────────────────

    @staticmethod
    async def create_product(
        data: ProductCreate,
        created_by_user_id: str,
    ) -> ProductResponse:
        """Create a new product. Admin/Manager only."""
        await ProductService._require_admin_or_manager(created_by_user_id)

        if data.slug:
            existing = await prisma.product.find_unique(where={"slug": data.slug})
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Product with slug '{data.slug}' already exists",
                )

        product = await prisma.product.create(data=data.model_dump())
        return ProductResponse(**product.model_dump())

    # ─────────────────────────────────────────
    # GET ALL PRODUCTS
    # ─────────────────────────────────────────

    @staticmethod
    async def get_all_products(
        user_id: str,
        params: ProductSearchParams,
    ) -> ProductListResponse:
        """
        Paginated product list with search & filters.
        All authenticated users (Admin, Manager, User).
        """
        await ProductService._require_authenticated(user_id)

        where_clause: Dict[str, Any] = {}

        if params.search:
            where_clause["OR"] = [
                {"name": {"contains": params.search, "mode": "insensitive"}},
                {"description": {"contains": params.search, "mode": "insensitive"}},
            ]

        if params.status:
            where_clause["status"] = params.status

        if params.minPrice is not None:
            where_clause.setdefault("price", {})["gte"] = params.minPrice

        if params.maxPrice is not None:
            where_clause.setdefault("price", {})["lte"] = params.maxPrice

        if params.inStock is not None:
            where_clause["stockQuantity"] = {"gt": 0} if params.inStock else {"lte": 0}

        total = await prisma.product.count(where=where_clause)
        skip = (params.page - 1) * params.pageSize
        total_pages = max(1, (total + params.pageSize - 1) // params.pageSize)

        products = await prisma.product.find_many(
            where=where_clause,
            skip=skip,
            take=params.pageSize,
            order={params.sortBy: params.sortOrder},
        )

        products_brief = [
            ProductBrief(
                id=p.id,
                name=p.name,
                thumbnail=p.thumbnail,
                shortDescription=p.shortDescription,
                stockQuantity=p.stockQuantity,
                price=p.price,
                discountPrice=p.discountPrice,
                status=p.status,
                images=p.images or [],
            )
            for p in products
        ]

        return ProductListResponse(
            products=products_brief,
            total=total,
            page=params.page,
            pageSize=params.pageSize,
            totalPages=total_pages,
        )

    # ─────────────────────────────────────────
    # GET PRODUCT BY ID
    # ─────────────────────────────────────────

    @staticmethod
    async def get_product_by_id(
        product_id: str,
        user_id: str,
    ) -> ProductResponse:
        """Full product details. All authenticated users."""
        await ProductService._require_authenticated(user_id)

        product = await prisma.product.find_unique(where={"id": product_id})
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found",
            )

        return ProductResponse(**product.model_dump())

    # ─────────────────────────────────────────
    # UPDATE PRODUCT
    # ─────────────────────────────────────────

    @staticmethod
    async def update_product(
        product_id: str,
        data: ProductUpdate,
        updated_by_user_id: str,
    ) -> ProductResponse:
        """Partial update. Admin/Manager only."""
        await ProductService._require_admin_or_manager(updated_by_user_id)

        product = await prisma.product.find_unique(where={"id": product_id})
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found",
            )

        update_data = data.model_dump(exclude_unset=True)
        updated_product = await prisma.product.update(
            where={"id": product_id},
            data=update_data,
        )

        return ProductResponse(**updated_product.model_dump())

    # ─────────────────────────────────────────
    # DELETE PRODUCT  ✅  DEFINITIVE FIX
    # ─────────────────────────────────────────

    @staticmethod
    async def delete_product(
        product_id: str,
        deleted_by_user_id: str,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Two-path delete. Works on products in ANY status.

        ── PATH B  force=True  (Admin only) ──────────────────────────────
        Hard-delete. Resolves EVERY FK relation before deleting the product.

        The `products` table is referenced by THREE child tables:

          Table         FK field     onDelete in schema   Action needed
          ──────────    ──────────   ──────────────────   ─────────────
          cart_items    product_id   RESTRICT (default)   Manual delete ← was done
          order_items   product_id   RESTRICT (default)   Manual delete ← was MISSING (root cause of 500)
          reviews       product_id   CASCADE              Auto by Postgres (included anyway for clarity)

        All three are purged in a SINGLE atomic transaction before the product
        row is removed. Counts are captured BEFORE the transaction because
        tx.delete_many() returns None in Prisma Python (not BatchPayload).

        ── PATH A  force=False  (Admin & Manager) ────────────────────────
        Soft-delete. Marks product DISCONTINUED, zeroes stock.
        Only cart_items are cleaned (order_items are preserved for history).
        Product row is kept so orders / invoices remain consistent.
        Idempotent: already-DISCONTINUED products return 200 immediately.
        """

        # ── Verify product exists (shared by both paths) ──────────────────
        product = await prisma.product.find_unique(where={"id": product_id})
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found",
            )

        # ═════════════════════════════════════════════════════════════════
        # PATH B — HARD DELETE  (force=True)
        # Evaluated FIRST — no status gate, works on AVAILABLE & DISCONTINUED
        # ═════════════════════════════════════════════════════════════════
        if force:
            # Role check first — fail fast before touching anything
            await ProductService._require_admin(deleted_by_user_id)

            # ── Count ALL child rows BEFORE the transaction ───────────────
            # tx.delete_many() returns None inside a Prisma Python transaction,
            # not BatchPayload — so we NEVER read its return value.
            # We capture counts here as plain ints for the response payload.
            cart_items_count: int = await prisma.cartitem.count(
                where={"productId": product_id}
            )
            order_items_count: int = await prisma.orderitem.count(
                where={"productId": product_id}
            )

            # ── Single atomic transaction ─────────────────────────────────
            # Deletion order matters: children first, parent last.
            #
            #  Step 1 — cart_items   (RESTRICT FK → must delete manually)
            #  Step 2 — order_items  (RESTRICT FK → must delete manually) ← THE MISSING PIECE
            #  Step 3 — reviews      (CASCADE FK  → Postgres handles it,
            #                         but explicit delete is safer and faster)
            #  Step 4 — product row  (now safe — no dangling child rows remain)
            async with prisma.tx() as tx:
                await tx.cartitem.delete_many(where={"productId": product_id})
                await tx.orderitem.delete_many(where={"productId": product_id})
                await tx.review.delete_many(where={"productId": product_id})
                await tx.product.delete(where={"id": product_id})

            return {
                "message": "Product permanently deleted",
                "productId": product_id,
                "action": "hard_delete",
                "previousStatus": product.status,
                "cartItemsRemoved": cart_items_count,
                "orderItemsRemoved": order_items_count,
            }

        # ═════════════════════════════════════════════════════════════════
        # PATH A — SOFT DELETE  (force=False, Admin only)
        # Policy: Manager cannot delete (soft or hard) any resource.
        # ═════════════════════════════════════════════════════════════════
        await ProductService._require_admin(deleted_by_user_id)

        # Idempotency guard — already discontinued, nothing to do
        if product.status == ProductStatus.DISCONTINUED:
            return {
                "message": "Product is already discontinued",
                "productId": product_id,
                "action": "soft_delete",
                "cartItemsRemoved": 0,
            }

        # Count cart references BEFORE the transaction (tx.delete_many → None)
        cart_items_count = await prisma.cartitem.count(
            where={"productId": product_id}
        )

        # Atomic: clean cart refs → mark product discontinued
        # order_items are NOT touched — they represent real purchase history
        async with prisma.tx() as tx:
            await tx.cartitem.delete_many(where={"productId": product_id})
            await tx.product.update(
                where={"id": product_id},
                data={
                    "status": ProductStatus.DISCONTINUED,
                    "stockQuantity": 0,
                },
            )

        return {
            "message": "Product discontinued successfully",
            "productId": product_id,
            "action": "soft_delete",
            "cartItemsRemoved": cart_items_count,
        }

    # ─────────────────────────────────────────
    # EXPORT PRODUCTS TO EXCEL
    # ─────────────────────────────────────────

    @staticmethod
    async def export_products_to_excel(
        user_id: str,
        search: Optional[str] = None,
        status: Optional[ProductStatus] = None,
    ) -> BytesIO:
        """Export products list to Excel. Admin/Manager only."""
        await ProductService._require_admin_or_manager(user_id)

        where_clause: Dict[str, Any] = {}

        if search:
            where_clause["OR"] = [
                {"name": {"contains": search, "mode": "insensitive"}},
                {"description": {"contains": search, "mode": "insensitive"}},
            ]

        if status:
            where_clause["status"] = status

        products = await prisma.product.find_many(
            where=where_clause,
            order={"createdAt": "desc"},
        )

        export_data = [
            {
                "Product Name": p.name,
                "Short Description": p.shortDescription or "N/A",
                "Material": p.material or "N/A",
                "Dimensions": p.dimensions or "N/A",
                "Price (QAR)": p.price,
                "Discount Price (QAR)": p.discountPrice or "N/A",
                "Stock Quantity": p.stockQuantity,
                "Status": p.status,
                "Sales Count": p.salesCount,
                "Average Rating": p.averageRating or "N/A",
                "Created Date": p.createdAt.strftime("%Y-%m-%d"),
            }
            for p in products
        ]

        df = pd.DataFrame(export_data)

        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Products", index=False)

            worksheet = writer.sheets["Products"]
            for idx, col in enumerate(df.columns):
                max_length = (
                    max(df[col].astype(str).apply(len).max(), len(col)) + 2
                )
                col_letter = (
                    chr(65 + idx)
                    if idx < 26
                    else chr(65 + idx // 26 - 1) + chr(65 + idx % 26)
                )
                worksheet.column_dimensions[col_letter].width = min(max_length, 50)

        output.seek(0)
        return output