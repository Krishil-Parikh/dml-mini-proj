from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional, List
import pymongo, hashlib, jwt, uuid, httpx, json
import os
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

SECRET_KEY = "nexabank-secret-key-change-in-prod"
ALGORITHM = "HS256"
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "http://localhost")
OPENROUTER_APP_NAME = os.getenv("OPENROUTER_APP_NAME", "NexaBank")

# ── DB ──────────────────────────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
mongo_client = pymongo.MongoClient(MONGO_URI)
db = mongo_client["nexabank"]

def init_db():
    users = db.users
    accounts = db.accounts
    
    # Create indexes for uniqueness
    users.create_index("email", unique=True)
    accounts.create_index("account_number", unique=True)

    # Seed admin
    admin = users.find_one({"email": "admin@nexabank.com"})
    if not admin:
        admin_id = str(uuid.uuid4())
        ph = hashlib.sha256("admin123".encode()).hexdigest()
        users.insert_one({
            "id": admin_id, "full_name": "Admin", "email": "admin@nexabank.com",
            "password_hash": ph, "role": "ADMIN", "created_at": datetime.now().isoformat()
        })

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="NexaBank API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
bearer = HTTPBearer(auto_error=False)

# ── Auth helpers ─────────────────────────────────────────────────────
def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()
def make_token(user_id, role):
    payload = {"sub": user_id, "role": role, "exp": datetime.utcnow() + timedelta(hours=24)}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        data = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return data
    except:
        raise HTTPException(status_code=401, detail="Invalid token")

def gen_account_number():
    return "4000" + str(uuid.uuid4().int)[:6]

def doc_to_dict(doc):
    if doc and '_id' in doc:
        del doc['_id']
    return doc

# ── Schemas ──────────────────────────────────────────────────────────
class RegisterReq(BaseModel):
    full_name: str
    email: str
    password: str

class LoginReq(BaseModel):
    email: str
    password: str

class CreateAccountReq(BaseModel):
    account_type: str  # SAVINGS | CURRENT | FIXED_DEPOSIT

class TransferReq(BaseModel):
    from_account: str
    to_account: str
    amount: float
    description: str = "Fund Transfer"

class LoanReq(BaseModel):
    loan_type: str
    amount: float
    tenure_months: int

class ChatReq(BaseModel):
    message: str
    account_id: Optional[str] = None

# ── Auth endpoints ────────────────────────────────────────────────────
@app.post("/api/auth/register")
def register(req: RegisterReq):
    uid = str(uuid.uuid4())
    try:
        db.users.insert_one({
            "id": uid, "full_name": req.full_name, "email": req.email, 
            "password_hash": hash_pw(req.password), "role": "CUSTOMER", 
            "created_at": datetime.now().isoformat()
        })
    except pymongo.errors.DuplicateKeyError:
        raise HTTPException(400, "Email already registered")
    return {"message": "Registered successfully"}

@app.post("/api/auth/login")
def login(req: LoginReq):
    user = db.users.find_one({"email": req.email, "password_hash": hash_pw(req.password)})
    if not user:
        raise HTTPException(401, "Invalid credentials")
    token = make_token(user["id"], user["role"])
    return {"token": token, "role": user["role"], "name": user["full_name"]}

# ── Account endpoints ─────────────────────────────────────────────────
@app.post("/api/accounts/create")
def create_account(req: CreateAccountReq, user=Depends(current_user)):
    aid = str(uuid.uuid4())
    acc_no = gen_account_number()
    initial = 10000.0 if req.account_type == "SAVINGS" else 25000.0
    
    db.accounts.insert_one({
        "id": aid, "user_id": user["sub"], "account_number": acc_no, 
        "account_type": req.account_type, "balance": initial, "status": "ACTIVE"
    })
    db.transactions.insert_one({
        "id": str(uuid.uuid4()), "account_id": aid, "type": "CREDIT", 
        "amount": initial, "description": "Account Opening Bonus", 
        "balance_after": initial, "created_at": datetime.now().isoformat()
    })
    return {"account_number": acc_no, "balance": initial, "type": req.account_type}

@app.get("/api/accounts/my")
def my_accounts(user=Depends(current_user)):
    rows = list(db.accounts.find({"user_id": user["sub"]}))
    return [doc_to_dict(r) for r in rows]

@app.get("/api/accounts/{acc_no}/balance")
def balance(acc_no: str, user=Depends(current_user)):
    row = db.accounts.find_one({"account_number": acc_no})
    if not row:
        raise HTTPException(404, "Account not found")
    if row["user_id"] != user["sub"] and user["role"] != "ADMIN":
        raise HTTPException(403, "Access denied")
    return {"balance": row["balance"], "account_number": acc_no}

# ── Transaction endpoints ─────────────────────────────────────────────
@app.post("/api/transactions/transfer")
def transfer(req: TransferReq, user=Depends(current_user)):
    if req.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    
    sender = db.accounts.find_one({"account_number": req.from_account})
    receiver = db.accounts.find_one({"account_number": req.to_account})
    
    if not sender or not receiver:
        raise HTTPException(404, "Account not found")
    if sender["user_id"] != user["sub"]:
        raise HTTPException(403, "Not your account")
    if sender["balance"] < req.amount:
        raise HTTPException(400, "Insufficient funds")
        
    new_sender_bal = sender["balance"] - req.amount
    new_receiver_bal = receiver["balance"] + req.amount
    now = datetime.now().isoformat()
    ref = str(uuid.uuid4())[:8].upper()
    
    db.accounts.update_one({"id": sender["id"]}, {"$set": {"balance": new_sender_bal}})
    db.accounts.update_one({"id": receiver["id"]}, {"$set": {"balance": new_receiver_bal}})
    
    db.transactions.insert_one({
        "id": str(uuid.uuid4()), "account_id": sender["id"], "type": "DEBIT", 
        "amount": req.amount, "description": f"Transfer to {req.to_account} | {req.description}", 
        "balance_after": new_sender_bal, "created_at": now
    })
    db.transactions.insert_one({
        "id": str(uuid.uuid4()), "account_id": receiver["id"], "type": "CREDIT", 
        "amount": req.amount, "description": f"Transfer from {req.from_account}", 
        "balance_after": new_receiver_bal, "created_at": now
    })
    
    return {"reference": ref, "amount": req.amount, "new_balance": new_sender_bal}

@app.get("/api/transactions/history/{acc_no}")
def history(acc_no: str, user=Depends(current_user)):
    acc = db.accounts.find_one({"account_number": acc_no})
    if not acc:
        raise HTTPException(404, "Account not found")
    if acc["user_id"] != user["sub"] and user["role"] != "ADMIN":
        raise HTTPException(403, "Access denied")
        
    txns = list(db.transactions.find({"account_id": acc["id"]}).sort("created_at", -1).limit(20))
    return [doc_to_dict(t) for t in txns]

# ── Loan endpoints ────────────────────────────────────────────────────
@app.post("/api/loans/apply")
def apply_loan(req: LoanReq, user=Depends(current_user)):
    rate_map = {"PERSONAL": 12.5, "HOME": 8.5, "VEHICLE": 10.0, "EDUCATION": 9.0}
    rate = rate_map.get(req.loan_type, 12.0)
    r = rate / 12 / 100
    emi = req.amount * r * (1 + r)**req.tenure_months / ((1 + r)**req.tenure_months - 1)
    lid = str(uuid.uuid4())
    
    db.loans.insert_one({
        "id": lid, "user_id": user["sub"], "loan_type": req.loan_type, 
        "amount": req.amount, "interest_rate": rate, "tenure_months": req.tenure_months, 
        "emi": round(emi, 2), "status": "PENDING", "applied_at": datetime.now().isoformat()
    })
    return {"loan_id": lid, "emi": round(emi, 2), "interest_rate": rate, "status": "PENDING"}

@app.get("/api/loans/my")
def my_loans(user=Depends(current_user)):
    rows = list(db.loans.find({"user_id": user["sub"]}).sort("applied_at", -1))
    return [doc_to_dict(r) for r in rows]

# ── Admin endpoints ───────────────────────────────────────────────────
@app.get("/api/admin/users")
def admin_users(user=Depends(current_user)):
    if user["role"] != "ADMIN":
        raise HTTPException(403, "Admin only")
    users = list(db.users.find({}, {"_id": 0, "password_hash": 0}))
    return users

@app.patch("/api/admin/loans/{loan_id}/status")
def update_loan(loan_id: str, body: dict, user=Depends(current_user)):
    if user["role"] != "ADMIN":
        raise HTTPException(403, "Admin only")
    db.loans.update_one({"id": loan_id}, {"$set": {"status": body["status"]}})
    return {"message": "Updated"}

@app.get("/api/admin/loans")
def admin_loans(user=Depends(current_user)):
    if user["role"] != "ADMIN":
        raise HTTPException(403, "Admin only")
        
    pipeline = [
        {"$lookup": {
            "from": "users",
            "localField": "user_id",
            "foreignField": "id",
            "as": "user_info"
        }},
        {"$unwind": "$user_info"},
        {"$addFields": {"full_name": "$user_info.full_name"}},
        {"$project": {"user_info": 0, "_id": 0}},
        {"$sort": {"applied_at": -1}}
    ]
    rows = list(db.loans.aggregate(pipeline))
    return rows

# ── AI Chat (OpenRouter) ──────────────────────────────────────────────
@app.post("/api/chat")
async def chat(req: ChatReq, user=Depends(current_user)):
    if not OPENROUTER_API_KEY:
        raise HTTPException(503, "AI is not configured. Set OPENROUTER_API_KEY.")

    context = ""
    if req.account_id:
        acc = db.accounts.find_one({"id": req.account_id, "user_id": user["sub"]})
        if acc:
            txns = list(db.transactions.find({"account_id": acc["id"]}).sort("created_at", -1).limit(5))
            context = f"Account: {acc['account_number']}, Balance: ₹{acc['balance']:.2f}, Type: {acc['account_type']}. Recent transactions: {json.dumps([doc_to_dict(t) for t in txns])}"

    system = f"""You are NexaBot, a helpful banking assistant for NexaBank.
Be concise and friendly. Answer banking questions, help with transactions, explain products.
{f'User context: {context}' if context else ''}
Keep responses under 3 sentences unless detail is needed."""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{OPENROUTER_BASE_URL}/chat/completions", headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": OPENROUTER_SITE_URL,
                "X-Title": OPENROUTER_APP_NAME
            }, json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": req.message}
                ],
                "stream": False,
                "temperature": 0.4
            })
            resp.raise_for_status()
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                raise HTTPException(502, "AI provider returned an empty response")
            return {"reply": content}
    except HTTPException:
        raise
    except Exception:
        return {"reply": "AI assistant is temporarily unavailable. Please try again later."}

@app.get("/actuator/health")
def health():
    return {"status": "UP", "timestamp": datetime.now().isoformat()}

from fastapi.responses import FileResponse
import os

@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    frontend_dir = os.path.join(os.path.dirname(__file__), "../frontend")
    file_path = os.path.join(frontend_dir, full_path)
    
    if full_path and os.path.isfile(file_path):
        return FileResponse(file_path)
    
    return FileResponse(os.path.join(frontend_dir, "index.html"))
