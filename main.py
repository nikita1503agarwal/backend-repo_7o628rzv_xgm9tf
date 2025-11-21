import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any

import requests
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import (
    User,
    Instance,
    Message,
    Webhook,
    OTPRequest,
    OTPVerify,
    InstanceCreate,
    SendMessage,
    RegisterWebhook,
)

app = FastAPI(title="Sab Tech WhatsApp API Demo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Utilities ----------

def _random_code(length: int = 6) -> str:
    return "".join(secrets.choice(string.digits) for _ in range(length))


def _random_token(length: int = 40) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _collection(name: str):
    return db[name]


def _user_from_token(token: str) -> Optional[Dict[str, Any]]:
    return _collection("user").find_one({"access_tokens": token})


async def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    user = _user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


# ---------- Health ----------

@app.get("/")
def read_root():
    return {"message": "WhatsApp API demo backend is running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name or ""
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:80]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response


# ---------- Auth (OTP) ----------

@app.post("/auth/otp/request")
def request_otp(payload: OTPRequest):
    if not payload.email and not payload.phone:
        raise HTTPException(status_code=400, detail="Provide email or phone")

    identifier = {"email": payload.email} if payload.email else {"phone": payload.phone}
    code = _random_code()
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)

    _collection("user").update_one(
        identifier,
        {
            "$set": {
                **identifier,
                "otp_code": code,
                "otp_expires_at": expires,
            },
            "$setOnInsert": {"name": None, "access_tokens": []},
        },
        upsert=True,
    )

    # In a real app, send via email/SMS. For demo, return code directly.
    return {"message": "OTP generated", "code": code, "expires_at": expires.isoformat()}


@app.post("/auth/otp/verify")
def verify_otp(payload: OTPVerify):
    if not payload.email and not payload.phone:
        raise HTTPException(status_code=400, detail="Provide email or phone")

    identifier = {"email": payload.email} if payload.email else {"phone": payload.phone}
    user = _collection("user").find_one(identifier)
    if not user or not user.get("otp_code"):
        raise HTTPException(status_code=400, detail="OTP not requested")

    if user["otp_code"] != payload.code:
        raise HTTPException(status_code=400, detail="Invalid OTP")

    if user.get("otp_expires_at") and datetime.now(timezone.utc) > user["otp_expires_at"]:
        raise HTTPException(status_code=400, detail="OTP expired")

    token = _random_token()

    _collection("user").update_one(
        {"_id": user["_id"]},
        {
            "$set": {"otp_code": None, "otp_expires_at": None},
            "$push": {"access_tokens": token},
        },
    )

    return {"access_token": token, "token_type": "bearer"}


# ---------- Instances ----------

@app.get("/instances")
def list_instances(current_user: dict = Depends(get_current_user)):
    instances = list(_collection("instance").find({"user_id": str(current_user["_id"])}))
    for i in instances:
        i["_id"] = str(i["_id"])  # stringify for JSON
    return {"items": instances}


@app.post("/instances")
def create_instance(payload: InstanceCreate, current_user: dict = Depends(get_current_user)):
    instance_id = _random_token(10)
    token = _random_token(32)
    doc = Instance(
        user_id=str(current_user["_id"]),
        name=payload.name,
        instance_id=instance_id,
        token=token,
        is_authenticated=False,
    ).model_dump()
    new_id = create_document("instance", doc)
    return {"_id": new_id, "instance_id": instance_id, "token": token, "is_authenticated": False}


@app.post("/instances/{instance_id}/authenticate")
def authenticate_instance(instance_id: str, current_user: dict = Depends(get_current_user)):
    inst = _collection("instance").find_one({"instance_id": instance_id, "user_id": str(current_user["_id"])})
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    _collection("instance").update_one({"_id": inst["_id"]}, {"$set": {"is_authenticated": True}})
    return {"instance_id": instance_id, "is_authenticated": True}


# ---------- Webhooks ----------

@app.post("/webhooks/register")
def register_webhook(payload: RegisterWebhook):
    inst = _collection("instance").find_one({"instance_id": payload.instance_id})
    if not inst or inst.get("token") != payload.token:
        raise HTTPException(status_code=401, detail="Invalid instance credentials")

    doc = Webhook(instance_id=payload.instance_id, url=payload.url, events=payload.events or ["message.status", "message.incoming"]).model_dump()
    new_id = create_document("webhook", doc)
    return {"_id": new_id, "message": "Webhook registered"}


def _emit_webhook(instance_id: str, event: str, data: dict):
    hooks = list(_collection("webhook").find({"instance_id": instance_id, "events": {"$in": [event]}}))
    for h in hooks:
        try:
            requests.post(h["url"], json={"event": event, "data": data}, timeout=3)
        except Exception:
            # Best-effort; ignore
            pass


# ---------- Messages ----------

@app.post("/messages/send")
def send_message(payload: SendMessage):
    inst = _collection("instance").find_one({"instance_id": payload.instance_id})
    if not inst or inst.get("token") != payload.token:
        raise HTTPException(status_code=401, detail="Invalid instance credentials")

    msg_id = _random_token(12)
    status = "sent" if inst.get("is_authenticated") else "failed"
    error = None if status == "sent" else "Instance not authenticated (scan QR first)"

    message_doc = Message(
        instance_id=payload.instance_id,
        to=payload.to,
        type=(payload.type or "text"),
        text=payload.text,
        media_url=payload.media_url,
        interactive=payload.interactive,
        status=status,
        error=error,
        message_id=msg_id,
    ).model_dump()

    create_document("message", message_doc)

    # Simulate delivery progression for authenticated instances
    if status == "sent":
        # queue simple state machine via best-effort webhooks (no async worker here)
        _emit_webhook(payload.instance_id, "message.status", {"message_id": msg_id, "status": "sent"})

    return {"message_id": msg_id, "status": status, "error": error}


@app.get("/messages/{message_id}/status")
def get_message_status(message_id: str):
    msg = _collection("message").find_one({"message_id": message_id})
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    msg["_id"] = str(msg["_id"])  # stringify
    return {"message_id": msg.get("message_id"), "status": msg.get("status"), "error": msg.get("error")}


# ---------- Schema Introspection (optional helper) ----------

@app.get("/schema")
def get_schema():
    # Provide a lightweight introspection of available collections
    return {
        "collections": [
            "user",
            "instance",
            "message",
            "webhook",
        ]
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
