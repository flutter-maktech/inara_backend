from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from prisma.enums import ProductStatus


# ============================================
# PRODUCT/STORE MODELS (Admin manages products)
# ============================================

class ProductBase(BaseModel):
    """Base product model matching UI"""
    name: str  # Product Title
    description: Optional[str] = None  # Full Description
    shortDescription: Optional[str] = None  # Pick Up Note / Short description
    material: Optional[str] = None  # Material (e.g., "Cotton", "EVA foam")
    dimensions: Optional[str] = None  # Dimensions (e.g., "23 cm × 15 cm × 7.5 cm")
    price: float  # Price (QAR)
    stockQuantity: int = 0  # Stock Level
    
    # Media - up to 4 images
    images: List[str] = []  # Array of image URLs (max 4)
    thumbnail: Optional[str] = None  # Main thumbnail (first image typically)
    
    # Additional fields
    weight: Optional[float] = None
    color: Optional[str] = None
    size: Optional[str] = None
    discountPrice: Optional[float] = None
    sku: Optional[str] = None  # Stock Keeping Unit
    tags: List[str] = []
    categoryId: Optional[str] = None


class ProductCreate(ProductBase):
    """Create new product"""
    slug: Optional[str] = None  # Auto-generated from name if not provided
    status: ProductStatus = ProductStatus.AVAILABLE


class ProductUpdate(BaseModel):
    """Update existing product - all fields optional"""
    name: Optional[str] = None
    description: Optional[str] = None
    shortDescription: Optional[str] = None
    material: Optional[str] = None
    dimensions: Optional[str] = None
    price: Optional[float] = None
    stockQuantity: Optional[int] = None
    images: Optional[List[str]] = None
    thumbnail: Optional[str] = None
    weight: Optional[float] = None
    color: Optional[str] = None
    size: Optional[str] = None
    discountPrice: Optional[float] = None
    sku: Optional[str] = None
    tags: Optional[List[str]] = None
    categoryId: Optional[str] = None
    status: Optional[ProductStatus] = None
    isFeatured: Optional[bool] = None


class ProductBrief(BaseModel):
    """Brief product info for listing (UI card)"""
    id: str
    name: str
    thumbnail: Optional[str] = None
    shortDescription: Optional[str] = None
    stockQuantity: int
    price: float
    images: Optional[List[str]] = None
    discountPrice: Optional[float] = None
    status: ProductStatus
    
    class Config:
        from_attributes = True


class ProductResponse(ProductBase):
    """Full product response"""
    id: str
    slug: Optional[str] = None
    status: ProductStatus
    isFeatured: bool
    viewCount: int
    salesCount: int
    averageRating: Optional[float] = None
    totalReviews: int
    createdAt: datetime
    updatedAt: datetime
    
    class Config:
        from_attributes = True


class ProductListResponse(BaseModel):
    """Paginated product list"""
    products: List[ProductBrief]
    total: int
    page: int
    pageSize: int
    totalPages: int


# ============================================
# SEARCH & FILTER MODELS
# ============================================

class ProductSearchParams(BaseModel):
    """Search and filter parameters"""
    search: Optional[str] = None  # Search by name or description
    status: Optional[ProductStatus] = None
    minPrice: Optional[float] = None
    maxPrice: Optional[float] = None
    inStock: Optional[bool] = None  # Filter: stockQuantity > 0
    page: int = Field(1, ge=1)
    pageSize: int = Field(10, ge=1, le=100)
    sortBy: str = "createdAt"
    sortOrder: str = "desc"