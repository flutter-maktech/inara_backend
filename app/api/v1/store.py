"""
Store API Router (Product Management)
======================================
Store = Admin/Manager creates and manages products for sale.

Endpoints:
  GET    /store/products              → Paginated product list + search
  POST   /store/products              → Add new product
  GET    /store/products/{id}         → Get product by ID
  PATCH  /store/products/{id}         → Edit product
  DELETE /store/products/{id}         → Delete product
  GET    /store/products/export/excel → Export products to Excel
"""

from fastapi import APIRouter, Depends, Response, status, UploadFile, Form, File, Query
from typing import Optional, List
import json
from app.models.product_model import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    ProductListResponse,
    ProductSearchParams,
)
from app.core.cloudinary_service import upload_multiple_images
from app.services.product_service import ProductService
from app.api.v1.dependencies import get_current_active_user
from prisma.enums import ProductStatus

router = APIRouter()


# ─────────────────────────────────────────────────────────────────
# EXPORT — {product_id} to avoid routing conflict
# ─────────────────────────────────────────────────────────────────

@router.get("/products/export/excel")
async def export_products_to_excel(
    search: Optional[str] = None,
    status: Optional[ProductStatus] = None,
    current_user=Depends(get_current_active_user)
):
    """
    Export product list to Excel (.xlsx).

    **UI Reference:** Download button on Store page (Image 1).
    **Access:** Admin / Manager only
    """
    excel_file = await ProductService.export_products_to_excel(
        user_id=current_user.id,
        search=search,
        status=status
    )
    return Response(
        content=excel_file.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=products_export.xlsx"}
    )


# ─────────────────────────────────────────────────────────────────
# GET ALL PRODUCTS
# ─────────────────────────────────────────────────────────────────

@router.get("/products", response_model=ProductListResponse)
async def get_all_products(
    search: Optional[str] = None,
    status: Optional[ProductStatus] = None,
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    inStock: Optional[bool] = None,
    page: int = 1,
    pageSize: int = 10,
    sortBy: str = "createdAt",
    sortOrder: str = "desc",
    current_user=Depends(get_current_active_user)
):
    """
    Get all products with search and filters.

    **Returns:** Brief product info (thumbnail, title, short description, stock, price)
    **Features:** Search by name/description
    **UI Reference:** Store product grid
    **Access:** All authenticated users (Admin, Manager, User)
    """
    params = ProductSearchParams(
        search=search,
        status=status,
        minPrice=minPrice,
        maxPrice=maxPrice,
        inStock=inStock,
        page=page,
        pageSize=pageSize,
        sortBy=sortBy,
        sortOrder=sortOrder
    )
    return await ProductService.get_all_products(current_user.id, params)


# ─────────────────────────────────────────────────────────────────
# CREATE PRODUCT
# ─────────────────────────────────────────────────────────────────

@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    # ── Text fields (Form) ──────────────────────────────
    name: str = Form(...),
    slug: Optional[str] = Form(None),
    price: float = Form(...),
    stockQuantity: int = Form(...),
    status: ProductStatus = Form(ProductStatus.AVAILABLE),
    
    description: Optional[str] = Form(None),
    shortDescription: Optional[str] = Form(None),
    material: Optional[str] = Form(None),
    dimensions: Optional[str] = Form(None),
    weight: Optional[float] = Form(None),
    color: Optional[str] = Form(None),
    size: Optional[str] = Form(None),
    discountPrice: Optional[float] = Form(None),
    sku: Optional[str] = Form(None),
    categoryId: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    
    # ── Image files ──────────────────────────────────────
    images: List[UploadFile] = File(default=[]),
    
    current_user=Depends(get_current_active_user)
):
    # Tags parse 
    parsed_tags = []
    if tags:
        try:
            parsed_tags = json.loads(tags)
        except json.JSONDecodeError:
            parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]
    
    # Images Cloudinary upload
    image_urls: List[str] = []
    if images:
        valid_images = [img for img in images if img.filename]
        if valid_images:
            image_urls = await upload_multiple_images(valid_images, folder="products")
    
    # Thumbnail image
    thumbnail_url = image_urls[0] if image_urls else None
    
    # Dict ProductCreate object 
    product_obj = ProductCreate(
        name=name,
        slug=slug,
        price=price,
        stockQuantity=stockQuantity,
        status=status,
        description=description,
        shortDescription=shortDescription,
        material=material,
        dimensions=dimensions,
        weight=weight,
        color=color,
        size=size,
        discountPrice=discountPrice,
        sku=sku,
        categoryId=categoryId,
        tags=parsed_tags,
        images=image_urls,
        thumbnail=thumbnail_url,
    )
    
    return await ProductService.create_product(product_obj, current_user.id)

# ─────────────────────────────────────────────────────────────────
# GET PRODUCT BY ID
# ─────────────────────────────────────────────────────────────────

@router.get("/products/{product_id}", response_model=ProductResponse)
async def get_product_by_id(
    product_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Get full product details by ID.

    **Access:** All authenticated users (Admin, Manager, User)
    """
    return await ProductService.get_product_by_id(product_id, current_user.id)


# ─────────────────────────────────────────────────────────────────
# UPDATE PRODUCT
# ────────────────────────────────────────────────────────────────-

@router.patch("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: str,

    # ── Text fields (Form) ──────────────────────────────
    name: Optional[str] = Form(None),
    slug: Optional[str] = Form(None),
    price: Optional[float] = Form(None),
    stockQuantity: Optional[int] = Form(None),
    status: Optional[ProductStatus] = Form(None),

    description: Optional[str] = Form(None),
    shortDescription: Optional[str] = Form(None),
    material: Optional[str] = Form(None),
    dimensions: Optional[str] = Form(None),
    weight: Optional[float] = Form(None),
    color: Optional[str] = Form(None),
    size: Optional[str] = Form(None),
    discountPrice: Optional[float] = Form(None),
    sku: Optional[str] = Form(None),
    categoryId: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),

    # ── Image files ──────────────────────────────────────
    images: List[UploadFile] = File(default=[]),

    current_user=Depends(get_current_active_user)
):

    # ── Tags parse ──────────────────────────────────────
    parsed_tags = None
    if tags is not None:
        try:
            parsed_tags = json.loads(tags)
        except json.JSONDecodeError:
            parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]

    # ── Upload images (only if provided) ─────────────────
    image_urls = None
    thumbnail_url = None

    if images:
        valid_images = [img for img in images if img.filename]
        if valid_images:
            image_urls = await upload_multiple_images(valid_images, folder="products")
            thumbnail_url = image_urls[0]

    # ── Build update data ───────────────────────────────
    update_data = {
        "name": name,
        "slug": slug,
        "price": price,
        "stockQuantity": stockQuantity,
        "status": status,
        "description": description,
        "shortDescription": shortDescription,
        "material": material,
        "dimensions": dimensions,
        "weight": weight,
        "color": color,
        "size": size,
        "discountPrice": discountPrice,
        "sku": sku,
        "categoryId": categoryId,
        "tags": parsed_tags,
    }

    # images only if uploaded
    if image_urls is not None:
        update_data["images"] = image_urls
        update_data["thumbnail"] = thumbnail_url

    # Remove None values for PATCH behavior
    update_data = {k: v for k, v in update_data.items() if v is not None}

    data = ProductUpdate(**update_data)

    return await ProductService.update_product(
        product_id,
        data,
        current_user.id
    )


# ─────────────────────────────────────────────────────────────────
# DELETE PRODUCT
# ─────────────────────────────────────────────────────────────────

@router.delete("/products/{product_id}", status_code=status.HTTP_200_OK)
async def delete_product(
    product_id: str,
    force: bool = Query(
        default=False,
        description=(
            "false (default) — Safe soft-delete: marks product as DISCONTINUED and "
            "removes it from all active carts. Order history is preserved. "
            "Accessible by Admin and Manager. | "
            "true — Permanent hard-delete: purges all cart_items then deletes the "
            "product record. Irreversible. Admin only."
        ),
    ),
    current_user=Depends(get_current_active_user),
):
    """
    Delete a product.

    **Default behaviour (`force=false`):**
    Marks the product as `DISCONTINUED`, zeroes stock, and removes it from
    every active cart. The product record is kept so order history stays intact.
    Available to Admin and Manager.

    **Force delete (`force=true`):**
    Permanently erases the product and all its cart references.
    Intended only for products created in error with no order history.
    Admin only.

    **UI Reference:** Delete button on product card
    """
    return await ProductService.delete_product(
        product_id=product_id,
        deleted_by_user_id=current_user.id,
        force=force,
    )