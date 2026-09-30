from prisma import Prisma
from contextlib import asynccontextmanager

prisma = Prisma()

async def connect_db():
    """Connect to database"""
    await prisma.connect()

async def disconnect_db():
    """Disconnect from database"""
    await prisma.disconnect()

@asynccontextmanager
async def get_db():
    """Dependency for database access"""
    try:
        yield prisma
    finally:
        pass