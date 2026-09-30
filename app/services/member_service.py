"""
MemberService — Business logic for the Members section.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from io import BytesIO
import secrets

import pandas as pd
from fastapi import HTTPException, status

from app.db.db_client import prisma
from app.core import security
from app.core.email import send_email
from app.core.config import settings
from app.core.notification_service import dispatch_push_notification
from app.models.member_model import (
    MemberStats,
    MemberBrief,
    MembershipBrief,
    MemberListResponse,
    MemberProfile,
    ActiveMembershipDetail,
    ClassBookingRow,
    CourseBookingRow,
    PurchaseHistoryRow,
    SendNotificationRequest,
    SendNotificationResponse,
    UpdateMemberRequest,
    UpdateMemberResponse,
    UpdatedMemberSnapshot,
    InviteMemberRequest,
    MemberInvitationResponse,
    MemberInvitationListResponse,
    AcceptMemberInvitationRequest,
    AcceptMemberInvitationResponse,
)
from prisma.enums import UserRole, MembershipStatus, BookingStatus, InvitationStatus


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Email helpers
# ─────────────────────────────────────────────────────────────────────────────

def _send_member_invitation_email(
    email_to: str,
    invite_token: str,
    inviter_name: str,
    invitee_name: Optional[str],
    base_url: str,
) -> bool:
    """
    Send a member-invitation email.
    The accept link directs the user to a form where they set up their account.
    After completing their details they are directed to install the app.
    """
    subject = f"You're invited to join {settings.APP_NAME}"

    accept_url = (
        f"{base_url.rstrip('/')}/api/v1/members/invitations/accept-form"
        f"?token={invite_token}"
    )

    greeting = f"Hi {invitee_name}," if invitee_name else "Hi there,"

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>You're invited to {settings.APP_NAME}</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f4f7;
             font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background-color:#f4f4f7;padding:40px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0"
               style="background-color:#ffffff;border-radius:8px;overflow:hidden;
                      box-shadow:0 2px 8px rgba(0,0,0,0.08);">

          <!-- HEADER -->
          <tr>
            <td style="background:linear-gradient(135deg,#1a237e,#283593);
                       padding:40px 48px;text-align:center;">
              <h1 style="margin:0;color:#ffffff;font-size:26px;font-weight:700;">
                {settings.APP_NAME}
              </h1>
              <p style="margin:8px 0 0;color:#c5cae9;font-size:14px;">
                Membership Invitation
              </p>
            </td>
          </tr>

          <!-- BODY -->
          <tr>
            <td style="padding:40px 48px;">
              <p style="margin:0 0 8px;font-size:22px;font-weight:600;color:#1a1a2e;">
                {greeting} 👋
              </p>
              <p style="margin:0 0 24px;font-size:15px;color:#555;line-height:1.6;">
                <strong style="color:#1a237e;">{inviter_name}</strong> has invited you
                to become a member of <strong>{settings.APP_NAME}</strong>.
              </p>

              <p style="margin:0 0 16px;font-size:15px;color:#555;line-height:1.6;">
                Click the button below to set up your account — it only takes a minute:
              </p>

              <!-- CTA -->
              <table cellpadding="0" cellspacing="0" style="margin:24px 0;">
                <tr>
                  <td style="border-radius:6px;background-color:#1a237e;">
                    <a href="{accept_url}"
                       style="display:inline-block;padding:14px 36px;color:#ffffff;
                              font-size:15px;font-weight:600;text-decoration:none;
                              border-radius:6px;">
                      Accept Invitation &amp; Get Started →
                    </a>
                  </td>
                </tr>
              </table>

              <hr style="border:none;border-top:1px solid #eeeeee;margin:28px 0;"/>

              <p style="margin:0 0 8px;font-size:13px;color:#888;">
                Or copy and paste this link into your browser:
              </p>
              <p style="margin:0 0 24px;font-size:12px;color:#1a237e;
                        word-break:break-all;background:#e8eaf6;padding:12px 16px;
                        border-radius:4px;border-left:3px solid #283593;">
                {accept_url}
              </p>

              <table cellpadding="0" cellspacing="0" width="100%">
                <tr>
                  <td style="background:#fff8e1;border-left:4px solid #fbc02d;
                             padding:12px 16px;border-radius:0 4px 4px 0;">
                    <p style="margin:0;font-size:13px;color:#5d4037;">
                      ⏰ <strong>This invitation expires in 7 days.</strong>
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- FOOTER -->
          <tr>
            <td style="background:#f9f9f9;padding:20px 48px;text-align:center;
                       border-top:1px solid #eeeeee;">
              <p style="margin:0;font-size:12px;color:#aaa;">
                If you did not expect this invitation, you can safely ignore this email.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    try:
        send_email(email_to=email_to, subject=subject, html_content=html_content)
        return True
    except Exception:
        return False


def _send_member_welcome_notification_email(
    email_to: str,
    name: str,
) -> None:
    """
    Sends a welcome email after the member successfully accepts the invitation
    and completes their account setup.
    Directs them to download the app to book classes and get a membership.
    """
    subject = f"Welcome to {settings.APP_NAME}! Your account is ready 🎉"

    ios_url     = settings.IOS_APP_URL
    android_url = settings.ANDROID_APP_URL

    # QR code pointing to the Android store (universal fallback for scanning)
    qr_url = (
        f"https://api.qrserver.com/v1/create-qr-code/"
        f"?size=160x160&data={android_url}&color=1a237e&bgcolor=ffffff&margin=10"
    )

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#f4f4f7;
             font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:#f4f4f7;padding:40px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border-radius:8px;overflow:hidden;
                      box-shadow:0 2px 8px rgba(0,0,0,0.08);">

          <!-- HEADER -->
          <tr>
            <td style="background:linear-gradient(135deg,#1a237e,#283593);
                       padding:40px 48px;text-align:center;">
              <h1 style="margin:0;color:#fff;font-size:26px;font-weight:700;">
                {settings.APP_NAME}
              </h1>
              <p style="margin:8px 0 0;color:#c5cae9;font-size:14px;">
                Welcome aboard!
              </p>
            </td>
          </tr>

          <!-- BODY -->
          <tr>
            <td style="padding:40px 48px;">
              <p style="font-size:22px;font-weight:600;color:#1a1a2e;margin:0 0 16px;">
                🎉 Welcome to {settings.APP_NAME}, {name}!
              </p>
              <p style="font-size:15px;color:#555;line-height:1.7;margin:0 0 20px;">
                Your account has been created and you're all set to get started.
                Download the <strong>{settings.APP_NAME}</strong> app to book a class,
                explore our membership plans, and find out more about us.
              </p>

              <p style="font-size:15px;color:#555;line-height:1.7;margin:0 0 8px;">
                Thank you for joining <strong>{settings.APP_NAME}</strong>.
                We can't wait to see you on the mat! 🧘
              </p>

              <hr style="border:none;border-top:1px solid #eeeeee;margin:28px 0;"/>

              <!-- APP STORE BUTTONS -->
              <p style="font-size:14px;font-weight:600;color:#1a1a2e;margin:0 0 16px;
                        text-align:center;">
                Download the App
              </p>
              <table cellpadding="0" cellspacing="0" width="100%">
                <tr>
                  <td align="center">
                    <table cellpadding="0" cellspacing="0">
                      <tr>
                        <!-- iOS -->
                        <td style="padding-right:12px;">
                          <a href="{ios_url}"
                             style="display:inline-block;padding:12px 28px;
                                    background:#000000;color:#ffffff;
                                    font-size:14px;font-weight:600;
                                    text-decoration:none;border-radius:8px;
                                    border:1.5px solid #000000;">
                            🍎 App Store (iOS)
                          </a>
                        </td>
                        <!-- Android -->
                        <td>
                          <a href="{android_url}"
                             style="display:inline-block;padding:12px 28px;
                                    background:#1a237e;color:#ffffff;
                                    font-size:14px;font-weight:600;
                                    text-decoration:none;border-radius:8px;
                                    border:1.5px solid #1a237e;">
                            🤖 Google Play
                          </a>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

              <!-- QR CODE -->
              <table cellpadding="0" cellspacing="0" width="100%"
                     style="margin-top:28px;">
                <tr>
                  <td align="center">
                    <p style="font-size:13px;color:#888;margin:0 0 12px;">
                      Or scan this QR code with your phone camera:
                    </p>
                    <img src="{qr_url}" width="140" height="140"
                         alt="Download {settings.APP_NAME}"
                         style="border-radius:8px;border:2px solid #e8eaf6;"/>
                  </td>
                </tr>
              </table>

            </td>
          </tr>

          <!-- FOOTER -->
          <tr>
            <td style="background:#f9f9f9;padding:20px 48px;text-align:center;
                       border-top:1px solid #eeeeee;">
              <p style="margin:0;font-size:12px;color:#aaa;">
                You're receiving this email because you accepted an invitation to
                join {settings.APP_NAME}.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    try:
        send_email(email_to=email_to, subject=subject, html_content=html_content)
    except Exception:
        pass  # Non-fatal — account is already created


class MemberService:
    """
    Enterprise-grade service for the Members section.
    Access control: Only ADMIN / MANAGER can use these endpoints
    (unless noted otherwise).
    """

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    @staticmethod
    async def _require_admin_or_manager(user_id: str) -> None:
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user or user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin or Manager access required"
            )

    @staticmethod
    async def _require_admin(user_id: str) -> None:
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user or user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required"
            )

    @staticmethod
    async def _build_member_brief(user) -> MemberBrief:
        """
        Assemble one row for the Members table.
        Schema v2: Classes and Courses are standalone.
          - Class bookings  → Booking.classId  is set, courseId is NULL
          - Course bookings → Booking.courseId is set, classId  is NULL
        """
        latest_booking = await prisma.booking.find_first(
            where={"userId": user.id},
            order={"bookedAt": "desc"}
        )
        last_booking_at = latest_booking.bookedAt if latest_booking else None

        total_class_bookings = await prisma.booking.count(
            where={"userId": user.id, "classId": {"not": None}}
        )
        total_course_bookings = await prisma.booking.count(
            where={"userId": user.id, "courseId": {"not": None}}
        )

        active_membership = await prisma.membership.find_first(
            where={"userId": user.id, "status": MembershipStatus.ACTIVE},
            order={"createdAt": "desc"}
        )

        membership_brief = None
        if active_membership:
            membership_brief = MembershipBrief(
                name=active_membership.name,
                status=active_membership.status
            )

        return MemberBrief(
            id=user.id,
            name=user.name,
            email=user.email,
            avatar=user.avatar,
            gender=user.gender,
            phone=user.phone,
            dateOfBirth=getattr(user, "dateOfBirth", None),
            joinedAt=user.createdAt,
            lastBookingAt=last_booking_at,
            totalClassBookings=total_class_bookings,
            totalCourseBookings=total_course_bookings,
            activeMembership=membership_brief
        )

    # ─────────────────────────────────────────
    # 1. GET MEMBER STATS
    # ─────────────────────────────────────────

    @staticmethod
    async def get_member_stats(caller_id: str) -> MemberStats:
        """
        Returns the 3 stats cards at the top of the Members page:
          - Total number of members  (all active users with role=USER)
          - Total male members        (gender contains 'male', case-insensitive)
          - Total female members      (gender contains 'female', case-insensitive)

        Definition: "Member" = any registered user with role=USER and isActive=True.
        Membership plan ownership is NOT required — a newly invited user with no plan
        is still a member and must be counted here.

        Gender matching is intentionally case-insensitive so values stored as
        "Male", "male", or "MALE" are all counted correctly.
        """
        await MemberService._require_admin_or_manager(caller_id)

        base_where: Dict[str, Any] = {
            "role": UserRole.USER,
            "isActive": True,
        }

        total = await prisma.user.count(where=base_where)

        # Case-insensitive gender matching covers "Male"/"male"/"MALE" etc.
        male = await prisma.user.count(
            where={
                **base_where,
                "gender": {"contains": "male", "mode": "insensitive"},
                # Exclude "Female" which also contains "male"
                "NOT": [{"gender": {"contains": "female", "mode": "insensitive"}}],
            }
        )
        female = await prisma.user.count(
            where={
                **base_where,
                "gender": {"contains": "female", "mode": "insensitive"},
            }
        )

        return MemberStats(
            totalMembers=total,
            totalMaleMembers=male,
            totalFemaleMembers=female
        )

    # ─────────────────────────────────────────
    # 2. GET ALL MEMBERS (with search & pagination)
    # ─────────────────────────────────────────

    @staticmethod
    async def get_all_members(
        caller_id: str,
        search: Optional[str],
        page: int,
        page_size: int
    ) -> MemberListResponse:
        """
        Returns paginated member rows for the Members table.

        A "member" is any registered user with role=USER and isActive=True.
        Membership plan ownership is NOT required — users who registered via
        invitation but have not yet purchased a plan are still listed here.
        Their activeMembership column will simply be empty.
        """
        await MemberService._require_admin_or_manager(caller_id)

        where_clause: Dict[str, Any] = {
            "role": UserRole.USER,
            "isActive": True,
        }

        if search:
            where_clause["AND"] = [
                {"OR": [
                    {"name":  {"contains": search, "mode": "insensitive"}},
                    {"email": {"contains": search, "mode": "insensitive"}}
                ]}
            ]

        total = await prisma.user.count(where=where_clause)
        skip  = (page - 1) * page_size
        total_pages = max(1, (total + page_size - 1) // page_size)

        users = await prisma.user.find_many(
            where=where_clause,
            skip=skip,
            take=page_size,
            order={"createdAt": "desc"}
        )

        members = [await MemberService._build_member_brief(u) for u in users]

        return MemberListResponse(
            members=members,
            total=total,
            page=page,
            pageSize=page_size,
            totalPages=total_pages
        )

    # ─────────────────────────────────────────
    # 3. GET MEMBER PROFILE BY ID
    # ─────────────────────────────────────────

    @staticmethod
    async def get_member_profile(
        caller_id: str,
        member_id: str,
        classes_page: int = 1,
        classes_page_size: int = 6,
        courses_page: int = 1,
        courses_page_size: int = 6,
        purchases_page: int = 1,
        purchases_page_size: int = 6
    ) -> MemberProfile:
        """Full member profile with sub-tables."""
        await MemberService._require_admin_or_manager(caller_id)

        user = await prisma.user.find_unique(where={"id": member_id})
        if not user or user.role != UserRole.USER:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Member not found"
            )

        # ── Active membership ────────────────────────────────────────────
        active_ms = await prisma.membership.find_first(
            where={"userId": member_id, "status": MembershipStatus.ACTIVE},
            order={"createdAt": "desc"}
        )

        total_class_bookings_count = await prisma.booking.count(
            where={"userId": member_id}
        )

        active_membership_detail = None
        if active_ms:
            active_membership_detail = ActiveMembershipDetail(
                id=active_ms.id,
                name=active_ms.name,
                status=active_ms.status,
                expiresAt=active_ms.endDate,
                totalClassBookings=total_class_bookings_count
            )

        # ── All bookings ─────────────────────────────────────────────────
        all_class_bookings = await prisma.booking.find_many(
            where={"userId": member_id, "classId": {"not": None}},
            include={"classes": {"include": {"instructor": True}}},
            order={"bookedAt": "desc"}
        )

        all_course_bookings = await prisma.booking.find_many(
            where={"userId": member_id, "courseId": {"not": None}},
            include={"course": True},
            order={"bookedAt": "desc"}
        )

        all_bookings = all_class_bookings + all_course_bookings
        last_booking_at = (
            max((b.bookedAt for b in all_bookings), default=None)
        )

        # ── Favourite class ──────────────────────────────────────────────
        class_counter: Dict[str, Dict] = {}
        for b in all_class_bookings:
            if not b.classes:
                continue
            cid = b.classId
            if cid not in class_counter:
                class_counter[cid] = {"name": b.classes.title, "count": 0}
            class_counter[cid]["count"] += 1

        favourite_class = None
        if class_counter:
            top = max(class_counter.values(), key=lambda x: x["count"])
            favourite_class = top["name"]

        # ── Favourite instructor ─────────────────────────────────────────
        instructor_counter: Dict[str, Dict] = {}
        for b in all_class_bookings:
            if not b.classes or not b.classes.instructor:
                continue
            iid = b.classes.instructorId
            if iid not in instructor_counter:
                instructor_counter[iid] = {
                    "name": b.classes.instructor.name,
                    "count": 0
                }
            instructor_counter[iid]["count"] += 1

        favourite_instructor = None
        if instructor_counter:
            top_i = max(instructor_counter.values(), key=lambda x: x["count"])
            favourite_instructor = top_i["name"]

        # ── Classes list (paginated) ─────────────────────────────────────
        classes_agg: Dict[str, Dict] = {}
        for b in all_class_bookings:
            if not b.classes:
                continue
            cid = b.classId
            if cid not in classes_agg:
                classes_agg[cid] = {
                    "classId":     cid,
                    "className":   b.classes.title,
                    "bookingsCount": 0,
                    "lastBookedAt": b.bookedAt,
                }
            classes_agg[cid]["bookingsCount"] += 1
            if b.bookedAt > classes_agg[cid]["lastBookedAt"]:
                classes_agg[cid]["lastBookedAt"] = b.bookedAt

        all_classes_list = [ClassBookingRow(**v) for v in classes_agg.values()]
        classes_total    = len(all_classes_list)
        c_start = (classes_page - 1) * classes_page_size
        c_end   = c_start + classes_page_size
        classes_page_list = all_classes_list[c_start:c_end]

        # ── Courses list (paginated) ─────────────────────────────────────
        courses_agg: Dict[str, Dict] = {}
        for b in all_course_bookings:
            if not b.course:
                continue
            cid = b.courseId
            if cid not in courses_agg:
                courses_agg[cid] = {
                    "courseId":    cid,
                    "courseName":  b.course.title,
                    "bookingsCount": 0,
                    "lastBookedAt": b.bookedAt,
                }
            courses_agg[cid]["bookingsCount"] += 1
            if b.bookedAt > courses_agg[cid]["lastBookedAt"]:
                courses_agg[cid]["lastBookedAt"] = b.bookedAt

        all_courses_list = [CourseBookingRow(**v) for v in courses_agg.values()]
        courses_total    = len(all_courses_list)
        cr_start = (courses_page - 1) * courses_page_size
        cr_end   = cr_start + courses_page_size
        courses_page_list = all_courses_list[cr_start:cr_end]

        # ── Purchase history (paginated) ─────────────────────────────────
        all_orders = await prisma.order.find_many(
            where={"userId": member_id},
            include={"items": {"include": {"product": True}}},
            order={"createdAt": "desc"}
        )

        all_purchases: List[PurchaseHistoryRow] = []
        for order in all_orders:
            for item in order.items:
                all_purchases.append(PurchaseHistoryRow(
                    orderId=order.id,
                    orderNumber=order.orderNumber,
                    productName=item.product.name if item.product else "Unknown",
                    quantity=item.quantity,
                    price=item.price,
                    orderDate=order.createdAt,
                ))

        purchases_total = len(all_purchases)
        p_start = (purchases_page - 1) * purchases_page_size
        p_end   = p_start + purchases_page_size
        purchases_page_list = all_purchases[p_start:p_end]

        return MemberProfile(
            id=user.id,
            name=user.name,
            email=user.email,
            phone=user.phone,
            avatar=user.avatar,
            gender=user.gender,
            bio=getattr(user, "bio", None),
            dateOfBirth=getattr(user, "dateOfBirth", None),
            joinedAt=user.createdAt,
            lastBookingAt=last_booking_at,
            activeMembership=active_membership_detail,
            favouriteClass=favourite_class,
            favouriteInstructor=favourite_instructor,
            classesList=classes_page_list,
            classesTotal=classes_total,
            coursesList=courses_page_list,
            coursesTotal=courses_total,
            purchaseHistory=purchases_page_list,
            purchasesTotal=purchases_total,
        )

    # ─────────────────────────────────────────
    # 4. UPDATE MEMBER PROFILE (Admin / Manager)
    # ─────────────────────────────────────────

    @staticmethod
    async def update_member(
        caller_id: str,
        member_id: str,
        data: UpdateMemberRequest
    ) -> UpdateMemberResponse:
        """Edit a member's profile. Only changed fields are written to DB."""
        await MemberService._require_admin_or_manager(caller_id)

        member = await prisma.user.find_unique(where={"id": member_id})
        if not member or member.role != UserRole.USER:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Member not found"
            )

        update_data = data.model_dump(exclude_unset=True, exclude_none=True)
        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields provided to update"
            )

        updated_member = await prisma.user.update(
            where={"id": member_id},
            data=update_data
        )

        return UpdateMemberResponse(
            message="Member profile updated successfully",
            memberId=member_id,
            updatedFields=list(update_data.keys()),
            member=UpdatedMemberSnapshot(
                id=updated_member.id,
                email=updated_member.email,
                name=updated_member.name,
                phone=updated_member.phone,
                gender=updated_member.gender,
                avatar=updated_member.avatar,
                bio=getattr(updated_member, "bio", None),
                dateOfBirth=getattr(updated_member, "dateOfBirth", None),
                joinedAt=updated_member.createdAt,
            ),
        )

    # ─────────────────────────────────────────
    # 5. DELETE MEMBER (Admin only)
    # ─────────────────────────────────────────

    @staticmethod
    async def delete_member(caller_id: str, member_id: str) -> dict:
        """
        Permanently delete a member's account.
        Only ADMIN can perform this action.

        Cascade behaviour (handled by Prisma / DB):
          - Bookings, Memberships, Orders, Notifications, Reviews,
            Wishlists, CartItems are all user-owned and will be deleted.
        """
        await MemberService._require_admin(caller_id)

        member = await prisma.user.find_unique(where={"id": member_id})
        if not member or member.role != UserRole.USER:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Member not found"
            )

        # Prevent accidental self-deletion
        if caller_id == member_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot delete your own account"
            )

        await prisma.user.delete(where={"id": member_id})
        return {"message": "Member account deleted successfully", "memberId": member_id}

    # ─────────────────────────────────────────
    # 6. SEND NOTIFICATION
    # ─────────────────────────────────────────

    @staticmethod
    async def send_notification(
        caller_id: str,
        data: SendNotificationRequest
    ) -> SendNotificationResponse:
        """
        Send a push notification to all members (or a selected subset).

        Channel is resolved from data.channel:
          • "app"       — In-app notification (DB record, mobile push).
          • "whatsapp"  — WhatsApp Business Cloud API message.
          • "both"      — Both channels simultaneously.

        User-level preferences (appNotificationsEnabled,
        whatsappNotificationsEnabled) are respected — members who have
        opted out of a channel are counted in the skipped totals rather than
        skipped silently.
        """
        await MemberService._require_admin_or_manager(caller_id)

        # Fetch target users
        if data.memberIds:
            users = await prisma.user.find_many(
                where={"id": {"in": data.memberIds}, "role": UserRole.USER}
            )
        else:
            users = await prisma.user.find_many(
                where={"role": UserRole.USER, "isActive": True}
            )

        if not users:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No members found to notify"
            )

        # Normalise the channel string into boolean flags
        channel = data.channel.lower().strip()
        use_app       = channel in ("app", "both")
        use_whatsapp  = channel in ("whatsapp", "both")

        # Fall back to a sensible default title when the caller omits one
        notification_title = data.title or f"Notification from {settings.APP_NAME}"

        result = await dispatch_push_notification(
            users=users,
            title=notification_title,
            message=data.message,
            notification_type=data.notificationType,
            send_via_app=use_app,
            send_via_whatsapp=use_whatsapp,
        )

        return SendNotificationResponse(
            message="Notifications dispatched successfully",
            sentTo=len(users),
            channel=channel,
            sentViaApp=result["sentViaApp"],
            sentViaWhatsApp=result["sentViaWhatsApp"],
            skippedApp=result["skippedApp"],
            skippedWhatsApp=result["skippedWhatsApp"],
            failed=result["failed"],
        )

    # ─────────────────────────────────────────
    # 7. EXPORT MEMBERS TO EXCEL
    # ─────────────────────────────────────────

    @staticmethod
    async def export_members_to_excel(
        caller_id: str,
        search: Optional[str] = None
    ) -> BytesIO:
        """
        Exports the full members list to Excel.
        Includes ALL registered users with role=USER — regardless of whether
        they hold an active membership plan.
        """
        await MemberService._require_admin_or_manager(caller_id)

        where_clause: Dict[str, Any] = {
            "role": UserRole.USER,
            "isActive": True,
        }
        if search:
            where_clause["AND"] = [
                {"OR": [
                    {"name":  {"contains": search, "mode": "insensitive"}},
                    {"email": {"contains": search, "mode": "insensitive"}}
                ]}
            ]

        users = await prisma.user.find_many(
            where=where_clause,
            order={"createdAt": "desc"}
        )

        export_data = []
        for user in users:
            active_ms = await prisma.membership.find_first(
                where={"userId": user.id, "status": MembershipStatus.ACTIVE},
                order={"createdAt": "desc"}
            )
            total_bookings = await prisma.booking.count(where={"userId": user.id})
            export_data.append({
                "Name":               user.name,
                "Email":              user.email,
                "Phone":              user.phone or "N/A",
                "Gender":             user.gender or "N/A",
                "Date of Birth":      getattr(user, "dateOfBirth", None) or "N/A",
                "Active Membership":  active_ms.name if active_ms else "None",
                "Total Bookings":     total_bookings,
                "Joined":             user.createdAt.strftime("%Y-%m-%d"),
            })

        df = pd.DataFrame(export_data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Members", index=False)
            worksheet = writer.sheets["Members"]
            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).apply(len).max(), len(col)) + 2
                col_letter = chr(65 + idx)
                worksheet.column_dimensions[col_letter].width = min(max_len, 50)
        output.seek(0)
        return output

    # ─────────────────────────────────────────
    # 8. EXPORT MEMBER CLASSES TO EXCEL
    # ─────────────────────────────────────────

    @staticmethod
    async def export_member_classes_to_excel(
        caller_id: str,
        member_id: str
    ) -> BytesIO:
        """Exports the class list from a member's profile to Excel."""
        await MemberService._require_admin_or_manager(caller_id)

        user = await prisma.user.find_unique(where={"id": member_id})
        if not user or user.role != UserRole.USER:
            raise HTTPException(status_code=404, detail="Member not found")

        bookings = await prisma.booking.find_many(
            where={"userId": member_id},
            include={"classes": True},
            order={"bookedAt": "desc"}
        )

        classes_agg: Dict[str, Dict] = {}
        for b in bookings:
            if not b.classes:
                continue
            cid = b.classId
            if cid not in classes_agg:
                classes_agg[cid] = {
                    "Class Name": b.classes.title,
                    "Bookings":   0,
                    "Last Booked": b.bookedAt.strftime("%Y-%m-%d")
                }
            classes_agg[cid]["Bookings"] += 1

        df = pd.DataFrame(list(classes_agg.values()))
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Member Classes", index=False)
            worksheet = writer.sheets["Member Classes"]
            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).apply(len).max(), len(col)) + 2
                worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 50)
        output.seek(0)
        return output

    # ─────────────────────────────────────────
    # 9. EXPORT MEMBER COURSES TO EXCEL
    # ─────────────────────────────────────────

    @staticmethod
    async def export_member_courses_to_excel(
        caller_id: str,
        member_id: str
    ) -> BytesIO:
        """Exports the courses list from a member's profile to Excel."""
        await MemberService._require_admin_or_manager(caller_id)

        user = await prisma.user.find_unique(where={"id": member_id})
        if not user or user.role != UserRole.USER:
            raise HTTPException(status_code=404, detail="Member not found")

        bookings = await prisma.booking.find_many(
            where={"userId": member_id, "courseId": {"not": None}},
            include={"course": True},
            order={"bookedAt": "desc"}
        )

        courses_agg: Dict[str, Dict] = {}
        for b in bookings:
            if not b.course:
                continue
            cid = b.courseId
            if cid not in courses_agg:
                courses_agg[cid] = {
                    "Course Name": b.course.title,
                    "Bookings":    0,
                    "Last Booked": b.bookedAt.strftime("%Y-%m-%d")
                }
            courses_agg[cid]["Bookings"] += 1

        df = pd.DataFrame(list(courses_agg.values()))
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Member Courses", index=False)
            worksheet = writer.sheets["Member Courses"]
            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).apply(len).max(), len(col)) + 2
                worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 50)
        output.seek(0)
        return output

    # ─────────────────────────────────────────
    # 10. EXPORT MEMBER PURCHASES TO EXCEL
    # ─────────────────────────────────────────

    @staticmethod
    async def export_member_purchases_to_excel(
        caller_id: str,
        member_id: str
    ) -> BytesIO:
        """Exports the purchase history from a member's profile to Excel."""
        await MemberService._require_admin_or_manager(caller_id)

        user = await prisma.user.find_unique(where={"id": member_id})
        if not user or user.role != UserRole.USER:
            raise HTTPException(status_code=404, detail="Member not found")

        orders = await prisma.order.find_many(
            where={"userId": member_id},
            include={"items": {"include": {"product": True}}},
            order={"createdAt": "desc"}
        )

        rows = []
        for order in orders:
            for item in order.items:
                rows.append({
                    "Order Number": order.orderNumber,
                    "Product Name": item.product.name if item.product else "Unknown",
                    "Quantity":     item.quantity,
                    "Price (QAR)":  item.price,
                    "Total (QAR)":  item.total,
                    "Order Date":   order.createdAt.strftime("%Y-%m-%d"),
                    "Status":       order.status
                })

        df = pd.DataFrame(rows)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Purchase History", index=False)
            worksheet = writer.sheets["Purchase History"]
            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).apply(len).max(), len(col)) + 2
                worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 50)
        output.seek(0)
        return output

    # ═════════════════════════════════════════
    # MEMBER INVITATION FLOW
    # ═════════════════════════════════════════

    # ─────────────────────────────────────────
    # 11. INVITE MEMBER
    # ─────────────────────────────────────────

    @staticmethod
    async def invite_member(
        caller_id: str,
        data: InviteMemberRequest,
        base_url: str
    ) -> MemberInvitationResponse:
        """
        Admin sends a member invitation email.
        Creates a MemberInvitation record and emails the invitee.
        """
        await MemberService._require_admin_or_manager(caller_id)

        # Block re-inviting an existing active user
        existing_user = await prisma.user.find_unique(where={"email": data.email})
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A user with this email already exists"
            )

        # Cancel any older PENDING invitation for the same email
        # so there is never more than one active invitation per address
        old_invite = await prisma.memberinvitation.find_first(
            where={"email": data.email, "status": InvitationStatus.PENDING}
        )
        if old_invite:
            await prisma.memberinvitation.update(
                where={"id": old_invite.id},
                data={"status": InvitationStatus.CANCELLED}
            )

        invite_token = secrets.token_urlsafe(32)
        expires_at   = _now_utc() + timedelta(days=7)

        caller = await prisma.user.find_unique(where={"id": caller_id})
        inviter_name = caller.name if caller else "Admin"

        invitation = await prisma.memberinvitation.create(
            data={
                "email":       data.email,
                "name":        data.name,
                "inviteToken": invite_token,
                "status":      InvitationStatus.PENDING,
                "invitedBy":   caller_id,
                "expiresAt":   expires_at,
            }
        )

        _send_member_invitation_email(
            email_to=data.email,
            invite_token=invite_token,
            inviter_name=inviter_name,
            invitee_name=data.name,
            base_url=base_url,
        )

        return MemberInvitationResponse(
            id=invitation.id,
            email=invitation.email,
            name=invitation.name,
            status=invitation.status,
            inviteToken=invitation.inviteToken,
            invitedBy=caller_id,
            invitedByName=inviter_name,
            expiresAt=invitation.expiresAt,
            acceptedAt=invitation.acceptedAt,
            createdAt=invitation.createdAt,
        )

    # ─────────────────────────────────────────
    # 12. GET ALL MEMBER INVITATIONS
    # ─────────────────────────────────────────

    @staticmethod
    async def get_member_invitations(
        caller_id: str,
        page: int,
        page_size: int,
        status_filter: Optional[str]
    ) -> MemberInvitationListResponse:
        """Returns paginated list of member invitations."""
        await MemberService._require_admin_or_manager(caller_id)

        where: Dict[str, Any] = {}
        if status_filter:
            try:
                where["status"] = InvitationStatus[status_filter.upper()]
            except KeyError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid status filter: {status_filter}"
                )

        total = await prisma.memberinvitation.count(where=where)
        skip  = (page - 1) * page_size
        total_pages = max(1, (total + page_size - 1) // page_size)

        invitations = await prisma.memberinvitation.find_many(
            where=where,
            include={"invitedByUser": True},
            skip=skip,
            take=page_size,
            order={"createdAt": "desc"}
        )

        result = [
            MemberInvitationResponse(
                id=inv.id,
                email=inv.email,
                name=inv.name,
                status=inv.status,
                inviteToken=inv.inviteToken,
                invitedBy=inv.invitedBy,
                invitedByName=inv.invitedByUser.name if inv.invitedByUser else None,
                expiresAt=inv.expiresAt,
                acceptedAt=inv.acceptedAt,
                createdAt=inv.createdAt,
            )
            for inv in invitations
        ]

        return MemberInvitationListResponse(
            invitations=result,
            total=total,
            page=page,
            pageSize=page_size,
            totalPages=total_pages
        )

    # ─────────────────────────────────────────
    # 13. RESEND MEMBER INVITATION
    # ─────────────────────────────────────────

    @staticmethod
    async def resend_member_invitation(
        caller_id: str,
        invitation_id: str,
        base_url: str
    ) -> MemberInvitationResponse:
        """
        Resend the invitation email.
        If the invitation has expired, it is refreshed with a new token
        and a new 7-day expiry window.
        """
        await MemberService._require_admin_or_manager(caller_id)

        invitation = await prisma.memberinvitation.find_unique(
            where={"id": invitation_id},
            include={"invitedByUser": True}
        )
        if not invitation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found"
            )
        if invitation.status == InvitationStatus.ACCEPTED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invitation already accepted"
            )
        if invitation.status == InvitationStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invitation is cancelled and cannot be resent"
            )

        # If expired, rotate the token and reset expiry
        if invitation.expiresAt < _now_utc() or invitation.status == InvitationStatus.EXPIRED:
            new_token  = secrets.token_urlsafe(32)
            new_expiry = _now_utc() + timedelta(days=7)
            invitation = await prisma.memberinvitation.update(
                where={"id": invitation_id},
                data={
                    "inviteToken": new_token,
                    "expiresAt":   new_expiry,
                    "status":      InvitationStatus.PENDING,
                },
                include={"invitedByUser": True}
            )

        caller = await prisma.user.find_unique(where={"id": caller_id})
        inviter_name = caller.name if caller else "Admin"

        _send_member_invitation_email(
            email_to=invitation.email,
            invite_token=invitation.inviteToken,
            inviter_name=inviter_name,
            invitee_name=invitation.name,
            base_url=base_url,
        )

        return MemberInvitationResponse(
            id=invitation.id,
            email=invitation.email,
            name=invitation.name,
            status=invitation.status,
            inviteToken=invitation.inviteToken,
            invitedBy=invitation.invitedBy,
            invitedByName=inviter_name,
            expiresAt=invitation.expiresAt,
            acceptedAt=invitation.acceptedAt,
            createdAt=invitation.createdAt,
        )

    # ─────────────────────────────────────────
    # 14. ACCEPT MEMBER INVITATION
    # ─────────────────────────────────────────

    @staticmethod
    async def accept_member_invitation(
        data: AcceptMemberInvitationRequest
    ) -> AcceptMemberInvitationResponse:
        """
        PUBLIC endpoint — secured by invitation token only.

        Revised workflow (Invite → Accept → Complete Details → Done):
          1. Validate the invitation token.
          2. Ensure the invited email has no existing account.
          3. Create the User account (role = USER, isVerified = True).
          4. Mark the invitation as ACCEPTED.
          5. Send a welcome email with app store links and QR code.

        Membership selection is handled inside the mobile app — not during
        the web-based acceptance flow.
        """
        invitation = await prisma.memberinvitation.find_unique(
            where={"inviteToken": data.inviteToken}
        )
        if not invitation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invalid invitation token"
            )
        if invitation.status != InvitationStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invitation is already {invitation.status.lower()}"
            )
        if invitation.expiresAt < _now_utc():
            await prisma.memberinvitation.update(
                where={"id": invitation.id},
                data={"status": InvitationStatus.EXPIRED}
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invitation has expired. Please ask the admin to resend it."
            )

        # Guard: no duplicate accounts
        existing_user = await prisma.user.find_unique(
            where={"email": invitation.email}
        )
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An account with this email already exists"
            )

        # Create the user account — verified immediately (no OTP needed)
        password_hash = security.get_password_hash(data.password)

        # Build the create payload; only include gender when the user supplied it
        user_data: Dict[str, Any] = {
            "email":        invitation.email,
            "passwordHash": password_hash,
            "name":         data.name,
            "phone":        data.phone,
            "role":         UserRole.USER,
            "isActive":     True,
            "isVerified":   True,
        }
        if data.gender:
            user_data["gender"] = data.gender

        user = await prisma.user.create(data=user_data)

        # Mark invitation as accepted
        await prisma.memberinvitation.update(
            where={"id": invitation.id},
            data={
                "status":     InvitationStatus.ACCEPTED,
                "acceptedAt": _now_utc(),
            }
        )

        # Send welcome email with app store links and QR code (non-fatal)
        _send_member_welcome_notification_email(
            email_to=user.email,
            name=user.name,
        )

        return AcceptMemberInvitationResponse(
            message=(
                f"Thank you for joining {settings.APP_NAME}! "
                "Download our app to book a class and find out more about us."
            ),
            userId=user.id,
            email=user.email,
            name=user.name,
            iosAppUrl=settings.IOS_APP_URL,
            androidAppUrl=settings.ANDROID_APP_URL,
        )

    # ─────────────────────────────────────────
    # 15. CANCEL MEMBER INVITATION
    # ─────────────────────────────────────────

    @staticmethod
    async def cancel_member_invitation(
        caller_id: str,
        invitation_id: str
    ) -> dict:
        """Admin cancels a pending member invitation."""
        await MemberService._require_admin_or_manager(caller_id)

        invitation = await prisma.memberinvitation.find_unique(
            where={"id": invitation_id}
        )
        if not invitation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invitation not found"
            )
        if invitation.status != InvitationStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot cancel an invitation with status '{invitation.status}'"
            )

        await prisma.memberinvitation.update(
            where={"id": invitation_id},
            data={"status": InvitationStatus.CANCELLED}
        )

        return {
            "message": "Member invitation cancelled successfully",
            "invitationId": invitation_id,
            "email": invitation.email,
        }