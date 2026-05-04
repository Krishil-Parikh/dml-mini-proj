from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional, List
import sqlite3, hashlib, jwt, uuid, httpx, json
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
def get_db():
    conn = sqlite3.connect("nexabank.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, full_name TEXT, email TEXT UNIQUE,
            password_hash TEXT, role TEXT DEFAULT 'CUSTOMER', created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY, user_id TEXT, account_number TEXT UNIQUE,
            account_type TEXT, balance REAL DEFAULT 0, status TEXT DEFAULT 'ACTIVE',
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY, account_id TEXT, type TEXT, amount REAL,
            description TEXT, balance_after REAL, created_at TEXT,
            FOREIGN KEY(account_id) REFERENCES accounts(id)
        );
        CREATE TABLE IF NOT EXISTS loans (
            id TEXT PRIMARY KEY, user_id TEXT, loan_type TEXT, amount REAL,
            interest_rate REAL, tenure_months INTEGER, emi REAL,
            status TEXT DEFAULT 'PENDING', applied_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
    """)
    # Seed admin
    admin_id = str(uuid.uuid4())
    ph = hashlib.sha256("admin123".encode()).hexdigest()
    try:
        db.execute("INSERT INTO users VALUES (?,?,?,?,?,?)",
                   (admin_id, "Admin", "admin@nexabank.com", ph, "ADMIN", datetime.now().isoformat()))
        db.commit()
    except:
        pass
    db.close()

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
    db = get_db()
    uid = str(uuid.uuid4())
    try:
        db.execute("INSERT INTO users VALUES (?,?,?,?,?,?)",
                   (uid, req.full_name, req.email, hash_pw(req.password), "CUSTOMER", datetime.now().isoformat()))
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Email already registered")
    finally:
        db.close()
    return {"message": "Registered successfully"}

@app.post("/api/auth/login")
def login(req: LoginReq):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email=? AND password_hash=?",
                       (req.email, hash_pw(req.password))).fetchone()
    db.close()
    if not user:
        raise HTTPException(401, "Invalid credentials")
    token = make_token(user["id"], user["role"])
    return {"token": token, "role": user["role"], "name": user["full_name"]}

# ── Account endpoints ─────────────────────────────────────────────────
@app.post("/api/accounts/create")
def create_account(req: CreateAccountReq, user=Depends(current_user)):
    db = get_db()
    aid = str(uuid.uuid4())
    acc_no = gen_account_number()
    initial = 10000.0 if req.account_type == "SAVINGS" else 25000.0
    db.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?)",
               (aid, user["sub"], acc_no, req.account_type, initial, "ACTIVE"))
    db.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?)",
               (str(uuid.uuid4()), aid, "CREDIT", initial, "Account Opening Bonus", initial, datetime.now().isoformat()))
    db.commit()
    db.close()
    return {"account_number": acc_no, "balance": initial, "type": req.account_type}

@app.get("/api/accounts/my")
def my_accounts(user=Depends(current_user)):
    db = get_db()
    rows = db.execute("SELECT * FROM accounts WHERE user_id=?", (user["sub"],)).fetchall()
    db.close()
    return [dict(r) for r in rows]

@app.get("/api/accounts/{acc_no}/balance")
def balance(acc_no: str, user=Depends(current_user)):
    db = get_db()
    row = db.execute("SELECT * FROM accounts WHERE account_number=?", (acc_no,)).fetchone()
    db.close()
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
    db = get_db()
    sender = db.execute("SELECT * FROM accounts WHERE account_number=?", (req.from_account,)).fetchone()
    receiver = db.execute("SELECT * FROM accounts WHERE account_number=?", (req.to_account,)).fetchone()
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
    db.execute("UPDATE accounts SET balance=? WHERE id=?", (new_sender_bal, sender["id"]))
    db.execute("UPDATE accounts SET balance=? WHERE id=?", (new_receiver_bal, receiver["id"]))
    db.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?)",
               (str(uuid.uuid4()), sender["id"], "DEBIT", req.amount, f"Transfer to {req.to_account} | {req.description}", new_sender_bal, now))
    db.execute("INSERT INTO transactions VALUES (?,?,?,?,?,?,?)",
               (str(uuid.uuid4()), receiver["id"], "CREDIT", req.amount, f"Transfer from {req.from_account}", new_receiver_bal, now))
    db.commit()
    db.close()
    return {"reference": ref, "amount": req.amount, "new_balance": new_sender_bal}

@app.get("/api/transactions/history/{acc_no}")
def history(acc_no: str, user=Depends(current_user)):
    db = get_db()
    acc = db.execute("SELECT * FROM accounts WHERE account_number=?", (acc_no,)).fetchone()
    if not acc:
        raise HTTPException(404, "Account not found")
    if acc["user_id"] != user["sub"] and user["role"] != "ADMIN":
        raise HTTPException(403, "Access denied")
    txns = db.execute("SELECT * FROM transactions WHERE account_id=? ORDER BY created_at DESC LIMIT 20",
                       (acc["id"],)).fetchall()
    db.close()
    return [dict(t) for t in txns]

# ── Loan endpoints ────────────────────────────────────────────────────
@app.post("/api/loans/apply")
def apply_loan(req: LoanReq, user=Depends(current_user)):
    rate_map = {"PERSONAL": 12.5, "HOME": 8.5, "VEHICLE": 10.0, "EDUCATION": 9.0}
    rate = rate_map.get(req.loan_type, 12.0)
    r = rate / 12 / 100
    emi = req.amount * r * (1 + r)**req.tenure_months / ((1 + r)**req.tenure_months - 1)
    lid = str(uuid.uuid4())
    db = get_db()
    db.execute("INSERT INTO loans VALUES (?,?,?,?,?,?,?,?,?)",
               (lid, user["sub"], req.loan_type, req.amount, rate, req.tenure_months, round(emi, 2), "PENDING", datetime.now().isoformat()))
    db.commit()
    db.close()
    return {"loan_id": lid, "emi": round(emi, 2), "interest_rate": rate, "status": "PENDING"}

@app.get("/api/loans/my")
def my_loans(user=Depends(current_user)):
    db = get_db()
    rows = db.execute("SELECT * FROM loans WHERE user_id=? ORDER BY applied_at DESC", (user["sub"],)).fetchall()
    db.close()
    return [dict(r) for r in rows]

# ── Admin endpoints ───────────────────────────────────────────────────
@app.get("/api/admin/users")
def admin_users(user=Depends(current_user)):
    if user["role"] != "ADMIN":
        raise HTTPException(403, "Admin only")
    db = get_db()
    users = db.execute("SELECT id, full_name, email, role, created_at FROM users").fetchall()
    db.close()
    return [dict(u) for u in users]

@app.patch("/api/admin/loans/{loan_id}/status")
def update_loan(loan_id: str, body: dict, user=Depends(current_user)):
    if user["role"] != "ADMIN":
        raise HTTPException(403, "Admin only")
    db = get_db()
    db.execute("UPDATE loans SET status=? WHERE id=?", (body["status"], loan_id))
    db.commit()
    db.close()
    return {"message": "Updated"}

@app.get("/api/admin/loans")
def admin_loans(user=Depends(current_user)):
    if user["role"] != "ADMIN":
        raise HTTPException(403, "Admin only")
    db = get_db()
    rows = db.execute("SELECT l.*, u.full_name FROM loans l JOIN users u ON l.user_id=u.id ORDER BY l.applied_at DESC").fetchall()
    db.close()
    return [dict(r) for r in rows]

# ── AI Chat (OpenRouter) ──────────────────────────────────────────────
@app.post("/api/chat")
async def chat(req: ChatReq, user=Depends(current_user)):
    if not OPENROUTER_API_KEY:
        raise HTTPException(503, "AI is not configured. Set OPENROUTER_API_KEY.")

    db = get_db()
    context = ""
    if req.account_id:
        acc = db.execute("SELECT * FROM accounts WHERE id=? AND user_id=?",
                         (req.account_id, user["sub"])).fetchone()
        if acc:
            txns = db.execute("SELECT * FROM transactions WHERE account_id=? ORDER BY created_at DESC LIMIT 5",
                               (acc["id"],)).fetchall()
            context = f"Account: {acc['account_number']}, Balance: ₹{acc['balance']:.2f}, Type: {acc['account_type']}. Recent transactions: {json.dumps([dict(t) for t in txns])}"
    db.close()

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
    # Try to serve as a static file if it exists, else serve index.html
    frontend_dir = os.path.join(os.path.dirname(__file__), "../frontend")
    file_path = os.path.join(frontend_dir, full_path)
    
    if full_path and os.path.isfile(file_path):
        return FileResponse(file_path)
    
    # Otherwise return the SPA index.html
    return FileResponse(os.path.join(frontend_dir, "index.html"))
