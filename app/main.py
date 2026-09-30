from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from datetime import datetime
from app.core.config import settings as app_settings
from app.db.db_client import connect_db, disconnect_db
from app.core.service_key_middleware import ServiceKeyMiddleware
from app.api.v1 import (
    auth, users, dashboard_analytics, classes, courses,
    memberships, packages, members, instructors, managers, store, order, news, aboutus, roleMatrix, privacy_policy, terms_conditions, faq,
    payments, notifications, settings as settings_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await disconnect_db()


app = FastAPI(
    title=app_settings.APP_NAME,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Read-only Service API Keys (X-API-Key): method gate, endpoint allowlist,
# optional IP allowlist and rate limit, all enforced before routing.
# Added BEFORE CORS so CORS stays the outermost layer.
app.add_middleware(ServiceKeyMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=app_settings.get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================
# PUBLIC HEALTH
# ============================================

@app.get("/health")
async def global_health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow(),
        "version": "1.0.0"
    }


# ============================================
# API ROUTERS
# ============================================

app.include_router(auth.router,                 prefix=f"{app_settings.API_V1_PREFIX}/auth",         tags=["Authentication"])
app.include_router(users.router,                prefix=f"{app_settings.API_V1_PREFIX}/users",        tags=["Users"])
app.include_router(dashboard_analytics.router,  prefix=f"{app_settings.API_V1_PREFIX}/analytics",   tags=["Dashboard Analytics"])
app.include_router(classes.router,              prefix=f"{app_settings.API_V1_PREFIX}/classes",      tags=["Classes"])
app.include_router(courses.router,              prefix=f"{app_settings.API_V1_PREFIX}/courses",      tags=["Courses"])
app.include_router(members.router,              prefix=f"{app_settings.API_V1_PREFIX}/members",      tags=["Members"])
app.include_router(instructors.router,          prefix=f"{app_settings.API_V1_PREFIX}/instructors",  tags=["Instructors"])
app.include_router(managers.router,             prefix=f"{app_settings.API_V1_PREFIX}/managers",     tags=["Managers"])
app.include_router(memberships.router,          prefix=f"{app_settings.API_V1_PREFIX}/memberships",  tags=["Memberships"])
app.include_router(packages.router,             prefix=f"{app_settings.API_V1_PREFIX}/packages",     tags=["Packages"])
app.include_router(store.router,                prefix=f"{app_settings.API_V1_PREFIX}/store",        tags=["Store"])
app.include_router(order.router,                prefix=f"{app_settings.API_V1_PREFIX}/order",        tags=["Order"])
app.include_router(payments.router,             prefix=f"{app_settings.API_V1_PREFIX}/payments",     tags=["Payments"])
app.include_router(notifications.router,        prefix=f"{app_settings.API_V1_PREFIX}/notifications", tags=["Notifications"])
app.include_router(settings_router.router,      prefix=f"{app_settings.API_V1_PREFIX}/settings",     tags=["Settings"])
app.include_router(news.router,                 prefix=f"{app_settings.API_V1_PREFIX}/news",          tags=["News"])
app.include_router(aboutus.router,              prefix=f"{app_settings.API_V1_PREFIX}/about-us",      tags=["About Us"])
app.include_router(roleMatrix.router,           prefix=f"{app_settings.API_V1_PREFIX}/role-matrix",   tags=["Role Matrix"])
app.include_router(privacy_policy.router,       prefix=f"{app_settings.API_V1_PREFIX}/privacy-policy",   tags=["Legal ~ Privacy Policy"])
app.include_router(terms_conditions.router,     prefix=f"{app_settings.API_V1_PREFIX}/terms-conditions",  tags=["Legal ~ Terms and Conditions"])
app.include_router(faq.router,                  prefix=f"{app_settings.API_V1_PREFIX}/faq",               tags=["Legal ~ FAQ"])

@app.get("/")
async def root():
    return {
        "message": "INARA Platform API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/healthy"
    }