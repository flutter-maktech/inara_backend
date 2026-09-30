-- AlterTable
ALTER TABLE "users" ADD COLUMN     "app_notifications_enabled" BOOLEAN NOT NULL DEFAULT true,
ADD COLUMN     "whatsapp_notifications_enabled" BOOLEAN NOT NULL DEFAULT true;
