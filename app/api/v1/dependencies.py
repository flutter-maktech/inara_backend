from typing import Optional

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from jose import JWTError, jwt
from app.core import security
from app.core.config import settings
from app.db.db_client import prisma
from app.models.auth import TokenData

# auto_error=False: a request authenticated by a service API key has no Bearer
# token. The "Not authenticated" 401 is raised below instead, with the same
# status, detail and header as before.
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_PREFIX}/auth/login",
    auto_error=False,
)

# Documents the read-only key in Swagger ("Authorize" → X-API-Key).
# Validation and enforcement happen in ServiceKeyMiddleware; this is for docs only.
service_api_key_scheme = APIKeyHeader(
    name=settings.SERVICE_KEY_HEADER_NAME,
    auto_error=False,
    scheme_name="ServiceAPIKey",
    description="Read-only service API key (GET only). Issued by the INARA team.",
)


async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    service_api_key: Optional[str] = Security(service_api_key_scheme),
):
    # ── Path 1: read-only service key (already validated by the middleware) ──
    service_user = getattr(request.state, "service_principal", None)
    if service_user is not None:
        return service_user

    # ── Path 2: normal Bearer JWT (unchanged behaviour) ──
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = security.decode_access_token(token)
        if payload is None:
            raise credentials_exception
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email)
    except JWTError:
        raise credentials_exception

    user = await prisma.user.find_unique(where={"email": token_data.email})
    if user is None:
        raise credentials_exception
    return user

async def get_current_active_user(current_user = Depends(get_current_user)):
    if not current_user.isActive:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user
