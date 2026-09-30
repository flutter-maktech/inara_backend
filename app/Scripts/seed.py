"""
INARA PROJECT — COMPREHENSIVE SEED FILE
========================================
Schema v3 (Payment System):
  - Wallet / WalletTransaction: every user gets a wallet; deposits + debits recorded
  - PaymentLog: central audit trail covering WALLET, BOOKING, MEMBERSHIP, PACKAGE, ORDER modules
  - Booking: now carries paymentMethod, amountPaid, paymentLogId
  - Membership: now carries paymentMethod, paymentLogId, isPaid
  - Order: shipping removed — pickupNote added; lifecycle PENDING→PAID→PROCESSING→PICKEDUP
  - Product: pickupNote added
  - MemberInvitation: seeded (was only cleared before)
  - OrderStatus: covers PENDING, PAID, PROCESSING, PICKEDUP, FAILED, REFUNDED, CANCELLED
  - PaymentMethod: covers CREDIT_CARD, DEBIT_CARD, APPLE_PAY, GOOGLE_PAY, WALLET, GATEWAY
  - All enum values across the schema are exercised at least once
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from prisma import Prisma, Json
from passlib.context import CryptContext

prisma = Prisma()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ============================================
# HELPERS
# ============================================

def hp(password: str) -> str:
    return pwd_context.hash(password)

def future(days=0, hours=0) -> datetime:
    return datetime.now() + timedelta(days=days, hours=hours)

def past(days=0, hours=0) -> datetime:
    return datetime.now() - timedelta(days=days, hours=hours)

def future_at(days=0, hour=9, minute=0) -> datetime:
    base = datetime.now() + timedelta(days=days)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)

def idem_key() -> str:
    """Generate a unique idempotency key."""
    return uuid.uuid4().hex


# ============================================
# CLEAR ALL  (dependency order — children first)
# ============================================

async def clear_all():
    print("🗑️  Clearing existing data...")
    await prisma.invitation.delete_many()
    await prisma.memberinvitation.delete_many()
    await prisma.permissionrule.delete_many()
    await prisma.legaldocument.delete_many()
    await prisma.aboutus.delete_many()
    await prisma.news.delete_many()
    await prisma.review.delete_many()
    await prisma.wishlist.delete_many()
    await prisma.cartitem.delete_many()
    await prisma.orderitem.delete_many()
    await prisma.order.delete_many()
    await prisma.notification.delete_many()
    await prisma.booking.delete_many()
    await prisma.membership.delete_many()
    await prisma.classes.delete_many()
    await prisma.course.delete_many()
    await prisma.package.delete_many()
    await prisma.product.delete_many()
    await prisma.otp.delete_many()
    # Payment tables — child before parent
    await prisma.wallettransaction.delete_many()
    await prisma.wallet.delete_many()
    await prisma.paymentlog.delete_many()
    await prisma.user.delete_many()
    print("✅ All data cleared\n")


# ============================================
# USERS
# ============================================

async def seed_users() -> dict:
    print("👥 Seeding users...")

    users_raw = [
        # ── Admin ─────────────────────────────────────────────────────────────
        {
            "email": "admin@inara.com",
            "passwordHash": hp("Admin123!"),
            "name": "Admin User",
            "role": "ADMIN",
            "phone": "+97444111111",
            "gender": "Male",
            "isActive": True,
            "isVerified": True,
            "bio": "Platform administrator",
            "address": "Doha, Qatar",
            "dateOfBirth": "15-06",          # DD-MM format — year omitted for privacy
            "lastLoginAt": past(hours=1),
        },
        # ── Manager ───────────────────────────────────────────────────────────
        {
            "email": "manager@inara.com",
            "passwordHash": hp("Manager123!"),
            "name": "Sarah Manager",
            "role": "MANAGER",
            "phone": "+97444111222",
            "gender": "Female",
            "isActive": True,
            "isVerified": True,
            "bio": "Operations manager",
            "address": "Doha, Qatar",
            "dateOfBirth": "22-03",
            "lastLoginAt": past(hours=3),
        },
        # ── Instructors ───────────────────────────────────────────────────────
        {
            "email": "savannah@inara.com",
            "passwordHash": hp("Instructor123!"),
            "name": "Savannah Nguyen",
            "role": "INSTRUCTOR",
            "phone": "+97444222111",
            "gender": "Female",
            "isActive": True,
            "isVerified": True,
            "speciality": "Yoga & Mindfulness",
            "bio": "Senior Yoga & Mindfulness Instructor",
            "address": "https://images.unsplash.com/photo-1506126613408-eca07ce68773?w=800",
            "avatar": "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=200",
            "dateOfBirth": "10-09",
        },
        {
            "email": "jane@inara.com",
            "passwordHash": hp("Instructor123!"),
            "name": "Jane Cooper",
            "role": "INSTRUCTOR",
            "phone": "+97444222222",
            "gender": "Female",
            "isActive": True,
            "isVerified": True,
            "speciality": "Pilates & Barre",
            "bio": "Certified Pilates & Barre Instructor",
            "address": "https://images.unsplash.com/photo-1518611012118-696072aa579a?w=800",
            "avatar": "https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=200",
            "dateOfBirth": "04-12",
        },
        {
            "email": "mike@inara.com",
            "passwordHash": hp("Instructor123!"),
            "name": "Mike Thompson",
            "role": "INSTRUCTOR",
            "phone": "+97444222333",
            "gender": "Male",
            "isActive": True,
            "isVerified": True,
            "speciality": "Strength & Conditioning",
            "bio": "Strength & Conditioning Coach",
            "address": "https://images.unsplash.com/photo-1526506118085-60ce8714f8c5?w=800",
            "avatar": "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=200",
            "dateOfBirth": "28-07",
        },
        {
            "email": "courtney@inara.com",
            "passwordHash": hp("Instructor123!"),
            "name": "Courtney Henry",
            "role": "INSTRUCTOR",
            "phone": "+97444222444",
            "gender": "Female",
            "isActive": True,
            "isVerified": True,
            "speciality": "Hatha Yoga & Meditation",
            "bio": "Hatha Yoga & Meditation Specialist",
            "address": "https://images.unsplash.com/photo-1545389336-cf090694435e?w=800",
            "avatar": "https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=200",
            "dateOfBirth": "17-01",
        },
        {
            "email": "emma@inara.com",
            "passwordHash": hp("Instructor123!"),
            "name": "Emma Wilson",
            "role": "INSTRUCTOR",
            "phone": "+97444222555",
            "gender": "Female",
            "isActive": True,
            "isVerified": True,
            "speciality": "Breathwork & Restorative Yoga",
            "bio": "Breathwork & Restorative Yoga Instructor",
            "address": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=800",
            "avatar": "https://images.unsplash.com/photo-1508214751196-bcfd4ca60f91?w=200",
            "dateOfBirth": "05-11",
        },
        {
            "email": "david@inara.com",
            "passwordHash": hp("Instructor123!"),
            "name": "David Kim",
            "role": "INSTRUCTOR",
            "phone": "+97444222666",
            "gender": "Male",
            "isActive": True,
            "isVerified": True,
            "speciality": "Functional Fitness & Calisthenics",
            "bio": "Functional Fitness & Calisthenics Coach",
            "address": "https://images.unsplash.com/photo-1517836357463-d25dfeac3438?w=800",
            "avatar": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=200",
            "dateOfBirth": "30-04",
        },
        {
            "email": "sophia@inara.com",
            "passwordHash": hp("Instructor123!"),
            "name": "Sophia Martinez",
            "role": "INSTRUCTOR",
            "phone": "+97444222777",
            "gender": "Female",
            "isActive": True,
            "isVerified": True,
            "speciality": "Vinyasa Flow",
            "bio": "Vinyasa Flow Specialist",
            "address": "https://images.unsplash.com/photo-1601925228008-8c5b6bc7c4e5?w=800",
            "avatar": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=200",
            "dateOfBirth": "19-08",
        },
        {
            "email": "james@inara.com",
            "passwordHash": hp("Instructor123!"),
            "name": "James Anderson",
            "role": "INSTRUCTOR",
            "phone": "+97444222888",
            "gender": "Male",
            "isActive": True,
            "isVerified": True,
            "speciality": "Calisthenics & Bodyweight Strength",
            "bio": "Calisthenics & Bodyweight Strength Coach",
            "address": "https://images.unsplash.com/photo-1517838277536-f5f99be501cd?w=800",
            "avatar": "https://images.unsplash.com/photo-1552058544-f2b08422138a?w=200",
            "dateOfBirth": "12-02",
        },
        # ── Regular Users ─────────────────────────────────────────────────────
        {
            "email": "marvin@test.com",
            "passwordHash": hp("User123!"),
            "name": "Marvin McKinney",
            "role": "USER",
            "phone": "+97444333111",
            "gender": "Male",
            "isActive": True,
            "isVerified": True,
            "address": "Doha, Qatar",
            "dateOfBirth": "12-05",          # ✅ DD-MM string — not a datetime object
            "lastLoginAt": past(days=1),
        },
        {
            "email": "eleanor@test.com",
            "passwordHash": hp("User123!"),
            "name": "Eleanor Pena",
            "role": "USER",
            "phone": "+97444333222",
            "gender": "Female",
            "isActive": True,
            "isVerified": True,
            "address": "Al Wakrah, Qatar",
            "dateOfBirth": "22-08",
            "lastLoginAt": past(hours=5),
        },
        {
            "email": "jacob@test.com",
            "passwordHash": hp("User123!"),
            "name": "Jacob Jones",
            "role": "USER",
            "phone": "+97444333333",
            "gender": "Male",
            "isActive": True,
            "isVerified": True,
            "address": "Education City, Qatar",
            "dateOfBirth": "05-11",
            "lastLoginAt": past(days=2),
        },
    ]

    users = {}
    for u in users_raw:
        user = await prisma.user.create(data=u)
        users[user.email] = user
        print(f"  ✅ {user.name} ({user.role})")

    print(f"✨ {len(users)} users created\n")
    return users


# ============================================
# COURSES  —  standalone, all upcoming
# ============================================

async def seed_courses(users: dict) -> dict:
    print("📚 Seeding courses (standalone, all upcoming)...")

    i1 = users["savannah@inara.com"]
    i2 = users["jane@inara.com"]
    i3 = users["mike@inara.com"]

    courses_raw = [
        {
            "title": "Complete Yoga Mastery",
            "description": "Master yoga from beginner to advanced level. Covers Hatha, Vinyasa, and Restorative styles.",
            "imageUrl": "https://images.unsplash.com/photo-1545389336-cf090694435e?w=800",
            "status": "SCHEDULED",
            "price": 970.0,
            "isFree": False,
            "scheduledAt": future(days=7),
            "duration": "8:00 AM to 10:00 AM",
            "difficulty": "Beginner",
            "gender": "Both",
            "location": "INARA Studio A, West Bay, Doha",
            "locationMapLink": "https://maps.google.com/?q=West+Bay+Doha",
            "latitude": 25.3548,
            "longitude": 51.5310,
            "phone": "+97444123456",
            "maxParticipants": 100,
            "availableSeat": 85,
            "instructorId": i1.id,
            "order": 1,
        },
        {
            "title": "Advanced Pilates Training",
            "description": "Professional Pilates training for core strength, flexibility, and full body conditioning.",
            "imageUrl": "https://images.unsplash.com/photo-1518611012118-696072aa579a?w=800",
            "status": "ONGOING",
            "price": 1200.0,
            "isFree": False,
            "scheduledAt": future(days=14),
            "duration": "6:00 PM to 8:00 PM",
            "difficulty": "Advanced",
            "gender": "Female",
            "location": "INARA Studio B, The Pearl, Doha",
            "locationMapLink": "https://maps.google.com/?q=The+Pearl+Doha",
            "latitude": 25.3716,
            "longitude": 51.5513,
            "phone": "+97444123457",
            "maxParticipants": 50,
            "availableSeat": 45,
            "instructorId": i2.id,
            "order": 2,
        },
        {
            "title": "Strength & Conditioning",
            "description": "Intensive functional strength training. Build power, endurance, and resilience.",
            "imageUrl": "https://images.unsplash.com/photo-1526506118085-60ce8714f8c5?w=800",
            "status": "SCHEDULED",
            "price": 850.0,
            "isFree": False,
            "scheduledAt": future(days=10),
            "duration": "5:00 AM to 7:00 AM",
            "difficulty": "Intermediate",
            "gender": "Male",
            "location": "INARA Outdoor Arena, Lusail",
            "locationMapLink": "https://maps.google.com/?q=Lusail+Qatar",
            "latitude": 25.4267,
            "longitude": 51.4940,
            "phone": "+97444123458",
            "maxParticipants": 30,
            "availableSeat": 22,
            "instructorId": i3.id,
            "order": 3,
        },
        {
            "title": "Women's Wellness & Mindfulness",
            "description": "Holistic wellness program combining gentle yoga, meditation, and mindfulness.",
            "imageUrl": "https://images.unsplash.com/photo-1506126613408-eca07ce68773?w=800",
            "status": "COMPLETED",
            "price": 0.0,
            "isFree": True,
            "scheduledAt": past(days=5),
            "duration": "9:00 AM to 11:00 AM",
            "difficulty": "Beginner",
            "gender": "Female",
            "location": "INARA Wellness Center, Education City",
            "locationMapLink": "https://maps.google.com/?q=Education+City+Doha",
            "latitude": 25.3157,
            "longitude": 51.4240,
            "phone": "+97444123459",
            "maxParticipants": 25,
            "availableSeat": 0,
            "instructorId": i2.id,
            "order": 4,
        },
        {
            "title": "Functional Fitness Bootcamp",
            "description": "A cancelled bootcamp — preserved for historical data.",
            "imageUrl": "https://images.unsplash.com/photo-1517836357463-d25dfeac3438?w=800",
            "status": "CANCELLED",
            "price": 500.0,
            "isFree": False,
            "scheduledAt": past(days=10),
            "duration": "7:00 AM to 9:00 AM",
            "difficulty": "Advanced",
            "gender": "Both",
            "location": "INARA Outdoor Arena, Lusail",
            "phone": "+97444123458",
            "maxParticipants": 20,
            "availableSeat": 0,
            "cancelledAt": past(days=12),
            "cancellationReason": "Instructor unavailable due to travel",
            "instructorId": i3.id,
            "order": 5,
        },
    ]

    courses = {}
    for c in courses_raw:
        course = await prisma.course.create(data=c)
        courses[course.title] = course
        print(f"  ✅ {course.title} ({course.difficulty}) [{course.status}] — QAR {course.price}")

    print(f"✨ {len(courses)} courses created\n")
    return courses


# ============================================
# CLASSES  —  standalone, all statuses represented
# ============================================

async def seed_classes(users: dict) -> list:
    print("🏃 Seeding classes (standalone, all statuses covered)...")

    i1 = users["savannah@inara.com"]
    i2 = users["jane@inara.com"]
    i3 = users["mike@inara.com"]
    i4 = users["courtney@inara.com"]
    i5 = users["emma@inara.com"]
    i6 = users["david@inara.com"]
    i7 = users["sophia@inara.com"]
    i8 = users["james@inara.com"]

    classes_raw = [
        # ── SCHEDULED ──────────────────────────────────────────────────────────
        {
            "title": "Morning Vinyasa Flow",
            "description": "Energising morning flow. Sun salutations, standing poses, and cool-down.",
            "status": "SCHEDULED",
            "duration": "6:00 AM to 7:00 AM",
            "scheduledAt": future_at(days=1, hour=6),
            "maxParticipants": 20, "availableSeat": 20,
            "price": 50.0, "isFree": False,
            "difficulty": "Beginner", "gender": "Both",
            "location": "INARA Studio A, West Bay",
            "locationMapLink": "https://maps.google.com/?q=West+Bay+Doha",
            "latitude": 25.3548, "longitude": 51.5310,
            "phone": "+97444123456",
            "imageUrl": "https://images.unsplash.com/photo-1545389336-cf090694435e?w=400",
            "order": 1, "instructorId": i1.id,
        },
        {
            "title": "Inner Peace Yoga",
            "description": "Gentle yoga focused on breath and alignment for inner calm.",
            "status": "SCHEDULED",
            "duration": "9:00 AM to 10:15 AM",
            "scheduledAt": future_at(days=2, hour=9),
            "maxParticipants": 20, "availableSeat": 20,
            "price": 55.0, "isFree": False,
            "difficulty": "Beginner", "gender": "Both",
            "location": "INARA Studio A, West Bay",
            "locationMapLink": "https://maps.google.com/?q=West+Bay+Doha",
            "latitude": 25.3548, "longitude": 51.5310,
            "phone": "+97444123456",
            "imageUrl": "https://images.unsplash.com/photo-1506126613408-eca07ce68773?w=400",
            "order": 2, "instructorId": i1.id,
        },
        {
            "title": "Harmony Yoga",
            "description": "Intermediate poses with focus on balance and flexibility.",
            "status": "SCHEDULED",
            "duration": "8:00 AM to 9:30 AM",
            "scheduledAt": future_at(days=3, hour=8),
            "maxParticipants": 15, "availableSeat": 15,
            "price": 65.0, "isFree": False,
            "difficulty": "Intermediate", "gender": "Both",
            "location": "INARA Studio A, West Bay",
            "phone": "+97444123456",
            "imageUrl": "https://images.unsplash.com/photo-1545389336-cf090694435e?w=400",
            "order": 3, "instructorId": i1.id,
        },
        {
            "title": "Evening Pilates",
            "description": "Core-focused Pilates using mat and resistance bands.",
            "status": "SCHEDULED",
            "duration": "6:00 PM to 7:00 PM",
            "scheduledAt": future_at(days=2, hour=18),
            "maxParticipants": 15, "availableSeat": 15,
            "price": 75.0, "isFree": False,
            "difficulty": "Advanced", "gender": "Female",
            "location": "INARA Studio B, The Pearl",
            "locationMapLink": "https://maps.google.com/?q=The+Pearl+Doha",
            "latitude": 25.3716, "longitude": 51.5513,
            "phone": "+97444123457",
            "imageUrl": "https://images.unsplash.com/photo-1518611012118-696072aa579a?w=400",
            "order": 1, "instructorId": i2.id,
        },
        {
            "title": "Mind & Body Balance",
            "description": "Deep stretch and core activation session.",
            "status": "SCHEDULED",
            "duration": "5:00 PM to 6:00 PM",
            "scheduledAt": future_at(days=4, hour=17),
            "maxParticipants": 15, "availableSeat": 15,
            "price": 75.0, "isFree": False,
            "difficulty": "Advanced", "gender": "Female",
            "location": "INARA Studio B, The Pearl",
            "phone": "+97444123457",
            "imageUrl": "https://images.unsplash.com/photo-1518611012118-696072aa579a?w=400",
            "order": 2, "instructorId": i2.id,
        },
        {
            "title": "Power Strength Training",
            "description": "Full body compound movements for maximum strength gains.",
            "status": "SCHEDULED",
            "duration": "5:00 AM to 6:00 AM",
            "scheduledAt": future_at(days=2, hour=5),
            "maxParticipants": 12, "availableSeat": 12,
            "price": 100.0, "isFree": False,
            "difficulty": "Advanced", "gender": "Male",
            "location": "INARA Outdoor Arena, Lusail",
            "locationMapLink": "https://maps.google.com/?q=Lusail+Qatar",
            "latitude": 25.4267, "longitude": 51.4940,
            "phone": "+97444123458",
            "imageUrl": "https://images.unsplash.com/photo-1526506118085-60ce8714f8c5?w=400",
            "order": 1, "instructorId": i3.id,
        },
        {
            "title": "Pure Balance Yoga",
            "description": "Restorative yoga to balance mind, body, and soul.",
            "status": "SCHEDULED",
            "duration": "9:00 AM to 10:30 AM",
            "scheduledAt": future_at(days=3, hour=9),
            "maxParticipants": 20, "availableSeat": 20,
            "price": 60.0, "isFree": False,
            "difficulty": "Beginner", "gender": "Female",
            "location": "INARA Wellness Center, Education City",
            "locationMapLink": "https://maps.google.com/?q=Education+City+Doha",
            "latitude": 25.3157, "longitude": 51.4240,
            "phone": "+97444123459",
            "imageUrl": "https://images.unsplash.com/photo-1506126613408-eca07ce68773?w=400",
            "order": 1, "instructorId": i2.id,
        },
        {
            "title": "Dawn Sun Salutation",
            "description": "Start your day with an energising sequence of sun salutations and breathwork.",
            "status": "SCHEDULED",
            "duration": "6:00 AM to 7:00 AM",
            "scheduledAt": future_at(days=5, hour=6),
            "maxParticipants": 20, "availableSeat": 20,
            "price": 55.0, "isFree": False,
            "difficulty": "Beginner", "gender": "Both",
            "location": "INARA Studio A, West Bay",
            "phone": "+97444123456",
            "imageUrl": "https://images.unsplash.com/photo-1545389336-cf090694435e?w=400",
            "order": 10, "instructorId": i1.id,
        },
        {
            "title": "Core Power Pilates",
            "description": "Intense core-focused Pilates using resistance bands and mat work.",
            "status": "SCHEDULED",
            "duration": "7:00 AM to 8:00 AM",
            "scheduledAt": future_at(days=5, hour=7),
            "maxParticipants": 15, "availableSeat": 15,
            "price": 75.0, "isFree": False,
            "difficulty": "Advanced", "gender": "Female",
            "location": "INARA Studio B, The Pearl",
            "phone": "+97444123457",
            "imageUrl": "https://images.unsplash.com/photo-1518611012118-696072aa579a?w=400",
            "order": 10, "instructorId": i2.id,
        },
        {
            "title": "Hatha Morning Flow",
            "description": "Classic Hatha postures with deep breathing to ground and centre.",
            "status": "SCHEDULED",
            "duration": "8:00 AM to 9:00 AM",
            "scheduledAt": future_at(days=6, hour=8),
            "maxParticipants": 18, "availableSeat": 18,
            "price": 60.0, "isFree": False,
            "difficulty": "Beginner", "gender": "Both",
            "location": "INARA Wellness Center, Education City",
            "phone": "+97444123459",
            "imageUrl": "https://images.unsplash.com/photo-1517836357463-d25dfeac3438?w=400",
            "order": 10, "instructorId": i4.id,
        },
        {
            "title": "HIIT Ignite",
            "description": "High-intensity interval training to torch calories and build endurance.",
            "status": "SCHEDULED",
            "duration": "9:00 AM to 10:00 AM",
            "scheduledAt": future_at(days=6, hour=9),
            "maxParticipants": 20, "availableSeat": 20,
            "price": 90.0, "isFree": False,
            "difficulty": "Advanced", "gender": "Both",
            "location": "INARA Outdoor Arena, Lusail",
            "phone": "+97444123458",
            "imageUrl": "https://images.unsplash.com/photo-1526506118085-60ce8714f8c5?w=400",
            "order": 10, "instructorId": i3.id,
        },
        {
            "title": "Guided Meditation & Breathwork",
            "description": "A calming session using pranayama and mindfulness techniques.",
            "status": "SCHEDULED",
            "duration": "10:00 AM to 11:00 AM",
            "scheduledAt": future_at(days=7, hour=10),
            "maxParticipants": 25, "availableSeat": 25,
            "price": 50.0, "isFree": False,
            "difficulty": "Beginner", "gender": "Both",
            "location": "INARA Wellness Center, Education City",
            "phone": "+97444123459",
            "imageUrl": "https://images.unsplash.com/photo-1540206395-68808572332f?w=400",
            "order": 11, "instructorId": i5.id,
        },
        {
            "title": "Functional Strength Circuit",
            "description": "Full-body functional movements: kettlebells, pull-ups, and plyometrics.",
            "status": "SCHEDULED",
            "duration": "11:00 AM to 12:00 PM",
            "scheduledAt": future_at(days=7, hour=11),
            "maxParticipants": 12, "availableSeat": 12,
            "price": 100.0, "isFree": False,
            "difficulty": "Intermediate", "gender": "Male",
            "location": "INARA Outdoor Arena, Lusail",
            "phone": "+97444123458",
            "imageUrl": "https://images.unsplash.com/photo-1534438327276-14e5300c3a48?w=400",
            "order": 11, "instructorId": i6.id,
        },
        {
            "title": "Vinyasa Flow & Restore",
            "description": "Dynamic Vinyasa flow transitioning into a restorative cool-down.",
            "status": "SCHEDULED",
            "duration": "1:00 PM to 2:30 PM",
            "scheduledAt": future_at(days=8, hour=13),
            "maxParticipants": 18, "availableSeat": 18,
            "price": 65.0, "isFree": False,
            "difficulty": "Intermediate", "gender": "Both",
            "location": "INARA Studio A, West Bay",
            "phone": "+97444123456",
            "imageUrl": "https://images.unsplash.com/photo-1571019614242-c5c5dee9f50b?w=400",
            "order": 11, "instructorId": i7.id,
        },
        {
            "title": "Calisthenics Foundations",
            "description": "Master bodyweight basics: push-ups, dips, L-sits, and handstand prep.",
            "status": "SCHEDULED",
            "duration": "3:00 PM to 4:00 PM",
            "scheduledAt": future_at(days=9, hour=15),
            "maxParticipants": 15, "availableSeat": 15,
            "price": 80.0, "isFree": False,
            "difficulty": "Intermediate", "gender": "Both",
            "location": "INARA Outdoor Arena, Lusail",
            "phone": "+97444123458",
            "imageUrl": "https://images.unsplash.com/photo-1521572267360-ee0c2909d518?w=400",
            "order": 12, "instructorId": i8.id,
        },
        {
            "title": "Women's Restorative Yoga",
            "description": "Gentle supported poses with props designed for deep relaxation and recovery.",
            "status": "SCHEDULED",
            "duration": "4:00 PM to 5:30 PM",
            "scheduledAt": future_at(days=10, hour=16),
            "maxParticipants": 20, "availableSeat": 20,
            "price": 60.0, "isFree": False,
            "difficulty": "Beginner", "gender": "Female",
            "location": "INARA Wellness Center, Education City",
            "phone": "+97444123459",
            "imageUrl": "https://images.unsplash.com/photo-1506126613408-eca07ce68773?w=400",
            "order": 12, "instructorId": i4.id,
        },
        {
            "title": "Evening Stretch & Unwind",
            "description": "Full-body flexibility routine to release tension and prepare for restful sleep.",
            "status": "SCHEDULED",
            "duration": "7:00 PM to 8:00 PM",
            "scheduledAt": future_at(days=11, hour=19),
            "maxParticipants": 22, "availableSeat": 22,
            "price": 55.0, "isFree": False,
            "difficulty": "Beginner", "gender": "Both",
            "location": "INARA Studio B, The Pearl",
            "phone": "+97444123457",
            "imageUrl": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=400",
            "order": 12, "instructorId": i5.id,
        },
        # ── ONGOING ────────────────────────────────────────────────────────────
        {
            "title": "Live Yoga Session",
            "description": "An ongoing live class in progress right now.",
            "status": "ONGOING",
            "duration": "8:00 AM to 9:00 AM",
            "scheduledAt": past(hours=1),
            "maxParticipants": 15, "availableSeat": 5,
            "price": 60.0, "isFree": False,
            "difficulty": "Intermediate", "gender": "Both",
            "location": "INARA Studio A, West Bay",
            "phone": "+97444123456",
            "imageUrl": "https://images.unsplash.com/photo-1545389336-cf090694435e?w=400",
            "order": 0, "instructorId": i1.id,
        },
        # ── COMPLETED ──────────────────────────────────────────────────────────
        {
            "title": "Sunday Stretch Session",
            "description": "Full-body stretch class — completed last week.",
            "status": "COMPLETED",
            "duration": "10:00 AM to 11:00 AM",
            "scheduledAt": past(days=7),
            "maxParticipants": 20, "availableSeat": 0,
            "price": 50.0, "isFree": False,
            "difficulty": "Beginner", "gender": "Both",
            "location": "INARA Wellness Center, Education City",
            "phone": "+97444123459",
            "imageUrl": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=400",
            "order": 0, "instructorId": i5.id,
        },
        # ── CANCELLED ──────────────────────────────────────────────────────────
        {
            "title": "Advanced HIIT — Cancelled",
            "description": "This class was cancelled due to low enrollment.",
            "status": "CANCELLED",
            "duration": "6:00 AM to 7:00 AM",
            "scheduledAt": past(days=3),
            "maxParticipants": 10, "availableSeat": 10,
            "price": 90.0, "isFree": False,
            "difficulty": "Advanced", "gender": "Both",
            "location": "INARA Outdoor Arena, Lusail",
            "phone": "+97444123458",
            "imageUrl": "https://images.unsplash.com/photo-1526506118085-60ce8714f8c5?w=400",
            "cancelledAt": past(days=4),
            "cancellationReason": "Insufficient enrollment — class minimum not met",
            "order": 0, "instructorId": i3.id,
        },
    ]

    classes = []
    for c in classes_raw:
        cls = await prisma.classes.create(data=c)
        classes.append(cls)
        print(f"  ✅ {cls.title} [{cls.status}] — {cls.scheduledAt.strftime('%d %b %Y %I:%M %p')} — QAR {cls.price}")

    print(f"✨ {len(classes)} classes created\n")
    return classes


# ============================================
# PACKAGES
# ============================================

async def seed_packages(classes: list, courses: dict) -> dict:
    print("📦 Seeding packages...")

    all_class_ids  = [c.id for c in classes]
    all_course_ids = [c.id for c in courses.values()]
    morning_ids    = [c.id for c in classes if "Morning" in c.title or "Dawn" in c.title or "Inner" in c.title]
    beginner_ids   = [c.id for c in classes if c.difficulty == "Beginner"]

    packages_raw = [
        {
            "name": "1-Month Pack",
            "description": "Access to regular classes for 30 days — package deal",
            "price": 970.0, "discountPrice": None,
            "durationDays": 30, "allowedClasses": all_class_ids[:5], "allowedCourses": [],
            "timeRestriction": "Before 3:00 PM",
            "isActive": True, "autoRenew": False, "order": 1,
        },
        {
            "name": "3-Month Pack",
            "description": "Full access to all classes and courses for 90 days — package deal",
            "price": 2750.0, "discountPrice": 2500.0,
            "durationDays": 90, "allowedClasses": all_class_ids, "allowedCourses": all_course_ids,
            "timeRestriction": None,
            "isActive": True, "autoRenew": True, "order": 2,
        },
        {
            "name": "Morning Package",
            "description": "Morning classes only — perfect for early birds",
            "price": 1100.0, "discountPrice": 980.0,
            "durationDays": 30,
            "allowedClasses": morning_ids if morning_ids else all_class_ids[:3],
            "allowedCourses": [],
            "timeRestriction": "Before 12:00 PM",
            "isActive": True, "autoRenew": False, "order": 3,
        },
        {
            "name": "Annual VIP Pack",
            "description": "Full year unlimited access with priority booking — package deal",
            "price": 9800.0, "discountPrice": 8500.0,
            "durationDays": 365, "allowedClasses": all_class_ids, "allowedCourses": all_course_ids,
            "timeRestriction": None,
            "isActive": True, "autoRenew": True, "order": 4,
        },
        {
            "name": "Trial Pack",
            "description": "Try INARA for 7 days — taste the experience",
            "price": 150.0, "discountPrice": None,
            "durationDays": 7,
            "allowedClasses": beginner_ids if beginner_ids else all_class_ids[:2],
            "allowedCourses": [],
            "timeRestriction": "Before 12:00 PM",
            "isActive": True, "autoRenew": False, "order": 5,
        },
        {
            "name": "10 Class Pack",
            "description": "Attend 10 classes — valid for 3 months. No refund.",
            "price": 750.0, "discountPrice": None,
            "durationDays": 90, "allowedClasses": all_class_ids, "allowedCourses": [],
            "timeRestriction": None,
            "isActive": True, "autoRenew": False, "order": 6,
        },
    ]

    packages = {}
    for p in packages_raw:
        pkg = await prisma.package.create(data=p)
        packages[pkg.name] = pkg
        print(f"  ✅ {pkg.name} — QAR {pkg.price} — {pkg.durationDays} days")

    print(f"✨ {len(packages)} packages created\n")
    return packages


# ============================================
# PAYMENT LOGS  (created before memberships/bookings/orders so IDs are available)
# ============================================

async def seed_payment_logs(users: dict, packages: dict) -> dict:
    print("💳 Seeding payment logs (all modules + all statuses)...")

    u1 = users["marvin@test.com"]
    u2 = users["eleanor@test.com"]
    u3 = users["jacob@test.com"]

    # Resolve package IDs for the PACKAGE-module payment logs.
    # The gatewayResponse blob must carry the correct package_id so that
    # history_service._build_package_history_item can resolve the package.
    pkg_1month = packages.get("1-Month Pack")
    pkg_trial  = packages.get("Trial Pack")

    logs_raw = [
        # ── WALLET top-ups ───────────────────────────────────────────────────
        {
            "key": "wallet_topup_marvin",
            "data": {
                "userId": u1.id,
                "module": "WALLET",
                "referenceId": "WALLET-MARVIN01",
                "idempotencyKey": idem_key(),
                "amount": 500.0,
                "currency": "QAR",
                "paymentMethod": "CREDIT_CARD",
                "status": "SUCCESS",
                "gatewayInvoiceId": "INV-MF-10001",
                "gatewayPaymentId": "PAY-MF-10001",
                "gatewaySessionId": "SES-MF-10001",
                "gatewayResponse": Json({"InvoiceStatus": "Paid", "PaymentGateway": "Visa"}),
            },
        },
        {
            "key": "wallet_topup_eleanor",
            "data": {
                "userId": u2.id,
                "module": "WALLET",
                "referenceId": "WALLET-ELEANOR01",
                "idempotencyKey": idem_key(),
                "amount": 1000.0,
                "currency": "QAR",
                "paymentMethod": "DEBIT_CARD",
                "status": "SUCCESS",
                "gatewayInvoiceId": "INV-MF-10002",
                "gatewayPaymentId": "PAY-MF-10002",
                "gatewaySessionId": "SES-MF-10002",
                "gatewayResponse": Json({"InvoiceStatus": "Paid", "PaymentGateway": "Mastercard"}),
            },
        },
        {
            "key": "wallet_topup_jacob",
            "data": {
                "userId": u3.id,
                "module": "WALLET",
                "referenceId": "WALLET-JACOB01",
                "idempotencyKey": idem_key(),
                "amount": 200.0,
                "currency": "QAR",
                "paymentMethod": "APPLE_PAY",
                "status": "SUCCESS",
                "gatewayInvoiceId": "INV-MF-10003",
                "gatewayPaymentId": "PAY-MF-10003",
                "gatewaySessionId": None,
                "gatewayResponse": Json({"InvoiceStatus": "Paid", "PaymentGateway": "ApplePay"}),
            },
        },
        # ── BOOKING payment ──────────────────────────────────────────────────
        {
            "key": "booking_pay_marvin",
            "data": {
                "userId": u1.id,
                "module": "BOOKING",
                "referenceId": "BOOKING-MARVIN01",
                "idempotencyKey": idem_key(),
                "amount": 50.0,
                "currency": "QAR",
                "paymentMethod": "WALLET",
                "status": "SUCCESS",
                "gatewayInvoiceId": None,
                "gatewayPaymentId": None,
                "gatewaySessionId": None,
            },
        },
        {
            "key": "booking_pay_eleanor",
            "data": {
                "userId": u2.id,
                "module": "BOOKING",
                "referenceId": "BOOKING-ELEANOR01",
                "idempotencyKey": idem_key(),
                "amount": 75.0,
                "currency": "QAR",
                "paymentMethod": "GATEWAY",
                "status": "SUCCESS",
                "gatewayInvoiceId": "INV-MF-20001",
                "gatewayPaymentId": "PAY-MF-20001",
                "gatewaySessionId": "SES-MF-20001",
                "gatewayResponse": Json({"InvoiceStatus": "Paid", "PaymentGateway": "GooglePay"}),
            },
        },
        {
            "key": "booking_pay_failed",
            "data": {
                "userId": u3.id,
                "module": "BOOKING",
                "referenceId": "BOOKING-JACOB-FAIL01",
                "idempotencyKey": idem_key(),
                "amount": 65.0,
                "currency": "QAR",
                "paymentMethod": "GATEWAY",
                "status": "FAILED",
                "gatewayInvoiceId": "INV-MF-20002",
                "gatewayPaymentId": None,
                "gatewaySessionId": "SES-MF-20002",
                "gatewayResponse": Json({"InvoiceStatus": "Unpaid"}),
                "errorMessage": "Payment declined by issuing bank",
            },
        },
        # ── MEMBERSHIP payment ───────────────────────────────────────────────
        {
            "key": "membership_marvin",
            "data": {
                "userId": u1.id,
                "module": "MEMBERSHIP",
                "referenceId": "MEMBERSHIP-MARVIN01",
                "idempotencyKey": idem_key(),
                "amount": 970.0,
                "currency": "QAR",
                "paymentMethod": "WALLET",
                "status": "SUCCESS",
                "gatewayInvoiceId": None,
                "gatewayPaymentId": None,
                "gatewaySessionId": None,
            },
        },
        {
            "key": "membership_eleanor",
            "data": {
                "userId": u2.id,
                "module": "MEMBERSHIP",
                "referenceId": "MEMBERSHIP-ELEANOR01",
                "idempotencyKey": idem_key(),
                "amount": 2500.0,
                "currency": "QAR",
                "paymentMethod": "CREDIT_CARD",
                "status": "SUCCESS",
                "gatewayInvoiceId": "INV-MF-30001",
                "gatewayPaymentId": "PAY-MF-30001",
                "gatewaySessionId": "SES-MF-30001",
                "gatewayResponse": Json({"InvoiceStatus": "Paid", "PaymentGateway": "Visa"}),
            },
        },
        # ── PACKAGE payment ──────────────────────────────────────────────────
        # NOTE: Packages and Memberships are independent domains.
        # Package names in the seed use distinct names (e.g. "1-Month Pack",
        # "3-Month Pack") so they never collide with Membership plan template
        # names (e.g. "1 Month Membership", "3 Month Membership"). This
        # prevents false-positive 409 responses in _assert_no_active_package_membership.
        {
            "key": "package_marvin",
            "data": {
                "userId": u1.id,
                "module": "PACKAGE",
                "referenceId": "PACKAGE-MARVIN01",
                "idempotencyKey": idem_key(),
                "amount": 970.0,
                "currency": "QAR",
                "paymentMethod": "WALLET",
                "status": "SUCCESS",
                "gatewayInvoiceId": None,
                "gatewayPaymentId": None,
                "gatewaySessionId": None,
                # Embed the real package_id so package history can resolve it.
                "gatewayResponse": Json({
                    "package_id": pkg_1month.id if pkg_1month else None,
                    "auto_renew": False,
                }),
            },
        },
        {
            "key": "package_jacob",
            "data": {
                "userId": u3.id,
                "module": "PACKAGE",
                "referenceId": "PACKAGE-JACOB01",
                "idempotencyKey": idem_key(),
                "amount": 150.0,
                "currency": "QAR",
                "paymentMethod": "GOOGLE_PAY",
                "status": "SUCCESS",
                "gatewayInvoiceId": "INV-MF-40001",
                "gatewayPaymentId": "PAY-MF-40001",
                "gatewaySessionId": "SES-MF-40001",
                # Embed the real package_id so package history can resolve it.
                "gatewayResponse": Json({
                    "package_id": pkg_trial.id if pkg_trial else None,
                    "auto_renew": False,
                    "InvoiceStatus": "Paid",
                    "PaymentGateway": "GooglePay",
                }),
            },
        },
        # ── ORDER payment ────────────────────────────────────────────────────
        {
            "key": "order_eleanor_paid",
            "data": {
                "userId": u2.id,
                "module": "ORDER",
                "referenceId": "ORDER-ELEANOR01",
                "idempotencyKey": idem_key(),
                "amount": 345.0,
                "currency": "QAR",
                "paymentMethod": "CREDIT_CARD",
                "status": "SUCCESS",
                "gatewayInvoiceId": "INV-MF-50001",
                "gatewayPaymentId": "PAY-MF-50001",
                "gatewaySessionId": "SES-MF-50001",
                "gatewayResponse": Json({"InvoiceStatus": "Paid", "PaymentGateway": "Visa"}),
            },
        },
        {
            "key": "order_marvin_wallet",
            "data": {
                "userId": u1.id,
                "module": "ORDER",
                "referenceId": "ORDER-MARVIN01",
                "idempotencyKey": idem_key(),
                "amount": 199.0,
                "currency": "QAR",
                "paymentMethod": "WALLET",
                "status": "SUCCESS",
                "gatewayInvoiceId": None,
                "gatewayPaymentId": None,
                "gatewaySessionId": None,
            },
        },
        # ── REFUNDED log ─────────────────────────────────────────────────────
        {
            "key": "booking_refund_marvin",
            "data": {
                "userId": u1.id,
                "module": "BOOKING",
                "referenceId": "BOOKING-MARVIN-REF01",
                "idempotencyKey": idem_key(),
                "amount": 55.0,
                "currency": "QAR",
                "paymentMethod": "WALLET",
                "status": "REFUNDED",
                "gatewayInvoiceId": None,
                "gatewayPaymentId": None,
                "gatewaySessionId": None,
                "refundAmount": 55.0,
                "refundedAt": past(days=2),
            },
        },
        # ── CANCELLED log ────────────────────────────────────────────────────
        {
            "key": "order_cancelled",
            "data": {
                "userId": u3.id,
                "module": "ORDER",
                "referenceId": "ORDER-JACOB-CANCEL01",
                "idempotencyKey": idem_key(),
                "amount": 85.0,
                "currency": "QAR",
                "paymentMethod": "GATEWAY",
                "status": "CANCELLED",
                "gatewayInvoiceId": "INV-MF-50002",
                "gatewayPaymentId": None,
                "gatewaySessionId": "SES-MF-50002",
                "gatewayResponse": Json({"InvoiceStatus": "Cancelled"}),
            },
        },
        # ── INITIATED / PENDING (in-flight) ──────────────────────────────────
        {
            "key": "wallet_pending_jacob",
            "data": {
                "userId": u3.id,
                "module": "WALLET",
                "referenceId": "WALLET-JACOB-PENDING01",
                "idempotencyKey": idem_key(),
                "amount": 300.0,
                "currency": "QAR",
                "paymentMethod": "GATEWAY",
                "status": "PENDING",
                "gatewayInvoiceId": "INV-MF-60001",
                "gatewayPaymentId": None,
                "gatewaySessionId": "SES-MF-60001",
            },
        },
    ]

    logs = {}
    for entry in logs_raw:
        log = await prisma.paymentlog.create(data=entry["data"])
        logs[entry["key"]] = log
        print(f"  ✅ [{log.module}] {log.referenceId} — {log.status} — QAR {log.amount}")

    print(f"✨ {len(logs)} payment logs created\n")
    return logs


# ============================================
# WALLETS + WALLET TRANSACTIONS
# ============================================

async def seed_wallets(users: dict, payment_logs: dict) -> dict:
    print("👛 Seeding wallets and transactions...")

    u1 = users["marvin@test.com"]
    u2 = users["eleanor@test.com"]
    u3 = users["jacob@test.com"]

    # Each user wallet — balance reflects seed history
    wallet_configs = [
        # user, starting balance after top-ups minus debits
        (u1, 500.0 - 50.0 - 199.0 - 55.0,  # topup 500 - booking 50 - order 199 - refund restored +55 = 256
         payment_logs["wallet_topup_marvin"]),
        (u2, 1000.0 - 75.0 - 345.0,         # topup 1000 - booking 75 - order 345 = 580
         payment_logs["wallet_topup_eleanor"]),
        (u3, 200.0,                           # topup 200, no wallet debits yet
         payment_logs["wallet_topup_jacob"]),
    ]

    wallets = {}
    for user, balance, _ in wallet_configs:
        wallet = await prisma.wallet.create(data={
            "userId": user.id,
            "balance": round(max(balance, 0), 3),
            "currency": "QAR",
        })
        wallets[user.email] = wallet
        print(f"  ✅ Wallet for {user.name} — QAR {wallet.balance:.2f}")

    # ── Wallet Transactions ────────────────────────────────────────────────────
    txns = [
        # Marvin: +500 deposit, -50 booking, -199 order, +55 refund credited back
        {
            "walletId": wallets[u1.email].id,
            "type": "DEPOSIT",
            "amount": 500.0,
            "balanceBefore": 0.0,
            "balanceAfter": 500.0,
            "description": "Wallet top-up via credit card",
            "referenceId": "WALLET-MARVIN01",
            "paymentLogId": payment_logs["wallet_topup_marvin"].id,
        },
        {
            "walletId": wallets[u1.email].id,
            "type": "DEBIT",
            "amount": 50.0,
            "balanceBefore": 500.0,
            "balanceAfter": 450.0,
            "description": "Class booking: Morning Vinyasa Flow",
            "referenceId": "BOOKING-MARVIN01",
            "paymentLogId": payment_logs["booking_pay_marvin"].id,
        },
        {
            "walletId": wallets[u1.email].id,
            "type": "DEBIT",
            "amount": 199.0,
            "balanceBefore": 450.0,
            "balanceAfter": 251.0,
            "description": "Store order ORD-002",
            "referenceId": "ORDER-MARVIN01",
            "paymentLogId": payment_logs["order_marvin_wallet"].id,
        },
        {
            "walletId": wallets[u1.email].id,
            "type": "DEPOSIT",
            "amount": 55.0,
            "balanceBefore": 251.0,
            "balanceAfter": 306.0,
            "description": "Refund: cancelled booking — more than 3h before class",
            "referenceId": "BOOKING-MARVIN-REF01",
            "paymentLogId": payment_logs["booking_refund_marvin"].id,
        },
        # Eleanor: +1000 deposit, -75 booking, -345 order
        {
            "walletId": wallets[u2.email].id,
            "type": "DEPOSIT",
            "amount": 1000.0,
            "balanceBefore": 0.0,
            "balanceAfter": 1000.0,
            "description": "Wallet top-up via debit card",
            "referenceId": "WALLET-ELEANOR01",
            "paymentLogId": payment_logs["wallet_topup_eleanor"].id,
        },
        {
            "walletId": wallets[u2.email].id,
            "type": "DEBIT",
            "amount": 75.0,
            "balanceBefore": 1000.0,
            "balanceAfter": 925.0,
            "description": "Class booking: Evening Pilates",
            "referenceId": "BOOKING-ELEANOR01",
            "paymentLogId": payment_logs["booking_pay_eleanor"].id,
        },
        {
            "walletId": wallets[u2.email].id,
            "type": "DEBIT",
            "amount": 345.0,
            "balanceBefore": 925.0,
            "balanceAfter": 580.0,
            "description": "Store order ORD-001",
            "referenceId": "ORDER-ELEANOR01",
            "paymentLogId": payment_logs["order_eleanor_paid"].id,
        },
        # Jacob: +200 deposit only
        {
            "walletId": wallets[u3.email].id,
            "type": "DEPOSIT",
            "amount": 200.0,
            "balanceBefore": 0.0,
            "balanceAfter": 200.0,
            "description": "Wallet top-up via Apple Pay",
            "referenceId": "WALLET-JACOB01",
            "paymentLogId": payment_logs["wallet_topup_jacob"].id,
        },
    ]

    for t in txns:
        await prisma.wallettransaction.create(data=t)

    print(f"  ✅ {len(txns)} wallet transactions recorded")
    print(f"✨ {len(wallets)} wallets created\n")
    return wallets


# ============================================
# MEMBERSHIPS
# ============================================


async def seed_membership_plans(admin_user) -> list:
    """
    Seed admin-owned Membership PLAN TEMPLATES (the catalogue).

    DESIGN NOTE — Schema-Compatible Catalogue:
    Prisma's Membership.userId is NON-NULLABLE. Filtering on `userId IS NULL`
    to identify "templates" is architecturally impossible — Prisma throws
    MissingRequiredValueError. The correct, zero-migration solution is to own
    plan templates under the ADMIN user. get_membership_catalogue then filters
    by `userId IN [admin_ids]`, which is a native Prisma-supported query.

    These rows represent the PUBLIC CATALOGUE that any authenticated user can
    browse via GET /memberships/catalogue and purchase via POST /memberships/purchase.
    They are NOT user memberships — they are admin-created plan definitions.
    """
    print("📋 Seeding membership plan templates (admin-owned catalogue)...")

    plans_raw = [
        {
            "userId":          admin_user.id,
            "name":            "Trial Week",
            "description":     "Experience INARA for 7 days — perfect for first-timers.",
            "price":           150.0,
            "durationDays":    7,
            "allowedClasses":  [],
            "allowedCourses":  [],
            "timeRestriction": "Before 12:00 PM",
            "status":          "ACTIVE",
            "progress":        0.0,
            "startDate":       datetime.now(),
            "endDate":         None,
            "autoRenew":       False,
            "isPaid":          False,
        },
        {
            "userId":          admin_user.id,
            "name":            "1 Month Membership",
            "description":     "Full access to regular classes for 30 days.",
            "price":           970.0,
            "durationDays":    30,
            "allowedClasses":  [],
            "allowedCourses":  [],
            "timeRestriction": "Before 3:00 PM",
            "status":          "ACTIVE",
            "progress":        0.0,
            "startDate":       datetime.now(),
            "endDate":         None,
            "autoRenew":       False,
            "isPaid":          False,
        },
        {
            "userId":          admin_user.id,
            "name":            "3 Month Membership",
            "description":     "Full access to all classes and courses for 90 days.",
            "price":           2500.0,
            "durationDays":    90,
            "allowedClasses":  [],
            "allowedCourses":  [],
            "timeRestriction": None,
            "status":          "ACTIVE",
            "progress":        0.0,
            "startDate":       datetime.now(),
            "endDate":         None,
            "autoRenew":       True,
            "isPaid":          False,
        },
        {
            "userId":          admin_user.id,
            "name":            "Morning Plan",
            "description":     "Morning classes only — ideal for early birds. Valid 30 days.",
            "price":           980.0,
            "durationDays":    30,
            "allowedClasses":  [],
            "allowedCourses":  [],
            "timeRestriction": "Before 12:00 PM",
            "status":          "ACTIVE",
            "progress":        0.0,
            "startDate":       datetime.now(),
            "endDate":         None,
            "autoRenew":       False,
            "isPaid":          False,
        },
        {
            "userId":          admin_user.id,
            "name":            "Annual VIP",
            "description":     "Full-year unlimited access with priority booking — best value.",
            "price":           8500.0,
            "durationDays":    365,
            "allowedClasses":  [],
            "allowedCourses":  [],
            "timeRestriction": None,
            "status":          "ACTIVE",
            "progress":        0.0,
            "startDate":       datetime.now(),
            "endDate":         None,
            "autoRenew":       True,
            "isPaid":          False,
        },
    ]

    plans = []
    for p in plans_raw:
        plan = await prisma.membership.create(data=p)
        plans.append(plan)
        print(f"  ✅ [PLAN TEMPLATE] {plan.name} — QAR {plan.price} — {plan.durationDays} days")

    print(f"✨ {len(plans)} membership plan templates created\n")
    return plans


async def seed_memberships(users: dict, classes: list, courses: dict, payment_logs: dict) -> list:
    """
    Seed user-purchased Membership records (active subscriptions).

    These are USER-OWNED rows — distinct from admin-owned plan templates above.
    get_membership_catalogue will NEVER return these because they belong to
    USER-role accounts, not ADMIN-role accounts.
    """
    print("🎫 Seeding memberships (with payment tracking)...")

    u1 = users["marvin@test.com"]
    u2 = users["eleanor@test.com"]
    u3 = users["jacob@test.com"]
    all_class_ids  = [c.id for c in classes]
    all_course_ids = [c.id for c in courses.values()]
    beginner_ids   = [c.id for c in classes if c.difficulty == "Beginner"]

    memberships_raw = [
        # ── Marvin: ACTIVE membership — purchased via Membership catalogue ─────
        # paymentLogId → module=MEMBERSHIP (NOT PACKAGE).
        # Name matches the admin plan template "1 Month Membership".
        # The duplicate-guard in _assert_no_active_package_membership will NOT
        # fire when Marvin buys the "1-Month Pack" package because that check
        # only looks at paymentLogIds with module=PACKAGE.
        {
            "userId": u1.id,
            "name": "1 Month Membership",
            "description": "Access to regular classes for 30 days",
            "price": 970.0,
            "durationDays": 30,
            "allowedClasses": all_class_ids[:5],
            "allowedCourses": [],
            "timeRestriction": "Before 3:00 PM",
            "status": "ACTIVE",
            "progress": 33.0,
            "startDate": past(days=10),
            "endDate": future(days=20),
            "enrolledAt": past(days=10),
            "autoRenew": False,
            "paymentMethod": "WALLET",
            "paymentLogId": payment_logs["membership_marvin"].id,
            "isPaid": True,
        },
        # ── Marvin: ACTIVE 1-Month Pack — purchased via Package route (WALLET) ─
        # paymentLogId → module=PACKAGE. Name matches the Package catalogue
        # entry "1-Month Pack" — deliberately different from the membership
        # above to prevent cross-domain name collisions.
        # get_my_packages sees this via paymentLogId.module == "PACKAGE".
        # The duplicate-guard WILL fire if Marvin tries to re-buy "1-Month Pack".
        {
            "userId": u1.id,
            "name": "1-Month Pack",
            "description": "Access to regular classes for 30 days — package deal",
            "price": 970.0,
            "durationDays": 30,
            "allowedClasses": all_class_ids[:5],
            "allowedCourses": [],
            "timeRestriction": "Before 3:00 PM",
            "status": "ACTIVE",
            "progress": 33.0,
            "startDate": past(days=10),
            "endDate": future(days=20),
            "enrolledAt": past(days=10),
            "autoRenew": False,
            "paymentMethod": "WALLET",
            "paymentLogId": payment_logs["package_marvin"].id,
            "isPaid": True,
        },
        # ── Marvin: EXPIRED trial (purchase history) ──────────────────────────
        {
            "userId": u1.id,
            "name": "Trial Week (Expired)",
            "description": "7 day introductory trial — completed",
            "price": 150.0,
            "durationDays": 7,
            "allowedClasses": beginner_ids if beginner_ids else all_class_ids[:2],
            "allowedCourses": [],
            "timeRestriction": None,
            "status": "EXPIRED",
            "progress": 100.0,
            "startDate": past(days=40),
            "endDate": past(days=33),
            "enrolledAt": past(days=40),
            "completedAt": past(days=33),
            "autoRenew": False,
            "paymentMethod": "WALLET",
            "paymentLogId": None,
            "isPaid": True,
        },
        # ── Eleanor: ACTIVE 3-month — paid via credit card gateway ────────────
        {
            "userId": u2.id,
            "name": "3 Month Membership",
            "description": "Full access to all classes and courses for 90 days",
            "price": 2500.0,
            "durationDays": 90,
            "allowedClasses": all_class_ids,
            "allowedCourses": all_course_ids,
            "timeRestriction": None,
            "status": "ACTIVE",
            "progress": 20.0,
            "startDate": past(days=18),
            "endDate": future(days=72),
            "enrolledAt": past(days=18),
            "autoRenew": True,
            "paymentMethod": "CREDIT_CARD",
            "paymentLogId": payment_logs["membership_eleanor"].id,
            "isPaid": True,
        },
        # ── Jacob: ACTIVE Trial Pack — purchased via Package route ────────────
        # paymentLogId → module=PACKAGE.
        # Name "Trial Pack" matches the Package catalogue entry (not "Trial Week"
        # which is a Membership plan template name). This ensures Jacob's entry
        # appears in get_my_packages and package_history correctly.
        {
            "userId": u3.id,
            "name": "Trial Pack",
            "description": "7-day trial — taste the INARA experience",
            "price": 150.0,
            "durationDays": 7,
            "allowedClasses": beginner_ids if beginner_ids else all_class_ids[:2],
            "allowedCourses": [],
            "timeRestriction": "Before 12:00 PM",
            "status": "ACTIVE",
            "progress": 71.0,
            "startDate": past(days=5),
            "endDate": future(days=2),
            "enrolledAt": past(days=5),
            "autoRenew": False,
            "paymentMethod": "GOOGLE_PAY",
            "paymentLogId": payment_logs["package_jacob"].id,
            "isPaid": True,
        },
        # ── Marvin: PAUSED membership ─────────────────────────────────────────
        {
            "userId": u1.id,
            "name": "Morning Plan",
            "description": "Morning classes only — currently paused",
            "price": 980.0,
            "durationDays": 30,
            "allowedClasses": beginner_ids if beginner_ids else all_class_ids[:3],
            "allowedCourses": [],
            "timeRestriction": "Before 12:00 PM",
            "status": "PAUSED",
            "progress": 50.0,
            "startDate": past(days=20),
            "endDate": future(days=10),
            "enrolledAt": past(days=20),
            "autoRenew": False,
            "paymentMethod": "WALLET",
            "paymentLogId": None,
            "isPaid": True,
        },
        # ── Eleanor: CANCELLED membership ────────────────────────────────────
        {
            "userId": u2.id,
            "name": "Trial Week (Cancelled)",
            "description": "Trial cancelled by user before use",
            "price": 150.0,
            "durationDays": 7,
            "allowedClasses": beginner_ids if beginner_ids else all_class_ids[:2],
            "allowedCourses": [],
            "timeRestriction": None,
            "status": "CANCELLED",
            "progress": 0.0,
            "startDate": past(days=60),
            "endDate": past(days=53),
            "enrolledAt": past(days=60),
            "cancelledAt": past(days=59),
            "autoRenew": False,
            "paymentMethod": "GATEWAY",
            "paymentLogId": None,
            "isPaid": False,
        },
    ]

    memberships = []
    for m in memberships_raw:
        mem = await prisma.membership.create(data=m)
        memberships.append(mem)
        exp = mem.endDate.strftime("%Y-%m-%d") if mem.endDate else "N/A"
        print(f"  ✅ {mem.name} → {mem.status} | {mem.progress:.0f}% | expires {exp} | paid={mem.isPaid}")

    print(f"✨ {len(memberships)} memberships created\n")
    return memberships



# ============================================
# BOOKINGS  —  class + course, all statuses, with payment tracking
# ============================================

async def seed_bookings(users: dict, classes: list, courses: dict, payment_logs: dict) -> list:
    print("📅 Seeding bookings (all statuses + payment tracking)...")

    u1 = users["marvin@test.com"]
    u2 = users["eleanor@test.com"]
    u3 = users["jacob@test.com"]

    scheduled_classes = [c for c in classes if c.status == "SCHEDULED"]
    course_list = list(courses.values())

    bookings_data = []

    # ── Class bookings with various statuses ──────────────────────────────────
    for idx, cls in enumerate(scheduled_classes):
        user_pool = [u1, u2, u3]
        count = min(2 + (idx % 2), 3)
        for i in range(count):
            user = user_pool[i % 3]
            # Vary payment method and status across records
            if i == 0:
                bstatus = "CONFIRMED"
                pm = "WALLET"
                amount = cls.price
                log_id = payment_logs["booking_pay_marvin"].id if user.id == u1.id else None
            elif i == 1:
                bstatus = "CONFIRMED"
                pm = "GATEWAY"
                amount = cls.price
                log_id = payment_logs["booking_pay_eleanor"].id if user.id == u2.id else None
            else:
                bstatus = "PENDING"
                pm = "WALLET"
                amount = 0.0
                log_id = None
            bookings_data.append({
                "userId": user.id,
                "classId": cls.id,
                "status": bstatus,
                "bookedAt": past(days=idx + 1),
                "notes": f"Class booking for {cls.title}",
                "paymentMethod": pm,
                "amountPaid": amount,
                "paymentLogId": log_id,
            })

    # ── A few extra statuses for completeness ────────────────────────────────
    if scheduled_classes:
        # CANCELLED (with refund applied)
        bookings_data.append({
            "userId": u1.id,
            "classId": scheduled_classes[0].id if len(scheduled_classes) > 0 else None,
            "status": "CANCELLED",
            "bookedAt": past(days=15),
            "cancelledAt": past(days=14),
            "notes": "Cancelled — refund issued to wallet",
            "paymentMethod": "WALLET",
            "amountPaid": 55.0,
            "paymentLogId": payment_logs["booking_refund_marvin"].id,
        }) if len(scheduled_classes) > 0 else None

    # ── Course bookings ────────────────────────────────────────────────────────
    booking_statuses = ["CONFIRMED", "CONFIRMED", "ATTENDED", "MISSED", "PENDING"]
    for idx, course in enumerate(course_list[:3]):  # limit to 3 courses
        user_pool = [u1, u2, u3]
        for i in range(min(2, 3)):
            user = user_pool[i % 3]
            bookings_data.append({
                "userId": user.id,
                "courseId": course.id,
                "status": booking_statuses[idx % len(booking_statuses)],
                "bookedAt": past(days=idx + 2),
                "notes": f"Enrolled in course: {course.title}",
                "paymentMethod": "CREDIT_CARD" if i == 0 else "WALLET",
                "amountPaid": course.price if not course.isFree else 0.0,
                "paymentLogId": None,
            })

    bookings = []
    seen = set()
    for b in bookings_data:
        if not b:
            continue
        # Enforce unique constraint: (userId, classId) and (userId, courseId)
        if "classId" in b and b["classId"]:
            key = (b["userId"], "class", b["classId"])
        elif "courseId" in b and b["courseId"]:
            key = (b["userId"], "course", b["courseId"])
        else:
            continue
        if key in seen:
            continue
        seen.add(key)
        try:
            booking = await prisma.booking.create(data=b)
            bookings.append(booking)
        except Exception:
            pass  # skip remaining duplicates

    print(f"✨ {len(bookings)} bookings created\n")
    return bookings


# ============================================
# PRODUCTS  —  all fields + all statuses
# ============================================

async def seed_products() -> list:
    print("🛍️  Seeding products (all statuses + pickupNote)...")

    products_raw = [
        {
            "name": "Premium Eco-Friendly Yoga Mat (6mm)",
            "slug": "premium-yoga-mat-6mm",
            "description": "High-quality non-slip TPE yoga mat with alignment lines and carry strap. Perfect for beginners and advanced practitioners.",
            "shortDescription": "6mm thick, eco-friendly TPE material",
            "material": "TPE", "dimensions": "183x61x0.6 cm", "weight": 1.2,
            "color": "Teal", "size": "Standard",
            "price": 2450.0, "discountPrice": 1990.0,
            "sku": "YM-001", "stockQuantity": 20,
            "status": "AVAILABLE",
            "images": [
                "https://images.unsplash.com/photo-1601925228008-8c5b6bc7c4e5?w=600",
                "https://images.unsplash.com/photo-1518611012118-696072aa579a?w=600",
            ],
            "thumbnail": "https://images.unsplash.com/photo-1601925228008-8c5b6bc7c4e5?w=300",
            "tags": ["yoga", "mat", "eco-friendly"],
            "isFeatured": True,
            "viewCount": 240, "salesCount": 18,
            "averageRating": 4.8, "totalReviews": 12,
            "pickupNote": "Available at the studio reception — bring your order confirmation.",
        },
        {
            "name": "100% Cotton Yoga Strap (8 ft)",
            "slug": "cotton-yoga-strap-8ft",
            "description": "Durable cotton yoga strap with metal D-ring buckle. Ideal for deepening stretches and improving flexibility.",
            "shortDescription": "8 ft, metal D-ring, machine washable",
            "material": "Cotton", "dimensions": "244x3.8 cm", "weight": 0.12,
            "color": "Natural Beige", "size": "8 ft",
            "price": 2450.0, "discountPrice": None,
            "sku": "YS-001", "stockQuantity": 10,
            "status": "AVAILABLE",
            "images": ["https://images.unsplash.com/photo-1545389336-cf090694435e?w=600"],
            "thumbnail": "https://images.unsplash.com/photo-1545389336-cf090694435e?w=300",
            "tags": ["yoga", "strap", "cotton"],
            "isFeatured": False,
            "viewCount": 90, "salesCount": 7,
            "averageRating": 4.5, "totalReviews": 5,
            "pickupNote": "Available at the studio reception.",
        },
        {
            "name": "Round Meditation Cushion (Zafu)",
            "slug": "meditation-cushion-zafu",
            "description": "Traditional buckwheat-filled zafu for comfortable seated meditation. Removable, washable cover.",
            "shortDescription": "Buckwheat hull filling, removable cover",
            "material": "Cotton + Buckwheat Hull", "dimensions": "Ø33x15 cm", "weight": 1.8,
            "color": "Charcoal Grey", "size": "Standard",
            "price": 2450.0, "discountPrice": 2100.0,
            "sku": "MC-001", "stockQuantity": 10,
            "status": "AVAILABLE",
            "images": ["https://images.unsplash.com/photo-1506126613408-eca07ce68773?w=600"],
            "thumbnail": "https://images.unsplash.com/photo-1506126613408-eca07ce68773?w=300",
            "tags": ["meditation", "cushion", "zafu"],
            "isFeatured": True,
            "viewCount": 115, "salesCount": 9,
            "averageRating": 4.9, "totalReviews": 8,
            "pickupNote": "Available at the studio. Call ahead to confirm stock.",
        },
        {
            "name": "Insulated Stainless Steel Water Bottle 750ml",
            "slug": "steel-water-bottle-750ml",
            "description": "Double-wall vacuum insulated stainless steel bottle. Keeps cold 24h, hot 12h. BPA-free.",
            "shortDescription": "Double-wall vacuum insulation, BPA-free",
            "material": "Stainless Steel", "dimensions": "26x7 cm", "weight": 0.32,
            "color": "Midnight Black", "size": "750ml",
            "price": 2450.0, "discountPrice": None,
            "sku": "WB-001", "stockQuantity": 10,
            "status": "AVAILABLE",
            "images": ["https://images.unsplash.com/photo-1602143407151-7111542de6e8?w=600"],
            "thumbnail": "https://images.unsplash.com/photo-1602143407151-7111542de6e8?w=300",
            "tags": ["bottle", "hydration", "insulated"],
            "isFeatured": False,
            "viewCount": 180, "salesCount": 23,
            "averageRating": 4.6, "totalReviews": 15,
            "pickupNote": "Available at the studio shop.",
        },
        {
            "name": "High-Density Foam Yoga Block",
            "slug": "foam-yoga-block",
            "description": "Extra-firm high-density EVA foam block for support and alignment in yoga poses.",
            "shortDescription": "High-density EVA, non-slip surface",
            "material": "EVA Foam", "dimensions": "23x15x7.5 cm", "weight": 0.25,
            "color": "Purple", "size": "Standard",
            "price": 2450.0, "discountPrice": None,
            "sku": "YB-002", "stockQuantity": 10,
            "status": "AVAILABLE",
            "images": ["https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=600"],
            "thumbnail": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=300",
            "tags": ["yoga", "block", "foam"],
            "isFeatured": False,
            "viewCount": 76, "salesCount": 11,
            "averageRating": 4.3, "totalReviews": 6,
            "pickupNote": "Available at the studio reception.",
        },
        {
            "name": "Resistance Band Set — 5 Levels",
            "slug": "resistance-bands-5-levels",
            "description": "Professional resistance band set. Five resistance levels from light to extra heavy. Ideal for home and studio workouts.",
            "shortDescription": "5 resistance levels, natural latex",
            "material": "Natural Latex", "dimensions": "N/A", "weight": 0.4,
            "color": "Multicolour", "size": "One Size",
            "price": 850.0, "discountPrice": 700.0,
            "sku": "RB-001", "stockQuantity": 0,
            "status": "OUT_OF_STOCK",
            "images": ["https://images.unsplash.com/photo-1517836357463-d25dfeac3438?w=600"],
            "thumbnail": "https://images.unsplash.com/photo-1517836357463-d25dfeac3438?w=300",
            "tags": ["resistance", "bands", "strength"],
            "isFeatured": True,
            "viewCount": 320, "salesCount": 40,
            "averageRating": 4.7, "totalReviews": 22,
            "pickupNote": "Currently out of stock — notify me when available.",
        },
        {
            "name": "Foam Roller — Deep Tissue (90cm)",
            "slug": "foam-roller-90cm",
            "description": "Extra-firm EVA foam roller for deep tissue massage and muscle recovery. Full-length 90cm.",
            "shortDescription": "Extra firm, full-length 90cm",
            "material": "EVA Foam", "dimensions": "90x15 cm", "weight": 0.55,
            "color": "Black", "size": "90 cm",
            "price": 1400.0, "discountPrice": 1150.0,
            "sku": "FR-001", "stockQuantity": 0,
            "status": "DISCONTINUED",
            "images": ["https://images.unsplash.com/photo-1526506118085-60ce8714f8c5?w=600"],
            "thumbnail": "https://images.unsplash.com/photo-1526506118085-60ce8714f8c5?w=300",
            "tags": ["foam roller", "recovery", "massage"],
            "isFeatured": False,
            "viewCount": 95, "salesCount": 5,
            "averageRating": 4.2, "totalReviews": 4,
            "pickupNote": None,
        },
    ]

    products = []
    for p in products_raw:
        product = await prisma.product.create(data=p)
        products.append(product)
        print(f"  ✅ {product.name} [{product.status}] — QAR {product.price}")

    print(f"✨ {len(products)} products created\n")
    return products


# ============================================
# ORDERS  —  full lifecycle + all OrderStatus values
# ============================================

async def seed_orders(users: dict, products: list, payment_logs: dict) -> list:
    print("🛒 Seeding orders (all status values + pickup, no shipping)...")

    u1 = users["marvin@test.com"]
    u2 = users["eleanor@test.com"]
    u3 = users["jacob@test.com"]

    # Available products (AVAILABLE status, stockQuantity > 0)
    avail = [p for p in products if p.status == "AVAILABLE"]

    orders_config = [
        # ── PAID (just paid, awaiting processing) ─────────────────────────────
        {
            "order": {
                "orderNumber": "ORD-2025-001",
                "userId": u2.id,
                "orderType": "PRODUCT",
                "subtotal": 345.0, "discount": 0.0, "tax": 0.0, "total": 345.0,
                "status": "PAID",
                "paymentMethod": "CREDIT_CARD",
                "transactionId": "PAY-MF-50001",
                "paidAt": past(days=1),
                "pickupNote": "Pickup at Studio A reception — bring your order confirmation.",
                "notes": "Please gift-wrap the mat.",
                "paymentLogId": payment_logs["order_eleanor_paid"].id,
            },
            "items": [(avail[0], 1)],
        },
        # ── PROCESSING ────────────────────────────────────────────────────────
        {
            "order": {
                "orderNumber": "ORD-2025-002",
                "userId": u1.id,
                "orderType": "PRODUCT",
                "subtotal": 199.0, "discount": 0.0, "tax": 0.0, "total": 199.0,
                "status": "PROCESSING",
                "paymentMethod": "WALLET",
                "transactionId": None,
                "paidAt": past(days=3),
                "pickupNote": "Your order is being prepared. We'll notify you when ready.",
                "paymentLogId": payment_logs["order_marvin_wallet"].id,
            },
            "items": [(avail[1] if len(avail) > 1 else avail[0], 1)],
        },
        # ── PICKEDUP (completed lifecycle) ────────────────────────────────────
        {
            "order": {
                "orderNumber": "ORD-2025-003",
                "userId": u3.id,
                "orderType": "PRODUCT",
                "subtotal": 700.0, "discount": 50.0, "tax": 0.0, "total": 650.0,
                "status": "PICKEDUP",
                "paymentMethod": "DEBIT_CARD",
                "transactionId": "PAY-MF-50099",
                "paidAt": past(days=10),
                "pickupNote": "Picked up from Studio B. Thank you!",
                "couponCode": "SAVE50",
                "paymentLogId": None,
            },
            "items": [(avail[2] if len(avail) > 2 else avail[0], 2)],
        },
        # ── FAILED ────────────────────────────────────────────────────────────
        {
            "order": {
                "orderNumber": "ORD-2025-004",
                "userId": u3.id,
                "orderType": "PRODUCT",
                "subtotal": 85.0, "discount": 0.0, "tax": 0.0, "total": 85.0,
                "status": "FAILED",
                "paymentMethod": "GATEWAY",
                "transactionId": None,
                "paidAt": None,
                "notes": "Payment failed — inventory released.",
                "paymentLogId": payment_logs["order_cancelled"].id,
            },
            "items": [(avail[3] if len(avail) > 3 else avail[0], 1)],
        },
        # ── REFUNDED ──────────────────────────────────────────────────────────
        {
            "order": {
                "orderNumber": "ORD-2025-005",
                "userId": u2.id,
                "orderType": "PRODUCT",
                "subtotal": 140.0, "discount": 0.0, "tax": 0.0, "total": 140.0,
                "status": "REFUNDED",
                "paymentMethod": "APPLE_PAY",
                "transactionId": "PAY-MF-50050",
                "paidAt": past(days=20),
                "refundedAt": past(days=18),
                "refundAmount": 140.0,
                "notes": "Refund issued — product damaged on inspection.",
                "paymentLogId": None,
            },
            "items": [(avail[4] if len(avail) > 4 else avail[0], 1)],
        },
        # ── CANCELLED ─────────────────────────────────────────────────────────
        {
            "order": {
                "orderNumber": "ORD-2025-006",
                "userId": u1.id,
                "orderType": "PRODUCT",
                "subtotal": 60.0, "discount": 0.0, "tax": 0.0, "total": 60.0,
                "status": "CANCELLED",
                "paymentMethod": "GOOGLE_PAY",
                "transactionId": None,
                "paidAt": None,
                "notes": "Cancelled by user before payment.",
                "paymentLogId": None,
            },
            "items": [(avail[0], 1)],
        },
        # ── PENDING (just placed, awaiting gateway redirect) ──────────────────
        {
            "order": {
                "orderNumber": "ORD-2025-007",
                "userId": u1.id,
                "orderType": "PRODUCT",
                "subtotal": 250.0, "discount": 0.0, "tax": 0.0, "total": 250.0,
                "status": "PENDING",
                "paymentMethod": "GATEWAY",
                "transactionId": None,
                "paidAt": None,
                "pickupNote": "Available at Studio A once payment is confirmed.",
                "paymentLogId": None,
            },
            "items": [(avail[0], 1)],
        },
    ]

    orders = []
    for cfg in orders_config:
        try:
            order = await prisma.order.create(data=cfg["order"])
            orders.append(order)
            print(f"  ✅ {order.orderNumber} — {order.status} — QAR {order.total} [{order.paymentMethod}]")
            for product, qty in cfg["items"]:
                unit_price = product.discountPrice if product.discountPrice else product.price
                await prisma.orderitem.create(data={
                    "orderId": order.id,
                    "productId": product.id,
                    "quantity": qty,
                    "price": unit_price,
                    "total": unit_price * qty,
                })
        except Exception as e:
            print(f"  ⚠️  Order skipped: {e}")

    print(f"✨ {len(orders)} orders created\n")
    return orders


# ============================================
# REVIEWS  —  class + course + product
# ============================================

async def seed_reviews(users: dict, classes: list, courses: dict, products: list) -> list:
    print("⭐ Seeding reviews (classes + courses + products)...")

    u1 = users["marvin@test.com"]
    u2 = users["eleanor@test.com"]
    u3 = users["jacob@test.com"]
    yc = courses["Complete Yoga Mastery"]
    pc = courses["Advanced Pilates Training"]
    completed_classes = [c for c in classes if c.status in ("COMPLETED", "ONGOING")]

    reviews_raw = [
        # ── Course reviews ────────────────────────────────────────────────────
        {
            "userId": u1.id, "courseId": yc.id, "rating": 5,
            "comment": "Incredible course! Savannah's instructions are clear and precise. Transformed my practice completely.",
            "images": [], "isPublished": True,
        },
        {
            "userId": u2.id, "courseId": yc.id, "rating": 4,
            "comment": "Great for beginners. The pacing is perfect and the studio is very welcoming.",
            "images": [], "isPublished": True,
        },
        {
            "userId": u3.id, "courseId": pc.id, "rating": 5,
            "comment": "Best Pilates course in Doha. Jane is phenomenal — challenging but supportive.",
            "images": [], "isPublished": True,
        },
        {
            "userId": u2.id, "courseId": pc.id, "rating": 3,
            "comment": "Good course, but I found the pace a bit fast for a complete beginner.",
            "images": [], "isPublished": False,  # unpublished review — covers isPublished=False
        },
        # ── Class reviews ─────────────────────────────────────────────────────
        *(
            [
                {
                    "userId": u1.id, "classId": completed_classes[0].id, "rating": 5,
                    "comment": "Loved every minute of this class. Will be back!",
                    "images": ["https://images.unsplash.com/photo-1545389336-cf090694435e?w=300"],
                    "isPublished": True,
                }
            ]
            if completed_classes else []
        ),
        # ── Product reviews ───────────────────────────────────────────────────
        {
            "userId": u1.id, "productId": products[0].id, "rating": 5,
            "comment": "Best yoga mat I've owned. Non-slip, great cushioning, easy to clean.",
            "images": ["https://images.unsplash.com/photo-1601925228008-8c5b6bc7c4e5?w=300"],
            "isPublished": True,
        },
        {
            "userId": u2.id, "productId": products[1].id, "rating": 4,
            "comment": "Solid cotton strap. Exactly what I needed for my forward folds.",
            "images": [], "isPublished": True,
        },
        {
            "userId": u3.id, "productId": products[2].id, "rating": 5,
            "comment": "The zafu cushion is perfect. Very comfortable for long sits.",
            "images": [], "isPublished": True,
        },
    ]

    reviews = []
    for r in reviews_raw:
        review = await prisma.review.create(data=r)
        reviews.append(review)
        preview = (review.comment or "")[:55]
        print(f"  ✅ {review.rating}⭐ — {preview}{'...' if len(review.comment or '') > 55 else ''}")

    print(f"✨ {len(reviews)} reviews created\n")
    return reviews


# ============================================
# NOTIFICATIONS  —  all NotificationType values
# ============================================

async def seed_notifications(users: dict) -> list:
    print("🔔 Seeding notifications (all types)...")

    u1 = users["marvin@test.com"]
    u2 = users["eleanor@test.com"]
    u3 = users["jacob@test.com"]

    notifs_raw = [
        {
            "userId": u1.id, "title": "Class Starting Soon",
            "message": "Morning Vinyasa Flow starts in 1 hour. See you on the mat!",
            "type": "REMINDER", "isRead": False,
            "actionUrl": "/classes/morning-vinyasa",
        },
        {
            "userId": u1.id, "title": "Membership Expiring",
            "message": "Your 1 Month Membership expires in 5 days. Renew to keep your access!",
            "type": "WARNING", "isRead": False,
            "actionUrl": "/memberships",
        },
        {
            "userId": u1.id, "title": "Payment Successful",
            "message": "Your wallet top-up of QAR 500 was successful. Balance: QAR 306.",
            "type": "SUCCESS", "isRead": True, "readAt": past(hours=3),
            "actionUrl": "/wallet",
        },
        {
            "userId": u1.id, "title": "Welcome to INARA!",
            "message": "Your account is active. Browse classes and start your journey today.",
            "type": "INFO", "isRead": True, "readAt": past(days=10),
            "actionUrl": "/classes",
        },
        {
            "userId": u1.id, "title": "Payment Failed",
            "message": "Your payment for the class could not be processed. Please try again.",
            "type": "ERROR", "isRead": False,
            "actionUrl": "/payments",
        },
        {
            "userId": u2.id, "title": "Order Ready for Pickup",
            "message": "Your order ORD-2025-001 is ready at Studio A reception.",
            "type": "SUCCESS", "isRead": True, "readAt": past(hours=2),
            "actionUrl": "/orders/ORD-2025-001",
        },
        {
            "userId": u2.id, "title": "Booking Confirmed",
            "message": "Your booking for Evening Pilates has been confirmed.",
            "type": "SUCCESS", "isRead": False,
            "actionUrl": "/bookings",
        },
        {
            "userId": u3.id, "title": "Trial Expiring in 2 Days",
            "message": "Your trial ends soon! Upgrade to continue your wellness journey.",
            "type": "WARNING", "isRead": False,
            "actionUrl": "/memberships",
        },
        {
            "userId": u3.id, "title": "New Class Added",
            "message": "A new Calisthenics Foundations class has been added next week.",
            "type": "INFO", "isRead": False,
            "actionUrl": "/classes",
        },
    ]

    notifications = []
    for n in notifs_raw:
        notif = await prisma.notification.create(data=n)
        notifications.append(notif)
        print(f"  ✅ [{notif.type}] {notif.title}")

    print(f"✨ {len(notifications)} notifications created\n")
    return notifications


# ============================================
# WISHLISTS  —  class + course
# ============================================

async def seed_wishlists(users: dict, classes: list, courses: dict) -> list:
    print("❤️  Seeding wishlists (classes + courses)...")

    u1 = users["marvin@test.com"]
    u2 = users["eleanor@test.com"]
    u3 = users["jacob@test.com"]
    scheduled = [c for c in classes if c.status == "SCHEDULED"]

    wishlists_raw = [
        # Course wishlists
        {"userId": u1.id, "courseId": courses["Advanced Pilates Training"].id},
        {"userId": u2.id, "courseId": courses["Strength & Conditioning"].id},
        {"userId": u3.id, "courseId": courses["Complete Yoga Mastery"].id},
        {"userId": u3.id, "courseId": courses["Women's Wellness & Mindfulness"].id},
        # Class wishlists
        *([
            {"userId": u1.id, "classId": scheduled[0].id},
            {"userId": u2.id, "classId": scheduled[1].id if len(scheduled) > 1 else scheduled[0].id},
        ] if scheduled else []),
    ]

    wishlists = []
    for w in wishlists_raw:
        try:
            wl = await prisma.wishlist.create(data=w)
            wishlists.append(wl)
            kind = "course" if "courseId" in w else "class"
            print(f"  ✅ Wishlist {kind} added for user")
        except Exception as e:
            print(f"  ⚠️  Skipped (duplicate): {e}")

    print(f"✨ {len(wishlists)} wishlist items created\n")
    return wishlists


# ============================================
# CART ITEMS
# ============================================

async def seed_cart_items(users: dict, products: list) -> list:
    print("🛒 Seeding cart items...")

    u2 = users["eleanor@test.com"]
    u3 = users["jacob@test.com"]
    avail = [p for p in products if p.status == "AVAILABLE"]

    cart_raw = [
        {"userId": u2.id, "productId": avail[2].id, "quantity": 1},
        {"userId": u2.id, "productId": avail[3].id, "quantity": 1},
        {"userId": u3.id, "productId": avail[0].id, "quantity": 1},
        {"userId": u3.id, "productId": avail[1].id if len(avail) > 1 else avail[0].id, "quantity": 2},
    ]

    cart_items = []
    for item in cart_raw:
        try:
            ci = await prisma.cartitem.create(data=item)
            cart_items.append(ci)
            print(f"  ✅ Cart item added (qty: {ci.quantity})")
        except Exception as e:
            print(f"  ⚠️  Skipped (duplicate): {e}")

    print(f"✨ {len(cart_items)} cart items created\n")
    return cart_items


# ============================================
# NEWS  —  with durationDays and expiresAt covered
# ============================================

async def seed_news() -> list:
    print("📰 Seeding news (durationDays + expiresAt covered)...")

    news_raw = [
        {
            "title": "New Morning Yoga Classes Added",
            "shortDescription": "Join our new early morning yoga sessions starting next week. Perfect for starting your day with energy and mindfulness.",
            "thumbnail": "https://images.unsplash.com/photo-1506126613408-eca07ce68773?w=800",
            "publishedAt": past(days=2),
            "durationDays": 14,
            "expiresAt": future(days=12),
        },
        {
            "title": "Special Workshop This Weekend",
            "shortDescription": "Don't miss our special prenatal yoga workshop this Saturday. Expert guidance for expecting mothers.",
            "thumbnail": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=800",
            "publishedAt": past(days=5),
            "durationDays": 7,
            "expiresAt": future(days=2),
        },
        {
            "title": "Improve Your Flexibility in 30 Days",
            "shortDescription": "Join our 30-day flexibility challenge starting next month. Transform your practice with daily guided sessions.",
            "thumbnail": "https://images.unsplash.com/photo-1599901860904-17e6ed7083a0?w=800",
            "publishedAt": past(days=10),
            "durationDays": None,       # permanent — never expires
            "expiresAt": None,
        },
        {
            "title": "New Studio Opening in West Bay",
            "shortDescription": "We're excited to announce our brand new studio location opening in West Bay next month!",
            "thumbnail": "https://images.unsplash.com/photo-1545205597-3d9d02c29597?w=800",
            "publishedAt": past(days=15),
            "durationDays": 30,
            "expiresAt": future(days=15),
        },
        {
            "title": "Holiday Schedule Updates",
            "shortDescription": "Please note our modified schedule for the upcoming holidays. Check the app for updated class times.",
            "thumbnail": "https://images.unsplash.com/photo-1506126613408-eca07ce68773?w=800",
            "publishedAt": past(days=20),
            "durationDays": 5,
            "expiresAt": past(days=15),  # already expired — valid historical row
        },
    ]

    news = []
    for n in news_raw:
        news_item = await prisma.news.create(data=n)
        news.append(news_item)
        expires = news_item.expiresAt.strftime("%d %b %Y") if news_item.expiresAt else "permanent"
        print(f"  ✅ {news_item.title} — expires: {expires}")

    print(f"✨ {len(news)} news items created\n")
    return news


# ============================================
# ABOUT US  (singleton)
# ============================================

async def seed_about_us():
    print("ℹ️  Seeding About Us...")

    existing = await prisma.aboutus.find_first()
    if existing:
        print("  ⏭️  About Us already exists, skipping")
        return existing

    about_us = await prisma.aboutus.create(data={
        "activeMembers": 500,
        "totalClasses": 120,
        "totalInstructors": 18,
        "ourStory": (
            "Founded in 2019 in the heart of Doha, INARA was born from a vision to build "
            "Qatar's most welcoming wellness community. What started as a single yoga studio "
            "has grown into a thriving hub of movement, mindfulness, and belonging.\n\n"
            "Today our four studio locations host hundreds of weekly classes led by "
            "world-class instructors — each committed to helping every student discover "
            "their strongest, most balanced self."
        ),
        "ourMission": (
            "We believe wellness is a right, not a privilege. INARA's mission is to make "
            "expert-led fitness and mindfulness accessible to everyone in Qatar — regardless "
            "of age, background, or experience level.\n\n"
            "Every class we offer, every instructor we train, and every community event we host "
            "is guided by one principle: to help you feel better in your body, calmer in your mind, "
            "and more connected to the people around you."
        ),
        "location": "Al Corniche Street, Building 22, West Bay, Doha, Qatar",
        "locationMapLink": "https://maps.google.com/?q=West+Bay+Doha",
        "latitude": 25.3548,
        "longitude": 51.5310,
        "email": "hello@inara.com",
        "phoneNumber": "+974 4412 3456",
        "instagramAccount": "@inara.qatar",
        "facebookPage": "fb.com/inara.qatar",
        "websiteUrl": "https://inara.com",
    })

    print("  ✅ About Us initialised")
    return about_us


# ============================================
# LEGAL DOCUMENTS
# ============================================

async def seed_legal_documents():
    print("📜 Seeding Legal Documents...")

    docs = [
        {
            "type": "PRIVACY_POLICY",
            "content": (
                "# Privacy Policy\n**Last updated: March 2025**\n\n"
                "## 1. Information We Collect\nWe collect information you provide directly "
                "when you register an account, book a class, make a purchase, or contact support.\n\n"
                "## 2. How We Use Your Information\nTo manage bookings, send reminders, "
                "personalise your experience, and improve the platform.\n\n"
                "## 3. Data Sharing\nWe do not sell your data. We share only with payment "
                "processors, instructors relevant to your bookings, and as required by law.\n\n"
                "## 4. Your Rights\nAccess, correct, or delete your data at any time. "
                "Contact privacy@inara.com.\n\n"
                "## 5. Security\nIndustry-standard encryption and access controls protect your data."
            ),
        },
        {
            "type": "TERMS_CONDITIONS",
            "content": (
                "# Terms & Conditions\n**Last updated: March 2025**\n\n"
                "## 1. Eligibility\nYou must be at least 18 years old to use INARA services.\n\n"
                "## 2. Bookings & Cancellations\nCancellations made at least 3 hours before class "
                "start time are eligible for a refund. Cancellations within 3 hours are non-refundable.\n\n"
                "## 3. Payments & Refunds\nAll prices are in QAR. Membership and Package fees are "
                "non-refundable once purchased.\n\n"
                "## 4. Health Disclaimer\nINARA is not a substitute for professional medical advice. "
                "Consult your physician before starting any exercise programme.\n\n"
                "## 5. Governing Law\nThese terms are governed by the laws of the State of Qatar."
            ),
        },
        {
            "type": "FAQ",
            "content": (
                "# Frequently Asked Questions\n\n"
                "**How do I book a class?**\nLog in, go to Classes, select a class, and tap Book.\n\n"
                "**Can I cancel a booking?**\nYes — cancellations 3+ hours before class start receive a full refund. "
                "Cancellations within 3 hours are non-refundable.\n\n"
                "**What payment methods do you accept?**\nCredit Card, Debit Card, Apple Pay, Google Pay, and Wallet.\n\n"
                "**How do I top up my wallet?**\nGo to Wallet, tap Add Money, choose an amount, and complete payment.\n\n"
                "**Are memberships refundable?**\nNo. Membership and Package purchases are non-refundable.\n\n"
                "**How do I reset my password?**\nTap Forgot Password on the login screen.\n\n"
                "**How do I delete my account?**\nContact support@inara.com."
            ),
        },
    ]

    created = []
    for doc in docs:
        result = await prisma.legaldocument.upsert(
            where={"type": doc["type"]},
            data={"create": doc, "update": {"content": doc["content"]}},
        )
        created.append(result)
        label = doc["type"].replace("_", " ").title()
        print(f"  ✅ {label}")

    print(f"✨ {len(created)} legal documents seeded\n")
    return created


# ============================================
# INVITATIONS  —  team + member
# ============================================

async def seed_invitations(users: dict) -> tuple:
    print("✉️  Seeding invitations (team + member)...")

    admin = users["admin@inara.com"]

    # ── Team invitations (Invitation model) ───────────────────────────────────
    team_invites_raw = [
        {
            "email": "newmanager@inara.com",
            "role": "MANAGER",
            "inviteToken": uuid.uuid4().hex,
            "status": "PENDING",
            "invitedBy": admin.id,
            "expiresAt": future(days=7),
        },
        {
            "email": "newinstructor@inara.com",
            "role": "INSTRUCTOR",
            "inviteToken": uuid.uuid4().hex,
            "status": "ACCEPTED",
            "invitedBy": admin.id,
            "expiresAt": future(days=7),
            "acceptedAt": past(days=1),
        },
        {
            "email": "expired-invite@inara.com",
            "role": "INSTRUCTOR",
            "inviteToken": uuid.uuid4().hex,
            "status": "EXPIRED",
            "invitedBy": admin.id,
            "expiresAt": past(days=3),
        },
        {
            "email": "cancelled-invite@inara.com",
            "role": "MANAGER",
            "inviteToken": uuid.uuid4().hex,
            "status": "CANCELLED",
            "invitedBy": admin.id,
            "expiresAt": past(days=1),
        },
    ]

    team_invites = []
    for inv in team_invites_raw:
        i = await prisma.invitation.create(data=inv)
        team_invites.append(i)
        print(f"  ✅ Team invite → {i.email} [{i.role}] [{i.status}]")

    # ── Member invitations (MemberInvitation model) ───────────────────────────
    member_invites_raw = [
        {
            "email": "newmember1@test.com",
            "inviteToken": uuid.uuid4().hex,
            "status": "PENDING",
            "invitedBy": admin.id,
            "name": "Aisha Al-Mansoori",
            "expiresAt": future(days=7),
        },
        {
            "email": "newmember2@test.com",
            "inviteToken": uuid.uuid4().hex,
            "status": "ACCEPTED",
            "invitedBy": admin.id,
            "name": "Khalid Hassan",
            "expiresAt": future(days=7),
            "acceptedAt": past(hours=6),
        },
        {
            "email": "newmember3@test.com",
            "inviteToken": uuid.uuid4().hex,
            "status": "EXPIRED",
            "invitedBy": admin.id,
            "name": None,               # name can be null — covers optional field
            "expiresAt": past(days=5),
        },
        {
            "email": "newmember4@test.com",
            "inviteToken": uuid.uuid4().hex,
            "status": "CANCELLED",
            "invitedBy": admin.id,
            "name": "Fatima Al-Rashid",
            "expiresAt": past(days=1),
        },
    ]

    member_invites = []
    for inv in member_invites_raw:
        i = await prisma.memberinvitation.create(data=inv)
        member_invites.append(i)
        print(f"  ✅ Member invite → {i.email} [{i.status}]")

    print(f"✨ {len(team_invites)} team invites + {len(member_invites)} member invites created\n")
    return team_invites, member_invites


# ============================================
# OTPs
# ============================================

async def seed_otps() -> list:
    print("🔑 Seeding OTPs...")

    otps_raw = [
        {
            "email": "marvin@test.com",
            "code": "847291",
            "type": "EMAIL_VERIFICATION",
            "expiresAt": future(days=15),
        },
        {
            "email": "jacob@test.com",
            "code": "312847",
            "type": "PASSWORD_RESET",
            "expiresAt": past(hours=5),   # already expired OTP
        },
        {
            "email": "newmember1@test.com",
            "code": "591023",
            "type": "REFRESH_TOKEN",
            "expiresAt": future(days=10),
        },
    ]

    otps = []
    for o in otps_raw:
        otp = await prisma.otp.create(data=o)
        otps.append(otp)
        print(f"  ✅ OTP [{otp.type}] → {otp.email}")

    print(f"✨ {len(otps)} OTPs created\n")
    return otps


# ============================================
# PERMISSION RULES
# ============================================

async def seed_permission_rules():
    print("🔐 Seeding permission rules...")

    existing = await prisma.permissionrule.find_first()
    if existing:
        print("  ⏭️  Permission rules already exist, skipping")
        return []

    rules_data = [
        {"moduleName": "Dashboard/Overview",   "adminAccess": "VISIBLE", "managerAccess": "HIDDEN",  "instructorAccess": "HIDDEN",  "displayOrder": 1},
        {"moduleName": "Classes & Courses",    "adminAccess": "VISIBLE", "managerAccess": "VISIBLE", "instructorAccess": "VISIBLE", "displayOrder": 2},
        {"moduleName": "Member Database",      "adminAccess": "VISIBLE", "managerAccess": "VISIBLE", "instructorAccess": "VISIBLE", "displayOrder": 3},
        {"moduleName": "Instructor Directory", "adminAccess": "VISIBLE", "managerAccess": "VISIBLE", "instructorAccess": "VISIBLE", "displayOrder": 4},
        {"moduleName": "App Settings",         "adminAccess": "VISIBLE", "managerAccess": "VISIBLE", "instructorAccess": "HIDDEN",  "displayOrder": 5},
        {"moduleName": "Financial Reporting",  "adminAccess": "VISIBLE", "managerAccess": "HIDDEN",  "instructorAccess": "HIDDEN",  "displayOrder": 6},
        {"moduleName": "Role Matrix",          "adminAccess": "VISIBLE", "managerAccess": "HIDDEN",  "instructorAccess": "HIDDEN",  "displayOrder": 7},
        {"moduleName": "Legal Documents",      "adminAccess": "VISIBLE", "managerAccess": "HIDDEN",  "instructorAccess": "HIDDEN",  "displayOrder": 8},
        {"moduleName": "Payment Logs",         "adminAccess": "VISIBLE", "managerAccess": "HIDDEN",  "instructorAccess": "HIDDEN",  "displayOrder": 9},
        {"moduleName": "Wallet Management",    "adminAccess": "VISIBLE", "managerAccess": "VISIBLE", "instructorAccess": "HIDDEN",  "displayOrder": 10},
    ]

    rules = []
    for rule_data in rules_data:
        rule = await prisma.permissionrule.create(data=rule_data)
        rules.append(rule)
        print(f"  ✅ {rule.moduleName}: Admin={rule.adminAccess} | Manager={rule.managerAccess} | Instructor={rule.instructorAccess}")

    print(f"✨ {len(rules)} permission rules created\n")
    return rules


# ============================================
# MAIN
# ============================================

async def main():
    await prisma.connect()

    print("\n" + "=" * 65)
    print("  🚀  INARA — COMPREHENSIVE DATABASE SEED  (Schema v3)")
    print("=" * 65 + "\n")

    try:
        await clear_all()

        users            = await seed_users()
        courses          = await seed_courses(users)
        classes          = await seed_classes(users)
        packages         = await seed_packages(classes, courses)
        payment_logs     = await seed_payment_logs(users, packages)
        wallets          = await seed_wallets(users, payment_logs)
        # Seed admin-owned plan templates FIRST (the public catalogue)
        admin_user       = users["admin@inara.com"]
        membership_plans = await seed_membership_plans(admin_user)
        # Then seed user-purchased membership records (independent from packages)
        memberships      = await seed_memberships(users, classes, courses, payment_logs)
        bookings         = await seed_bookings(users, classes, courses, payment_logs)
        products         = await seed_products()
        orders           = await seed_orders(users, products, payment_logs)
        reviews          = await seed_reviews(users, classes, courses, products)
        notifications    = await seed_notifications(users)
        wishlists        = await seed_wishlists(users, classes, courses)
        cart_items       = await seed_cart_items(users, products)
        news             = await seed_news()
        about_us         = await seed_about_us()
        legal_docs       = await seed_legal_documents()
        team_inv, mem_inv = await seed_invitations(users)
        otps             = await seed_otps()
        permission_rules = await seed_permission_rules()

        print("=" * 65)
        print("  ✅  SEEDING COMPLETED SUCCESSFULLY!")
        print("=" * 65)
        print()
        print("📊 SUMMARY")
        print("-" * 40)
        print(f"  👥 Users                {len(users)}")
        print(f"  📚 Courses              {len(courses)}  (all 4 CourseStatus values)")
        print(f"  🏃 Classes              {len(classes)}  (SCHEDULED/ONGOING/COMPLETED/CANCELLED)")
        print(f"  📦 Packages             {len(packages)}")
        print(f"  💳 Payment Logs         {len(payment_logs)}  (all modules + statuses)")
        print(f"  👛 Wallets              {len(wallets)}  (DEPOSIT + DEBIT transactions)")
        print(f"  📋 Membership Plans     {len(membership_plans)}  (admin-owned catalogue templates)")
        print(f"  🎫 Memberships          {len(memberships)}  (user-purchased: ACTIVE/EXPIRED/PAUSED/CANCELLED)")
        print(f"  📅 Bookings             {len(bookings)}  (class + course, all statuses)")
        print(f"  🛍️  Products             {len(products)}  (AVAILABLE/OUT_OF_STOCK/DISCONTINUED)")
        print(f"  🛒 Orders               {len(orders)}  (PENDING/PAID/PROCESSING/PICKEDUP/FAILED/REFUNDED/CANCELLED)")
        print(f"  ⭐ Reviews              {len(reviews)}  (class + course + product)")
        print(f"  🔔 Notifications        {len(notifications)}  (all 5 NotificationType values)")
        print(f"  ❤️  Wishlists            {len(wishlists)}  (class + course)")
        print(f"  🛒 Cart Items           {len(cart_items)}")
        print(f"  📰 News                 {len(news)}  (durationDays + expiresAt covered)")
        print(f"  ℹ️  About Us             {'✅ Configured' if about_us else '❌ Not configured'}")
        print(f"  📜 Legal Docs           {len(legal_docs)}  (Privacy Policy, T&C, FAQ)")
        print(f"  ✉️  Team Invitations     {len(team_inv)}  (all InvitationStatus values)")
        print(f"  ✉️  Member Invitations   {len(mem_inv)}  (all InvitationStatus values)")
        print(f"  🔑 OTPs                 {len(otps)}")
        print(f"  🔐 Permission Rules     {len(permission_rules)}")
        print()
        print("🔑 TEST CREDENTIALS")
        print("-" * 40)
        print("  Admin:      admin@inara.com       / Admin123!")
        print("  Manager:    manager@inara.com     / Manager123!")
        print("  Instructor: savannah@inara.com    / Instructor123!")
        print("  User 1:     marvin@test.com       / User123!   (wallet: QAR 306)")
        print("  User 2:     eleanor@test.com      / User123!   (wallet: QAR 580)")
        print("  User 3:     jacob@test.com        / User123!   (wallet: QAR 200)")
        print()
        print("⚡ Run from PROJECT ROOT:")
        print("   python app/Scripts/seed.py")
        print("=" * 65 + "\n")

    except Exception as e:
        print(f"\n❌ Seeding failed: {e}")
        import traceback
        traceback.print_exc()

    finally:
        await prisma.disconnect()


if __name__ == "__main__":
    asyncio.run(main())