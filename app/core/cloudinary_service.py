import cloudinary
import cloudinary.uploader
from fastapi import UploadFile, HTTPException
from app.core.config import settings
import uuid

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
    secure=True
)

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

async def upload_image(file: UploadFile, folder: str = "products") -> str:
    """Single image Cloudinary তে upload করে URL return করে"""
    
    # Validate type
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type: {file.content_type}. Allowed: JPEG, PNG, WEBP, GIF"
        )
    
    # Read & validate size
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Max size: 5MB"
        )
    
    try:
        public_id = f"{folder}/{uuid.uuid4().hex}"
        
        result = cloudinary.uploader.upload(
            contents,
            public_id=public_id,
            folder=folder,
            resource_type="image",
            transformation=[
                {"quality": "auto"},       # Auto quality optimization
                {"fetch_format": "auto"},  # Auto format (WebP for supported browsers)
            ]
        )
        return result["secure_url"]
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Image upload failed: {str(e)}"
        )


async def upload_multiple_images(
    files: list[UploadFile],
    folder: str = "products"
) -> list[str]:
    """Multiple images upload করে URL list return করে"""
    
    if len(files) > 4:
        raise HTTPException(
            status_code=400,
            detail="Maximum 4 images allowed"
        )
    
    urls = []
    for file in files:
        url = await upload_image(file, folder)
        urls.append(url)
    
    return urls


async def delete_image(public_id: str) -> bool:
    """Cloudinary থেকে image delete করে"""
    try:
        result = cloudinary.uploader.destroy(public_id)
        return result.get("result") == "ok"
    except Exception:
        return False