"""
Database Schemas for WhatsApp API Demo

Each Pydantic model maps to a MongoDB collection whose name is the lowercase of the class.
- User -> "user"
- Instance -> "instance"
- Message -> "message"
- Webhook -> "webhook"
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Literal, Dict, Any
from datetime import datetime


class User(BaseModel):
    email: Optional[EmailStr] = Field(None, description="Email for OTP login")
    phone: Optional[str] = Field(None, description="Phone number for OTP login (E.164)")
    name: Optional[str] = Field(None, description="Display name")
    otp_code: Optional[str] = Field(None, description="Temporary OTP code (demo only)")
    otp_expires_at: Optional[datetime] = Field(None, description="OTP expiry timestamp")
    access_tokens: List[str] = Field(default_factory=list, description="Active session tokens")


class Instance(BaseModel):
    user_id: str = Field(..., description="Owner user _id as string")
    name: str = Field(..., description="Instance label")
    instance_id: str = Field(..., description="Public instance identifier")
    token: str = Field(..., description="Secret token to authenticate API calls")
    is_authenticated: bool = Field(False, description="Whether device QR has been scanned (simulated)")


class Message(BaseModel):
    instance_id: str = Field(..., description="Owning instance id")
    to: str = Field(..., description="Recipient MSISDN in E.164 format")
    type: Literal["text", "image", "document", "audio", "video", "interactive"] = Field("text")
    text: Optional[str] = Field(None, description="Text body if type=text")
    media_url: Optional[str] = Field(None, description="URL to media if applicable")
    interactive: Optional[Dict[str, Any]] = Field(None, description="Interactive payload for buttons/lists")
    status: Literal["queued", "sent", "delivered", "read", "failed"] = Field("queued")
    error: Optional[str] = None
    message_id: str = Field(..., description="Public message id for tracking")


class Webhook(BaseModel):
    instance_id: str = Field(..., description="Instance this webhook belongs to")
    url: str = Field(..., description="Callback URL")
    events: List[str] = Field(default_factory=lambda: ["message.status", "message.incoming"], description="Subscribed events")


# Lightweight request models for validation (used only in FastAPI routes)
class OTPRequest(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None

class OTPVerify(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    code: str

class InstanceCreate(BaseModel):
    name: str

class SendMessage(BaseModel):
    instance_id: str
    token: str
    to: str
    type: Optional[str] = "text"
    text: Optional[str] = None
    media_url: Optional[str] = None
    interactive: Optional[Dict[str, Any]] = None

class RegisterWebhook(BaseModel):
    instance_id: str
    token: str
    url: str
    events: Optional[List[str]] = None
