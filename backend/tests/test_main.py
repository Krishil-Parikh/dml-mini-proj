"""
NexaBank Backend Tests
Run: pytest backend/tests/ -v
"""
import pytest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi.testclient import TestClient
from main import app, init_db

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    init_db()

def get_token(email="admin@nexabank.com", password="admin123"):
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    return res.json().get("token")

class TestAuth:
    def test_login_success(self):
        res = client.post("/api/auth/login", json={"email":"admin@nexabank.com","password":"admin123"})
        assert res.status_code == 200
        assert "token" in res.json()

    def test_login_invalid(self):
        res = client.post("/api/auth/login", json={"email":"bad@bad.com","password":"wrong"})
        assert res.status_code == 401

    def test_register_and_login(self):
        import uuid
        email = f"test_{uuid.uuid4().hex[:6]}@test.com"
        res = client.post("/api/auth/register", json={"full_name":"Test User","email":email,"password":"pass123"})
        assert res.status_code == 200
        res2 = client.post("/api/auth/login", json={"email":email,"password":"pass123"})
        assert res2.status_code == 200

    def test_duplicate_email(self):
        client.post("/api/auth/register", json={"full_name":"A","email":"dup@test.com","password":"x"})
        res = client.post("/api/auth/register", json={"full_name":"B","email":"dup@test.com","password":"y"})
        assert res.status_code == 400

class TestAccounts:
    def test_create_account(self):
        token = get_token()
        res = client.post("/api/accounts/create",
                          json={"account_type":"SAVINGS"},
                          headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        assert "account_number" in res.json()

    def test_my_accounts(self):
        token = get_token()
        client.post("/api/accounts/create", json={"account_type":"SAVINGS"},
                    headers={"Authorization": f"Bearer {token}"})
        res = client.get("/api/accounts/my", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_no_auth(self):
        res = client.get("/api/accounts/my")
        assert res.status_code in (401, 403)

class TestTransactions:
    def setup_accounts(self):
        token = get_token()
        h = {"Authorization": f"Bearer {token}"}
        a1 = client.post("/api/accounts/create", json={"account_type":"SAVINGS"}, headers=h).json()
        a2 = client.post("/api/accounts/create", json={"account_type":"CURRENT"}, headers=h).json()
        return token, a1["account_number"], a2["account_number"]

    def test_transfer(self):
        token, acc1, acc2 = self.setup_accounts()
        res = client.post("/api/transactions/transfer",
                          json={"from_account":acc1,"to_account":acc2,"amount":1000,"description":"Test"},
                          headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        assert res.json()["amount"] == 1000

    def test_insufficient_funds(self):
        token, acc1, acc2 = self.setup_accounts()
        res = client.post("/api/transactions/transfer",
                          json={"from_account":acc1,"to_account":acc2,"amount":9999999},
                          headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 400

class TestLoans:
    def test_apply_loan(self):
        token = get_token()
        res = client.post("/api/loans/apply",
                          json={"loan_type":"PERSONAL","amount":100000,"tenure_months":12},
                          headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        data = res.json()
        assert "emi" in data
        assert data["status"] == "PENDING"
        assert data["emi"] > 0

    def test_my_loans(self):
        token = get_token()
        res = client.get("/api/loans/my", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200

class TestHealth:
    def test_health(self):
        res = client.get("/actuator/health")
        assert res.status_code == 200
        assert res.json()["status"] == "UP"
