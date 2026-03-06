
import base64
import json
import os
import secrets
import re
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from functools import wraps
from time import monotonic

import jwt
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_sqlalchemy import SQLAlchemy
from jinja2 import TemplateNotFound
from sqlalchemy import text
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "gig_market_farm.db")
PAYOUT_SETTINGS_PATH = os.path.join(BASE_DIR, "owner_payout_settings.json")

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

JWT_SECRET = os.getenv("JWT_SECRET", "change-this-secret-now")
JWT_ALGO = "HS256"
JWT_HOURS = int(os.getenv("JWT_EXPIRES_HOURS", "24"))
JOB_COMMISSION_RATE = 0.12
PRODUCT_COMMISSION_RATE = 0.08
REFERRAL_SIGNUP_BONUS = float(os.getenv("REFERRAL_SIGNUP_BONUS", "1.0"))
REFERRAL_LOGIN_CLICK_BONUS = float(os.getenv("REFERRAL_LOGIN_CLICK_BONUS", "0.05"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip()
AUTH_RATE_LIMIT_WINDOW_SEC = int(os.getenv("AUTH_RATE_LIMIT_WINDOW_SEC", "600"))
AUTH_RATE_LIMIT_MAX = int(os.getenv("AUTH_RATE_LIMIT_MAX", "30"))
LOGIN_LOCK_THRESHOLD = int(os.getenv("LOGIN_LOCK_THRESHOLD", "5"))
LOGIN_LOCK_MINUTES = int(os.getenv("LOGIN_LOCK_MINUTES", "15"))
FEATURED_JOB_FEE = float(os.getenv("FEATURED_JOB_FEE", "10.0"))
FEATURED_PRODUCT_FEE = float(os.getenv("FEATURED_PRODUCT_FEE", "8.0"))
BANNER_AD_MONTHLY_FEE = float(os.getenv("BANNER_AD_MONTHLY_FEE", "120.0"))

AUTH_RATE_BUCKETS: dict[str, deque] = defaultdict(deque)
LOGIN_FAILURES: dict[str, dict] = defaultdict(lambda: {"count": 0, "lock_until": 0.0})

db = SQLAlchemy(app)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    referral_code = db.Column(db.String(32), unique=True, nullable=True, index=True)
    referred_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    referral_balance = db.Column(db.Float, default=0.0, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), nullable=False)


class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False)
    budget = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(8), default="KES", nullable=False)
    status = db.Column(db.String(20), default="open", nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    assigned_freelancer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), nullable=False)


class JobApplication(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False)
    freelancer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    cover_note = db.Column(db.Text, nullable=False)
    proposed_amount = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), nullable=False)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False)
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0, nullable=False)
    image_url = db.Column(db.String(500), nullable=True)
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), nullable=False)


class ProductOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default="paid", nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), nullable=False)


class Commission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    source_type = db.Column(db.String(20), nullable=False)
    source_id = db.Column(db.Integer, nullable=False)
    gross_amount = db.Column(db.Float, nullable=False)
    commission_amount = db.Column(db.Float, nullable=False)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    beneficiary_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), nullable=False)


class BlogPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(220), unique=True, nullable=False, index=True)
    summary = db.Column(db.String(400), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_published = db.Column(db.Boolean, default=True, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), nullable=False)


class ReferralEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    referrer_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    referred_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    event_type = db.Column(db.String(32), nullable=False)
    amount = db.Column(db.Float, default=0.0, nullable=False)
    note = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), nullable=False)


class LoginClick(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    referral_code = db.Column(db.String(32), nullable=True)
    ip_address = db.Column(db.String(80), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), nullable=False)


class PaymentTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(20), nullable=False)
    reference = db.Column(db.String(80), unique=True, nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(20), default="initiated", nullable=False)
    payer_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    payload_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), nullable=False)



class WorkSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    started_at = db.Column(db.DateTime, nullable=False)
    ended_at = db.Column(db.DateTime, nullable=True)
    hours = db.Column(db.Float, default=0.0, nullable=False)
    note = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), nullable=False)


class Withdrawal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(30), nullable=False)
    destination = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), nullable=False)
def validate_password_strength(password: str) -> bool:
    if len(password) < 8:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"\d", password):
        return False
    if not re.search(r"[^A-Za-z0-9]", password):
        return False
    return True



def _client_ip() -> str:
    forwarded = (request.headers.get("X-Forwarded-For", "") or "").split(",")[0].strip()
    return (forwarded or request.remote_addr or "unknown")[:80]


def _rate_limited(key: str, limit: int = AUTH_RATE_LIMIT_MAX, window_sec: int = AUTH_RATE_LIMIT_WINDOW_SEC) -> bool:
    now = monotonic()
    bucket = AUTH_RATE_BUCKETS[key]
    while bucket and (now - bucket[0]) > window_sec:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


def _login_lock_remaining(email: str) -> int:
    row = LOGIN_FAILURES[email]
    left = int(max(0.0, row.get("lock_until", 0.0) - monotonic()))
    return left


def _record_login_failure(email: str) -> int:
    row = LOGIN_FAILURES[email]
    row["count"] = int(row.get("count", 0)) + 1
    if row["count"] >= LOGIN_LOCK_THRESHOLD:
        row["lock_until"] = monotonic() + (LOGIN_LOCK_MINUTES * 60)
        row["count"] = 0
    return _login_lock_remaining(email)


def _clear_login_failures(email: str) -> None:
    if email in LOGIN_FAILURES:
        LOGIN_FAILURES[email] = {"count": 0, "lock_until": 0.0}


def _public_base_url() -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/")
    try:
        return request.host_url.rstrip("/")
    except Exception:
        return "http://127.0.0.1:5050"

@app.after_request
def add_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    resp.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    resp.headers["Content-Security-Policy"] = "default-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'"
    if request.path.startswith("/api/auth"):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Pragma"] = "no-cache"
    if request.is_secure:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp
def generate_referral_code() -> str:
    while True:
        code = f"GMF{secrets.token_hex(3).upper()}"
        if not User.query.filter_by(referral_code=code).first():
            return code


def slugify(text_value: str) -> str:
    base = "".join(ch.lower() if ch.isalnum() else "-" for ch in text_value).strip("-")
    base = "-".join([x for x in base.split("-") if x])
    if not base:
        base = f"post-{secrets.token_hex(3)}"
    cand, i = base, 1
    while BlogPost.query.filter_by(slug=cand).first():
        i += 1
        cand = f"{base}-{i}"
    return cand


def create_token(user: User) -> str:
    return jwt.encode(
        {
            "sub": str(user.id),
            "role": user.role,
            "exp": datetime.now(UTC) + timedelta(hours=JWT_HOURS),
            "iat": datetime.now(UTC),
        },
        JWT_SECRET,
        algorithm=JWT_ALGO,
    )


def auth_required(roles=None):
    roles = roles or []

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return jsonify({"error": "Unauthorized"}), 401
            token = auth.split(" ", 1)[1].strip()
            try:
                payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
                user_id = int(payload.get("sub", 0))
            except Exception:
                return jsonify({"error": "Invalid token"}), 401

            user = db.session.get(User, user_id)
            if user is None:
                return jsonify({"error": "User not found"}), 401
            if roles and user.role not in roles:
                return jsonify({"error": "Forbidden"}), 403

            request.current_user = user
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def owner_user() -> User | None:
    return User.query.filter_by(role="owner").first()


def user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "role": u.role,
        "referral_code": u.referral_code,
        "referral_balance": round(u.referral_balance or 0.0, 2),
    }


def job_to_dict(j: Job) -> dict:
    return {
        "id": j.id,
        "title": j.title,
        "description": j.description,
        "budget": j.budget,
        "currency": j.currency or "KES",
        "status": j.status,
        "client_id": j.client_id,
        "assigned_freelancer_id": j.assigned_freelancer_id,
        "created_at": j.created_at.isoformat(),
    }


def product_to_dict(p: Product) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "price": p.price,
        "stock": p.stock,
        "image_url": p.image_url,
        "seller_id": p.seller_id,
        "created_at": p.created_at.isoformat(),
    }


def blog_to_dict(b: BlogPost) -> dict:
    return {
        "id": b.id,
        "title": b.title,
        "slug": b.slug,
        "summary": b.summary,
        "content": b.content,
        "created_at": b.created_at.isoformat(),
    }


def create_commission(source_type: str, source_id: int, gross: float, beneficiary_id: int) -> float:
    owner = owner_user()
    if not owner:
        return 0.0
    rate = JOB_COMMISSION_RATE if source_type == "job" else PRODUCT_COMMISSION_RATE
    amount = round(gross * rate, 2)
    db.session.add(
        Commission(
            source_type=source_type,
            source_id=source_id,
            gross_amount=gross,
            commission_amount=amount,
            owner_user_id=owner.id,
            beneficiary_user_id=beneficiary_id,
        )
    )
    return amount


def provider_configured(provider: str) -> bool:
    if provider == "mpesa":
        s = _read_payout_settings()
        api_ready = bool(os.getenv("MPESA_CONSUMER_KEY") and os.getenv("MPESA_CONSUMER_SECRET") and os.getenv("MPESA_PASSKEY") and os.getenv("MPESA_SHORTCODE"))
        manual_ready = bool(str(s.get("mpesa_number", "")).strip())
        return api_ready or manual_ready
    if provider == "paypal":
        s = _read_payout_settings()
        return bool((os.getenv("PAYPAL_CLIENT_ID") and os.getenv("PAYPAL_CLIENT_SECRET")) or str(s.get("paypal_email", "")).strip())
    if provider == "card":
        return bool(os.getenv("STRIPE_SECRET_KEY") or os.getenv("CARD_MANUAL_ENABLED", "1") == "1")
    if provider == "bitcoin":
        s = _read_payout_settings()
        return bool(os.getenv("COINBASE_COMMERCE_API_KEY") or str(s.get("bitcoin_wallet", "")).strip())
    return provider == "bank"


def mpesa_stk_push(amount: float, phone: str, reference: str) -> dict:
    key = os.getenv("MPESA_CONSUMER_KEY", "")
    secret = os.getenv("MPESA_CONSUMER_SECRET", "")
    passkey = os.getenv("MPESA_PASSKEY", "")
    shortcode = os.getenv("MPESA_SHORTCODE", "")
    callback_url = os.getenv("MPESA_CALLBACK_URL", "https://example.com/mpesa-callback")
    base_url = os.getenv("MPESA_BASE_URL", "https://sandbox.safaricom.co.ke")
    if not (key and secret and passkey and shortcode):
        settings = _read_payout_settings()
        owner_mpesa = str(settings.get("mpesa_number", "")).strip()
        if owner_mpesa:
            return {
                "ok": True,
                "response": {
                    "manual": True,
                    "provider": "mpesa",
                    "owner_mpesa_number": owner_mpesa,
                    "amount": amount,
                    "currency": "KES",
                    "reference": reference,
                    "note": "Pay to owner M-Pesa number and use reference as reason.",
                },
            }
        return {"ok": False, "message": "M-Pesa not configured"}
    try:
        auth = base64.b64encode(f"{key}:{secret}".encode()).decode()
        t = requests.get(f"{base_url}/oauth/v1/generate?grant_type=client_credentials", headers={"Authorization": f"Basic {auth}"}, timeout=20)
        t.raise_for_status()
        token = t.json().get("access_token")
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        pwd = base64.b64encode(f"{shortcode}{passkey}{ts}".encode()).decode()
        payload = {
            "BusinessShortCode": shortcode,
            "Password": pwd,
            "Timestamp": ts,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(round(amount)),
            "PartyA": phone,
            "PartyB": shortcode,
            "PhoneNumber": phone,
            "CallBackURL": callback_url,
            "AccountReference": reference,
            "TransactionDesc": "Gig Market Farm Payment",
        }
        r = requests.post(f"{base_url}/mpesa/stkpush/v1/processrequest", headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=20)
        return {"ok": r.ok, "response": r.json()}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}

def paypal_create_order(amount: float, currency: str, reference: str) -> dict:
    client_id = os.getenv("PAYPAL_CLIENT_ID", "")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "")
    base_url = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    if not (client_id and client_secret):
        settings = _read_payout_settings()
        owner_paypal = str(settings.get("paypal_email", "")).strip()
        if owner_paypal:
            return {"ok": True, "response": {"manual": True, "provider": "paypal", "owner_paypal_email": owner_paypal, "amount": amount, "currency": currency, "reference": reference, "note": "Send payment to owner PayPal email and keep reference."}}
        return {"ok": False, "message": "PayPal not configured"}
    try:
        tok = requests.post(f"{base_url}/v1/oauth2/token", data={"grant_type": "client_credentials"}, auth=(client_id, client_secret), timeout=20)
        tok.raise_for_status()
        access = tok.json().get("access_token")
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [{"reference_id": reference, "amount": {"currency_code": currency, "value": f"{amount:.2f}"}}],
        }
        r = requests.post(f"{base_url}/v2/checkout/orders", headers={"Authorization": f"Bearer {access}"}, json=payload, timeout=20)
        return {"ok": r.ok, "response": r.json()}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def stripe_payment_intent(amount: float, currency: str, reference: str) -> dict:
    secret_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not secret_key:
        return {"ok": True, "response": {"manual": True, "provider": "card", "amount": amount, "currency": currency, "reference": reference, "note": "Card gateway key not set yet. Use M-Pesa/PayPal/Bank for now."}}
    try:
        data = {
            "amount": int(round(amount * 100)),
            "currency": currency.lower(),
            "payment_method_types[]": "card",
            "metadata[reference]": reference,
        }
        r = requests.post("https://api.stripe.com/v1/payment_intents", headers={"Authorization": f"Bearer {secret_key}"}, data=data, timeout=20)
        return {"ok": r.ok, "response": r.json()}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def coinbase_charge(amount: float, currency: str, reference: str) -> dict:
    api_key = os.getenv("COINBASE_COMMERCE_API_KEY", "")
    if not api_key:
        settings = _read_payout_settings()
        owner_wallet = str(settings.get("bitcoin_wallet", "")).strip()
        if owner_wallet:
            return {
                "ok": True,
                "response": {
                    "manual": True,
                    "provider": "bitcoin",
                    "owner_bitcoin_wallet": owner_wallet,
                    "amount": amount,
                    "currency": currency,
                    "reference": reference,
                    "note": "Send BTC to owner wallet and include reference in payment proof.",
                },
            }
        return {"ok": False, "message": "Bitcoin provider not configured"}
    try:
        payload = {
            "name": "Gig Market Farm Payment",
            "description": "Marketplace payment",
            "pricing_type": "fixed_price",
            "local_price": {"amount": f"{amount:.2f}", "currency": currency.upper()},
            "metadata": {"reference": reference},
        }
        r = requests.post("https://api.commerce.coinbase.com/charges", headers={"X-CC-Api-Key": api_key, "X-CC-Version": "2018-03-22"}, json=payload, timeout=20)
        return {"ok": r.ok, "response": r.json()}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def bank_transfer_instructions(reference: str, amount: float, currency: str) -> dict:
    return {
        "ok": True,
        "response": {
            "reference": reference,
            "amount": amount,
            "currency": currency,
            "bank_name": os.getenv("BANK_NAME", "Sample Bank"),
            "account_name": os.getenv("BANK_ACCOUNT_NAME", "Gig Market Farm Ltd"),
            "account_number": os.getenv("BANK_ACCOUNT_NUMBER", "001234567890"),
            "swift": os.getenv("BANK_SWIFT", "SAMPLEXXX"),
        },
    }



def safe_render(template_name: str, page_name: str):
    try:
        return render_template(template_name)
    except TemplateNotFound:
        html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{page_name} | Gig Market Farm</title></head><body>"
            f"<h2>{page_name}</h2>"
            "<p>This page template is missing in the current deployment.</p>"
            "<p>Deploy includes app.py but not templates/ files yet.</p>"
            "<p><a href='/health'>Check Health</a></p>"
            "</body></html>"
        )
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}

@app.route("/")
def index_page():
    return safe_render("index.html", "Home")



@app.route("/jobs")
def jobs_page():
    return safe_render("jobs.html", "Jobs")


@app.route("/marketplace")
def marketplace_page():
    return safe_render("marketplace.html", "Marketplace")


@app.route("/payments")
def payments_page():
    return safe_render("payments.html", "Payments")


@app.route("/blogs-page")
def blogs_page():
    return safe_render("blogs.html", "Blogs")


@app.route("/dashboard")
def dashboard_page():
    return safe_render("dashboard.html", "Dashboard")


@app.route("/auth")
def auth_page():
    return safe_render("auth.html", "Authentication")
@app.get("/health")
def health():
    return jsonify({"status": "ok"})



def _read_payout_settings() -> dict:
    if not os.path.exists(PAYOUT_SETTINGS_PATH):
        return {}
    try:
        with open(PAYOUT_SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_payout_settings(payload: dict) -> None:
    with open(PAYOUT_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


@app.get("/api/owner/payout-settings")
@auth_required(["owner"])
def get_owner_payout_settings():
    return jsonify(_read_payout_settings())


@app.post("/api/owner/payout-settings")
@auth_required(["owner"])
def set_owner_payout_settings():
    data = request.get_json(force=True)
    payload = {
        "bitcoin_wallet": str(data.get("bitcoin_wallet", "")).strip(),
        "paypal_email": str(data.get("paypal_email", "")).strip(),
        "bank_name": str(data.get("bank_name", "")).strip(),
        "bank_account_name": str(data.get("bank_account_name", "")).strip(),
        "bank_account_number": str(data.get("bank_account_number", "")).strip(),
        "mpesa_number": str(data.get("mpesa_number", "")).strip(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    _write_payout_settings(payload)
    return jsonify({"message": "Payout settings saved", "settings": payload})
@app.get("/api/features")
def features():
    return jsonify({"features": ["Freelance marketplace", "Seller marketplace", "M-Pesa/Card/PayPal/Bitcoin/Bank payments", "Referral earnings", "Blogs", "Owner commission tracking"]})


@app.get("/api/launch/readiness")
def launch_readiness():
    providers = ["mpesa", "card", "paypal", "bitcoin", "bank"]
    provider_status = [{"name": p, "configured": provider_configured(p)} for p in providers]
    payout = _read_payout_settings()
    payout_ready = any([
        bool(str(payout.get("bitcoin_wallet", "")).strip()),
        bool(str(payout.get("paypal_email", "")).strip()),
        bool(str(payout.get("bank_account_number", "")).strip()),
        bool(str(payout.get("mpesa_number", "")).strip()),
    ])
    live_required = {"mpesa", "card", "paypal", "bitcoin"}
    missing_live = [p["name"] for p in provider_status if p["name"] in live_required and not p["configured"]]
    return jsonify({
        "providers": provider_status,
        "missing_live_providers": missing_live,
        "payout_destination_set": payout_ready,
        "counts": {
            "users": User.query.count(),
            "jobs": Job.query.count(),
            "products": Product.query.count(),
            "blogs": BlogPost.query.count(),
        },
        "ready_for_demo": True,
        "ready_for_live_payments": (len(missing_live) == 0 and payout_ready),
    })

@app.post("/api/auth/login-click")
def login_click():
    ip = _client_ip()
    if _rate_limited(f"login_click:{ip}", limit=80, window_sec=AUTH_RATE_LIMIT_WINDOW_SEC):
        return jsonify({"error": "Too many login clicks. Try again later."}), 429
    data = request.get_json(force=True) if request.is_json else {}
    ref = str(data.get("referral_code", "")).strip().upper() or None
    db.session.add(LoginClick(referral_code=ref, ip_address=ip, user_agent=(request.headers.get("User-Agent", "") or "")[:255]))
    if ref:
        u = User.query.filter_by(referral_code=ref).first()
        if u:
            u.referral_balance = round((u.referral_balance or 0.0) + REFERRAL_LOGIN_CLICK_BONUS, 2)
            db.session.add(ReferralEvent(referrer_user_id=u.id, referred_user_id=None, event_type="login_click", amount=REFERRAL_LOGIN_CLICK_BONUS, note="Login click bonus"))
    db.session.commit()
    return jsonify({"message": "Login click tracked"}), 201


@app.post("/api/auth/register")
def register():
    ip = _client_ip()
    if _rate_limited(f"register:{ip}", limit=20, window_sec=AUTH_RATE_LIMIT_WINDOW_SEC):
        return jsonify({"error": "Too many registration attempts. Try again later."}), 429
    data = request.get_json(force=True)
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    role = str(data.get("role", "")).strip().lower()
    ref_code = str(data.get("referral_code", "")).strip().upper()

    if len(name) < 2 or "@" not in email or (not validate_password_strength(password)):
        return jsonify({"error": "Use a stronger password: upper, lower, number, symbol (8+ chars)"}), 400
    if role not in {"client", "freelancer", "seller"}:
        return jsonify({"error": "Role must be client, freelancer, or seller"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already registered"}), 400

    referrer = User.query.filter_by(referral_code=ref_code).first() if ref_code else None
    user = User(name=name, email=email, password_hash=generate_password_hash(password), role=role, referral_code=generate_referral_code(), referred_by_user_id=(referrer.id if referrer else None), referral_balance=0.0)
    db.session.add(user)
    db.session.flush()

    if referrer:
        referrer.referral_balance = round((referrer.referral_balance or 0.0) + REFERRAL_SIGNUP_BONUS, 2)
        db.session.add(ReferralEvent(referrer_user_id=referrer.id, referred_user_id=user.id, event_type="signup", amount=REFERRAL_SIGNUP_BONUS, note="Referral signup bonus"))

    db.session.commit()
    return jsonify({"token": create_token(user), "user": user_to_dict(user)}), 201


@app.post("/api/auth/login")
def login():
    ip = _client_ip()
    if _rate_limited(f"login:{ip}", limit=AUTH_RATE_LIMIT_MAX, window_sec=AUTH_RATE_LIMIT_WINDOW_SEC):
        return jsonify({"error": "Too many login attempts. Try again later."}), 429

    data = request.get_json(force=True)
    email = str(data.get("email", "")).strip().lower()
    if not email:
        return jsonify({"error": "Email is required"}), 400

    locked_seconds = _login_lock_remaining(email)
    if locked_seconds > 0:
        return jsonify({"error": f"Account temporarily locked. Try again in {locked_seconds} seconds."}), 429

    user = User.query.filter_by(email=email).first()
    if user is None or not check_password_hash(user.password_hash, str(data.get("password", ""))):
        remaining = _record_login_failure(email)
        if remaining > 0:
            return jsonify({"error": f"Invalid credentials. Account locked for {remaining} seconds."}), 401
        return jsonify({"error": "Invalid credentials"}), 401

    _clear_login_failures(email)
    if not user.referral_code:
        user.referral_code = generate_referral_code()
        db.session.commit()
    return jsonify({"token": create_token(user), "user": user_to_dict(user)})


@app.get("/api/auth/me")
@auth_required()
def me():
    return jsonify(user_to_dict(request.current_user))


@app.get("/api/referrals/summary")
@auth_required()
def referral_summary():
    u = request.current_user
    rows = ReferralEvent.query.filter_by(referrer_user_id=u.id).order_by(ReferralEvent.created_at.desc()).limit(50).all()
    return jsonify({"referral_code": u.referral_code, "referral_balance": round(u.referral_balance or 0.0, 2), "referred_users": User.query.filter_by(referred_by_user_id=u.id).count(), "events": [{"type": r.event_type, "amount": r.amount, "note": r.note, "created_at": r.created_at.isoformat()} for r in rows]})


@app.get("/api/referrals/link")
@auth_required()
def referral_link():
    u = request.current_user
    code = u.referral_code or generate_referral_code()
    if not u.referral_code:
        u.referral_code = code
        db.session.commit()
    return jsonify({
        "referral_code": code,
        "referral_link": f"{_public_base_url()}/auth?ref={code}",
    })
@app.post("/api/jobs")
@auth_required(["client", "owner"])
def create_job():
    u = request.current_user
    data = request.get_json(force=True)
    title = str(data.get("title", "")).strip()
    description = str(data.get("description", "")).strip()
    budget = float(data.get("budget", 0))
    currency = str(data.get("currency", "KES")).strip().upper()
    if currency not in {"KES", "USD"}:
        return jsonify({"error": "Currency must be KES or USD"}), 400
    if len(title) < 3 or len(description) < 10 or budget <= 0:
        return jsonify({"error": "Invalid job details"}), 400
    row = Job(title=title, description=description, budget=budget, currency=currency, client_id=u.id)
    db.session.add(row)
    db.session.commit()
    return jsonify(job_to_dict(row)), 201


@app.get("/api/jobs")
def list_jobs():
    return jsonify([job_to_dict(j) for j in Job.query.order_by(Job.created_at.desc()).all()])


@app.post("/api/jobs/<int:job_id>/apply")
@auth_required(["freelancer"])
def apply_job(job_id: int):
    u = request.current_user
    job = db.session.get(Job, job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.status != "open":
        return jsonify({"error": "Job is not open"}), 400
    data = request.get_json(force=True)
    note = str(data.get("cover_note", "")).strip()
    amt = float(data.get("proposed_amount", 0))
    if len(note) < 5 or amt <= 0:
        return jsonify({"error": "Invalid application"}), 400
    if JobApplication.query.filter_by(job_id=job_id, freelancer_id=u.id).first():
        return jsonify({"error": "Already applied"}), 400
    db.session.add(JobApplication(job_id=job_id, freelancer_id=u.id, cover_note=note, proposed_amount=amt))
    db.session.commit()
    return jsonify({"message": "Application submitted"}), 201


@app.post("/api/jobs/<int:job_id>/assign")
@auth_required(["client", "owner"])
def assign_job(job_id: int):
    u = request.current_user
    job = db.session.get(Job, job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if u.role != "owner" and job.client_id != u.id:
        return jsonify({"error": "Forbidden"}), 403
    freelancer_id = int(request.get_json(force=True).get("freelancer_id", 0))
    f = db.session.get(User, freelancer_id)
    if not f or f.role != "freelancer":
        return jsonify({"error": "Freelancer not found"}), 404
    job.assigned_freelancer_id = freelancer_id
    job.status = "assigned"
    db.session.commit()
    return jsonify(job_to_dict(job))


@app.post("/api/jobs/<int:job_id>/complete")
@auth_required(["client", "owner"])
def complete_job(job_id: int):
    u = request.current_user
    job = db.session.get(Job, job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if u.role != "owner" and job.client_id != u.id:
        return jsonify({"error": "Forbidden"}), 403
    if not job.assigned_freelancer_id:
        return jsonify({"error": "No freelancer assigned"}), 400
    job.status = "completed"
    c = create_commission("job", job.id, job.budget, job.assigned_freelancer_id)
    db.session.commit()
    return jsonify({"message": "Job completed", "commission": c})


@app.post("/api/products")
@auth_required(["seller", "owner"])
def create_product():
    u = request.current_user
    data = request.get_json(force=True)
    name = str(data.get("name", "")).strip()
    desc = str(data.get("description", "")).strip()
    price = float(data.get("price", 0))
    stock = int(data.get("stock", 0))
    image_url = str(data.get("image_url", "")).strip()
    if len(name) < 2 or len(desc) < 5 or price <= 0 or stock < 0:
        return jsonify({"error": "Invalid product details"}), 400
    p = Product(name=name, description=desc, price=price, stock=stock, image_url=image_url, seller_id=u.id)
    db.session.add(p)
    db.session.commit()
    return jsonify(product_to_dict(p)), 201


@app.get("/api/products")
def list_products():
    return jsonify([product_to_dict(p) for p in Product.query.order_by(Product.created_at.desc()).all()])


@app.post("/api/products/<int:product_id>/order")
@auth_required(["client", "owner"])
def create_order(product_id: int):
    buyer = request.current_user
    p = db.session.get(Product, product_id)
    if not p:
        return jsonify({"error": "Product not found"}), 404
    qty = int(request.get_json(force=True).get("quantity", 1))
    if qty <= 0:
        return jsonify({"error": "Quantity must be > 0"}), 400
    if p.stock < qty:
        return jsonify({"error": "Insufficient stock"}), 400
    total = round(p.price * qty, 2)
    p.stock -= qty
    o = ProductOrder(product_id=p.id, buyer_id=buyer.id, quantity=qty, total_amount=total, status="paid")
    db.session.add(o)
    c = create_commission("product", p.id, total, p.seller_id)
    db.session.commit()
    return jsonify({"order_id": o.id, "total_amount": total, "commission": c, "message": "Order placed"}), 201


@app.get("/api/payments/methods")
def payment_methods():
    providers = ["mpesa", "card", "paypal", "bitcoin", "bank"]
    return jsonify({"providers": [{"name": p, "configured": provider_configured(p)} for p in providers]})


@app.post("/api/payments/initiate")
@auth_required(["client", "owner", "seller"])
def initiate_payment():
    u = request.current_user
    data = request.get_json(force=True)
    provider = str(data.get("provider", "")).strip().lower()
    amount = float(data.get("amount", 0))
    currency = str(data.get("currency", "USD")).strip().upper()
    phone = str(data.get("phone", "")).strip()
    if provider not in {"mpesa", "card", "paypal", "bitcoin", "bank"}:
        return jsonify({"error": "Invalid provider"}), 400
    if amount <= 0:
        return jsonify({"error": "Amount must be > 0"}), 400

    ref = f"GMF-{provider.upper()}-{secrets.token_hex(4).upper()}"
    tx = PaymentTransaction(provider=provider, reference=ref, amount=amount, currency=currency, status="initiated", payer_user_id=u.id)
    db.session.add(tx)
    db.session.flush()

    if provider == "mpesa":
        r = mpesa_stk_push(amount, phone, ref)
    elif provider == "paypal":
        r = paypal_create_order(amount, currency, ref)
    elif provider == "card":
        r = stripe_payment_intent(amount, currency, ref)
    elif provider == "bitcoin":
        r = coinbase_charge(amount, currency, ref)
    else:
        r = bank_transfer_instructions(ref, amount, currency)

    tx.status = "pending" if r.get("ok") else "needs_config"
    tx.payload_json = json.dumps(r)
    db.session.commit()
    return jsonify({"transaction_id": tx.id, "reference": ref, "status": tx.status, "provider": provider, "provider_response": r}), 201


@app.get("/api/blogs")
def blogs():
    rows = BlogPost.query.filter_by(is_published=True).order_by(BlogPost.created_at.desc()).limit(50).all()
    return jsonify([blog_to_dict(b) for b in rows])


@app.post("/api/blogs")
@auth_required(["owner"])
def create_blog():
    d = request.get_json(force=True)
    title, summary, content = str(d.get("title", "")).strip(), str(d.get("summary", "")).strip(), str(d.get("content", "")).strip()
    if len(title) < 5 or len(summary) < 10 or len(content) < 20:
        return jsonify({"error": "Invalid blog post"}), 400
    b = BlogPost(title=title, slug=slugify(title), summary=summary, content=content, is_published=True, author_id=request.current_user.id)
    db.session.add(b)
    db.session.commit()
    return jsonify(blog_to_dict(b)), 201



@app.post("/api/work-sessions/start")
@auth_required(["freelancer", "seller"])
def start_work_session():
    u = request.current_user
    active = WorkSession.query.filter_by(user_id=u.id, ended_at=None).first()
    if active:
        return jsonify({"error": "You already have an active work session"}), 400
    note = str((request.get_json(force=True, silent=True) or {}).get("note", "")).strip()
    row = WorkSession(user_id=u.id, started_at=datetime.utcnow(), note=note)
    db.session.add(row)
    db.session.commit()
    return jsonify({"message": "Work session started", "session_id": row.id}), 201


@app.post("/api/work-sessions/stop")
@auth_required(["freelancer", "seller"])
def stop_work_session():
    u = request.current_user
    row = WorkSession.query.filter_by(user_id=u.id, ended_at=None).order_by(WorkSession.started_at.desc()).first()
    if not row:
        return jsonify({"error": "No active work session"}), 400
    row.ended_at = datetime.utcnow()
    try:
        row.hours = round((row.ended_at - row.started_at).total_seconds() / 3600.0, 2)
    except Exception:
        row.hours = 0.0
    db.session.commit()
    return jsonify({"message": "Work session stopped", "hours": row.hours})

@app.get("/api/work-sessions")
@auth_required(["freelancer", "seller", "owner"])
def list_work_sessions():
    u = request.current_user
    q = WorkSession.query.order_by(WorkSession.created_at.desc())
    if u.role != "owner":
        q = q.filter_by(user_id=u.id)
    rows = q.limit(100).all()
    return jsonify([
        {
            "id": r.id,
            "user_id": r.user_id,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
            "hours": r.hours,
            "note": r.note,
        }
        for r in rows
    ])


@app.post("/api/withdrawals/request")
@auth_required(["freelancer", "seller"])
def request_withdrawal():
    u = request.current_user
    data = request.get_json(force=True)
    amount = float(data.get("amount", 0))
    method = str(data.get("method", "")).strip().lower()
    destination = str(data.get("destination", "")).strip()

    if amount <= 0:
        return jsonify({"error": "Amount must be > 0"}), 400
    if method not in {"mpesa", "bank", "paypal", "bitcoin"}:
        return jsonify({"error": "Invalid withdrawal method"}), 400
    if not destination:
        return jsonify({"error": "Destination is required"}), 400

    if u.role == "seller":
        products = Product.query.filter_by(seller_id=u.id).all()
        pids = [p.id for p in products]
        orders = ProductOrder.query.filter(ProductOrder.product_id.in_(pids)).all() if pids else []
        gross = round(sum(o.total_amount for o in orders), 2)
        owner_cut = round(sum(c.commission_amount for c in Commission.query.filter_by(source_type="product", beneficiary_user_id=u.id).all()), 2)
    else:
        jobs = Job.query.filter_by(assigned_freelancer_id=u.id, status="completed").all()
        gross = round(sum(j.budget for j in jobs), 2)
        owner_cut = round(sum(c.commission_amount for c in Commission.query.filter_by(source_type="job", beneficiary_user_id=u.id).all()), 2)

    already_pending = round(sum(w.amount for w in Withdrawal.query.filter_by(user_id=u.id, status="pending").all()), 2)
    available = round(max(0, gross - owner_cut - already_pending), 2)
    if amount > available:
        return jsonify({"error": f"Amount exceeds available balance ({available})"}), 400

    row = Withdrawal(user_id=u.id, amount=amount, method=method, destination=destination, status="pending")
    db.session.add(row)
    db.session.commit()
    return jsonify({"message": "Withdrawal requested", "withdrawal_id": row.id, "available_after": round(available - amount, 2)}), 201


@app.get("/api/withdrawals")
@auth_required()
def list_withdrawals():
    u = request.current_user
    q = Withdrawal.query.order_by(Withdrawal.created_at.desc())
    if u.role != "owner":
        q = q.filter_by(user_id=u.id)
    rows = q.limit(100).all()
    return jsonify([
        {
            "id": r.id,
            "user_id": r.user_id,
            "amount": r.amount,
            "method": r.method,
            "destination": r.destination,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ])


@app.post("/api/withdrawals/<int:withdrawal_id>/status")
@auth_required(["owner"])
def update_withdrawal_status(withdrawal_id: int):
    row = db.session.get(Withdrawal, withdrawal_id)
    if not row:
        return jsonify({"error": "Withdrawal not found"}), 404
    status_in = str((request.get_json(force=True) or {}).get("status", "")).strip().lower()
    if status_in not in {"pending", "paid", "rejected"}:
        return jsonify({"error": "Invalid status"}), 400
    row.status = status_in
    db.session.commit()
    return jsonify({"message": "Withdrawal updated", "id": row.id, "status": row.status})
@app.get("/api/dashboard/owner")
@auth_required(["owner"])
def owner_dashboard():
    comms = Commission.query.all()
    payments = PaymentTransaction.query.all()
    refs = ReferralEvent.query.all()
    return jsonify({
        "total_users": User.query.count(),
        "total_jobs": Job.query.count(),
        "total_products": Product.query.count(),
        "total_orders": ProductOrder.query.count(),
        "total_blogs": BlogPost.query.count(),
        "total_login_clicks": LoginClick.query.count(),
        "total_commission": round(sum(c.commission_amount for c in comms), 2),
        "job_commission": round(sum(c.commission_amount for c in comms if c.source_type == "job"), 2),
        "product_commission": round(sum(c.commission_amount for c in comms if c.source_type == "product"), 2),
        "referral_payout_total": round(sum(r.amount for r in refs), 2),
        "payments_pending": len([p for p in payments if p.status in {"pending", "initiated"}]),
        "payments_total": len(payments),
        "withdrawals_pending": len([w for w in Withdrawal.query.filter_by(status="pending").all()]),
        "withdrawals_total": Withdrawal.query.count(),
        "total_work_hours_logged": round(sum(w.hours for w in WorkSession.query.all()), 2),
    })


@app.get("/api/owner/monetization-summary")
@auth_required(["owner"])
def monetization_summary():
    comms = Commission.query.all()
    referral_total = round(sum(r.amount for r in ReferralEvent.query.all()), 2)
    return jsonify({
        "current_streams": {
            "job_commissions": round(sum(c.commission_amount for c in comms if c.source_type == "job"), 2),
            "product_commissions": round(sum(c.commission_amount for c in comms if c.source_type == "product"), 2),
            "referral_cost_or_bonus_total": referral_total,
        },
        "suggested_new_streams": [
            {"name": "Featured Job Listing", "fee": FEATURED_JOB_FEE, "currency": "USD"},
            {"name": "Featured Product Listing", "fee": FEATURED_PRODUCT_FEE, "currency": "USD"},
            {"name": "Homepage Banner Ads", "fee": BANNER_AD_MONTHLY_FEE, "currency": "USD"},
            {"name": "Escrow Protection Fee", "note": "1-2% optional fee per paid job/order"},
            {"name": "Verified Seller Badge", "note": "monthly subscription for trusted sellers"},
        ],
    })
@app.get("/api/dashboard/seller")
@auth_required(["seller", "owner"])
def seller_dashboard():
    u = request.current_user
    sid = u.id if u.role == "seller" else int(request.args.get("seller_id", u.id))
    products = Product.query.filter_by(seller_id=sid).all()
    pids = [p.id for p in products]
    orders = ProductOrder.query.filter(ProductOrder.product_id.in_(pids)).all() if pids else []
    revenue = round(sum(o.total_amount for o in orders), 2)
    comms = Commission.query.filter_by(source_type="product", beneficiary_user_id=sid).all()
    owner_cut = round(sum(c.commission_amount for c in comms), 2)
    pending_withdrawals = round(sum(w.amount for w in Withdrawal.query.filter_by(user_id=sid, status="pending").all()), 2)
    work_hours = round(sum(w.hours for w in WorkSession.query.filter_by(user_id=sid).all()), 2)
    available_balance = round(max(0, revenue - owner_cut - pending_withdrawals), 2)
    return jsonify({"products_count": len(products), "orders_count": len(orders), "gross_revenue": revenue, "owner_commission_taken": owner_cut, "net_revenue": round(revenue - owner_cut, 2), "pending_withdrawals": pending_withdrawals, "available_balance": available_balance, "working_hours": work_hours})


@app.get("/api/dashboard/freelancer")
@auth_required(["freelancer", "owner"])
def freelancer_dashboard():
    u = request.current_user
    fid = u.id if u.role == "freelancer" else int(request.args.get("freelancer_id", u.id))
    assigned = Job.query.filter_by(assigned_freelancer_id=fid).all()
    completed = [j for j in assigned if j.status == "completed"]
    gross = round(sum(j.budget for j in completed), 2)
    comms = Commission.query.filter_by(source_type="job", beneficiary_user_id=fid).all()
    owner_cut = round(sum(c.commission_amount for c in comms), 2)
    pending_withdrawals = round(sum(w.amount for w in Withdrawal.query.filter_by(user_id=fid, status="pending").all()), 2)
    work_hours = round(sum(w.hours for w in WorkSession.query.filter_by(user_id=fid).all()), 2)
    available_balance = round(max(0, gross - owner_cut - pending_withdrawals), 2)
    return jsonify({"assigned_jobs": len(assigned), "completed_jobs": len(completed), "gross_earnings": gross, "owner_commission_taken": owner_cut, "net_earnings": round(gross - owner_cut, 2), "pending_withdrawals": pending_withdrawals, "available_balance": available_balance, "working_hours": work_hours})


@app.post("/api/leads")
def capture_lead():
    d = request.get_json(force=True)
    name, email, role = str(d.get("name", "")).strip(), str(d.get("email", "")).strip(), str(d.get("role", "")).strip()
    if len(name) < 2 or "@" not in email or not role:
        return jsonify({"error": "Invalid lead"}), 400
    db.session.add(Lead(name=name, email=email, role=role, phone=str(d.get("phone", "")).strip(), message=str(d.get("message", "")).strip()))
    db.session.commit()
    return jsonify({"message": "Lead captured"}), 201


def ensure_schema_updates() -> None:
    with db.engine.begin() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(user)"))]
        if "referral_code" not in cols:
            conn.execute(text("ALTER TABLE user ADD COLUMN referral_code VARCHAR(32)"))
        if "referred_by_user_id" not in cols:
            conn.execute(text("ALTER TABLE user ADD COLUMN referred_by_user_id INTEGER"))
        if "referral_balance" not in cols:
            conn.execute(text("ALTER TABLE user ADD COLUMN referral_balance FLOAT DEFAULT 0.0"))

        pcols = [r[1] for r in conn.execute(text("PRAGMA table_info(product)"))]
        if "image_url" not in pcols:
            conn.execute(text("ALTER TABLE product ADD COLUMN image_url VARCHAR(500)"))

        jcols = [r[1] for r in conn.execute(text("PRAGMA table_info(job)"))]
        if "currency" not in jcols:
            conn.execute(text("ALTER TABLE job ADD COLUMN currency VARCHAR(8) DEFAULT 'KES'"))

def seed_data() -> None:
    owner = User.query.filter_by(role="owner").first()
    if owner is None:
        owner = User(name="GMF Owner", email="keithmukonga@gmail.com", password_hash=generate_password_hash("Owner@12345"), role="owner", referral_code=generate_referral_code(), referral_balance=0.0)
        db.session.add(owner)
        db.session.flush()
    preferred_owner_ref = "GMFKEITH010"
    if not owner.referral_code:
        owner.referral_code = preferred_owner_ref
    elif owner.referral_code != preferred_owner_ref:
        existing = User.query.filter_by(referral_code=preferred_owner_ref).first()
        if existing is None or existing.id == owner.id:
            owner.referral_code = preferred_owner_ref

    client = User.query.filter_by(email="sample.client@gmf.local").first()
    if client is None:
        client = User(name="Sample Client", email="sample.client@gmf.local", password_hash=generate_password_hash("Client@1234"), role="client", referral_code=generate_referral_code())
        db.session.add(client)

    freelancer = User.query.filter_by(email="sample.freelancer@gmf.local").first()
    if freelancer is None:
        freelancer = User(name="Sample Freelancer", email="sample.freelancer@gmf.local", password_hash=generate_password_hash("Freelancer@1234"), role="freelancer", referral_code=generate_referral_code())
        db.session.add(freelancer)

    seller = User.query.filter_by(email="sample.seller@gmf.local").first()
    if seller is None:
        seller = User(name="Sample Seller", email="sample.seller@gmf.local", password_hash=generate_password_hash("Seller@1234"), role="seller", referral_code=generate_referral_code())
        db.session.add(seller)

    db.session.flush()

    if Job.query.count() < 24:
        sample_jobs = [
            ("Build Ecommerce Landing Page", "Need a high-converting black and gold landing page.", 700),
            ("Video Ad Editing", "Edit 10 short social videos for weekly campaign.", 350),
            ("SEO Blog Optimization", "Optimize 15 articles for ranking and conversions.", 500),
            ("Shopify Product Upload", "Upload and optimize 120 products with SEO titles.", 420),
            ("Logo + Brand Kit", "Create modern logo and social brand kit assets.", 250),
            ("WordPress Speed Fix", "Improve Core Web Vitals and page load performance.", 320),
            ("Email Automation Setup", "Set up welcome and abandoned-cart email flows.", 280),
            ("Google Ads Campaign", "Launch and optimize Google Search ad campaign.", 600),
            ("TikTok Content Manager", "Plan and schedule 30 days of TikTok content.", 400),
            ("Customer Support VA", "Handle chats and support tickets for ecommerce store.", 300),
            ("Data Entry Assistant", "Clean and organize inventory spreadsheet records.", 180),
            ("Mobile App UI Design", "Design Figma screens for service booking app.", 750),
            ("YouTube Channel Manager", "Manage uploads, thumbnails, and SEO metadata for weekly content.", 390),
            ("React Frontend Bug Fixes", "Resolve UI bugs and polish responsive behavior across pages.", 560),
            ("Database Migration Assistant", "Migrate legacy data into a normalized SQL schema.", 640),
            ("Social Media Community Manager", "Moderate comments and drive engagement daily.", 260),
            ("Podcast Audio Cleanup", "Noise reduction and mastering for 12 podcast episodes.", 310),
            ("Influencer Outreach Campaign", "Build outreach list and secure brand partnerships.", 520),
            ("Customer Onboarding SOP", "Document onboarding flows and support templates.", 230),
            ("Virtual Assistant - Scheduling", "Manage calendars, meetings, and reminders.", 210),
            ("AI Prompt Workflow Setup", "Create reusable prompts for support and content teams.", 480),
            ("Brand Photography Editing", "Retouch 200 ecommerce product photos.", 440),
            ("Sales Funnel Copywriting", "Write landing + email funnel copy for conversion.", 670),
            ("Backend API Integration", "Integrate external payment and shipment APIs.", 820),
        ]
        existing_titles = {x.title for x in Job.query.all()}
        for idx, (t, d, b) in enumerate(sample_jobs):
            if t not in existing_titles:
                ccy = "USD" if idx % 4 == 0 else "KES"
                db.session.add(Job(title=t, description=d, budget=b, currency=ccy, status="open", client_id=client.id))

    if Product.query.count() < 16:
        sample_products = [
            ("Organic Farm Honey 500ml", "Pure natural honey from local farms.", 18.0, 120, "https://images.unsplash.com/photo-1587049352851-8d4e89133924?auto=format&fit=crop&w=900&q=80"),
            ("Dried Mango Pack", "Sun-dried mango slices, premium export grade.", 7.5, 250, "https://images.unsplash.com/photo-1604908177522-04041b9f16d1?auto=format&fit=crop&w=900&q=80"),
            ("Cold Pressed Avocado Oil", "Healthy cooking oil from fresh avocados.", 22.0, 90, "https://images.unsplash.com/photo-1474979266404-7eaacbcd87c5?auto=format&fit=crop&w=900&q=80"),
            ("Premium Coffee Beans 1kg", "Fresh roasted arabica beans for cafes.", 16.0, 140, "https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?auto=format&fit=crop&w=900&q=80"),
            ("Natural Peanut Butter", "No sugar added creamy peanut butter.", 9.5, 180, "https://images.unsplash.com/photo-1585238342024-78d387f4a707?auto=format&fit=crop&w=900&q=80"),
            ("Herbal Tea Collection", "Mixed herbal tea pack for wellness lovers.", 11.0, 110, "https://images.unsplash.com/photo-1597318181409-cf64d0b5d8a2?auto=format&fit=crop&w=900&q=80"),
            ("Fresh Macadamia Nuts", "Premium export-quality macadamia nuts.", 14.0, 130, "https://images.unsplash.com/photo-1615486511484-92e172cc4fe0?auto=format&fit=crop&w=900&q=80"),
            ("Coconut Body Oil", "Natural skin oil infused with coconut extract.", 13.0, 95, "https://images.unsplash.com/photo-1617897903246-719242758050?auto=format&fit=crop&w=900&q=80"),
            ("Handmade Leather Wallet", "Durable genuine leather wallet with card slots.", 28.0, 60, "https://images.unsplash.com/photo-1627123424574-724758594e93?auto=format&fit=crop&w=900&q=80"),
            ("Organic Shea Butter", "Raw unrefined shea butter for skin and hair care.", 12.0, 170, "https://images.unsplash.com/photo-1611080541599-8c6dbde6ed28?auto=format&fit=crop&w=900&q=80"),
            ("Natural Black Soap", "Traditional deep cleansing soap bars.", 6.5, 300, "https://images.unsplash.com/photo-1607006344380-b6775a0824ce?auto=format&fit=crop&w=900&q=80"),
            ("Reusable Water Bottle", "Insulated stainless steel bottle 750ml.", 19.0, 125, "https://images.unsplash.com/photo-1602143407151-7111542de6e8?auto=format&fit=crop&w=900&q=80"),
            ("Scented Soy Candle Set", "Set of 3 long-lasting aromatic candles.", 24.0, 85, "https://images.unsplash.com/photo-1603006905003-be475563bc59?auto=format&fit=crop&w=900&q=80"),
            ("Fitness Resistance Bands", "5-level resistance training band pack.", 15.0, 220, "https://images.unsplash.com/photo-1596357395217-80de13130e92?auto=format&fit=crop&w=900&q=80"),
            ("Bamboo Cutting Board", "Eco-friendly kitchen board with juice groove.", 17.0, 100, "https://images.unsplash.com/photo-1516594798947-e65505dbb29d?auto=format&fit=crop&w=900&q=80"),
            ("Ceramic Mug Collection", "Premium heat-safe ceramic mug set.", 21.0, 75, "https://images.unsplash.com/photo-1514228742587-6b1558fcf93a?auto=format&fit=crop&w=900&q=80"),
        ]
        existing_names = {x.name for x in Product.query.all()}
        for n, d, pz, st, img in sample_products:
            if n not in existing_names:
                db.session.add(Product(name=n, description=d, price=pz, stock=st, image_url=img, seller_id=seller.id))
    if BlogPost.query.count() < 6:
        sample_blogs = [
            ("How Clients Hire Better Freelancers Faster", "A framework to post jobs that attract top freelancers.", "Use clear deliverables, milestone timelines, and budget ranges for better bids."),
            ("How Seller Businesses Can Get More Orders", "Steps for business owners to optimize listings and increase trust.", "Use good product photos, transparent shipping information, and quick replies."),
            ("Gig Market Farm Commission Model Explained", "Understand job, product, and referral earnings.", "The platform earns through successful transactions while creating value for all parties."),
            ("7 Ways Freelancers Increase Repeat Clients", "Practical methods to turn one project into long-term retainers.", "Deliver early updates, over-communicate timelines, and package maintenance services."),
            ("Product Listing Formula That Improves Sales", "Simple listing structure for better click-through and conversion.", "Use keyword-first titles, trust-focused descriptions, and lifestyle images."),
            ("Owner Playbook: Grow Marketplace Revenue", "How platform owners can grow earnings without hurting trust.", "Balance commission rates, promote best sellers, and reward quality referrals."),
        ]
        existing_slugs = {x.slug for x in BlogPost.query.all()}
        for title, summary, content in sample_blogs:
            slug = slugify(title)
            if slug not in existing_slugs:
                db.session.add(BlogPost(title=title, slug=slug, summary=summary, content=content, is_published=True, author_id=owner.id))
                existing_slugs.add(slug)

    db.session.commit()


def init_app() -> None:
    with app.app_context():
        db.create_all()
        ensure_schema_updates()
        db.create_all()
        seed_data()



@app.get("/api/payments")
@auth_required()
def list_payments():
    u = request.current_user
    q = PaymentTransaction.query.order_by(PaymentTransaction.created_at.desc())
    if u.role != "owner":
        q = q.filter_by(payer_user_id=u.id)
    rows = q.limit(100).all()
    return jsonify([
        {
            "id": r.id,
            "provider": r.provider,
            "reference": r.reference,
            "amount": r.amount,
            "currency": r.currency,
            "status": r.status,
            "payer_user_id": r.payer_user_id,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ])


@app.post("/api/payments/<string:reference>/mark-paid")
@auth_required(["owner"])
def mark_payment_paid(reference: str):
    tx = PaymentTransaction.query.filter_by(reference=reference).first()
    if not tx:
        return jsonify({"error": "Transaction not found"}), 404
    tx.status = "paid"
    db.session.commit()
    return jsonify({"message": "Payment marked paid", "reference": reference})


def _update_tx_status(reference: str, status: str, payload: dict):
    tx = PaymentTransaction.query.filter_by(reference=reference).first()
    if not tx:
        return False
    tx.status = status
    tx.payload_json = json.dumps(payload)
    db.session.commit()
    return True


@app.post("/api/payments/webhooks/mpesa")
def webhook_mpesa():
    payload = request.get_json(force=True, silent=True) or {}
    meta = payload.get("Body", {}).get("stkCallback", {})
    ref = meta.get("CheckoutRequestID") or meta.get("MerchantRequestID") or payload.get("reference")
    result_code = str(meta.get("ResultCode", ""))
    status = "paid" if result_code in {"0", ""} else "failed"
    if ref:
        _update_tx_status(ref, status, payload)
    return jsonify({"received": True})


@app.post("/api/payments/webhooks/paypal")
def webhook_paypal():
    payload = request.get_json(force=True, silent=True) or {}
    event_type = str(payload.get("event_type", ""))
    ref = (
        payload.get("resource", {}).get("purchase_units", [{}])[0].get("reference_id")
        if payload.get("resource") else payload.get("reference")
    )
    status = "paid" if "COMPLETED" in event_type.upper() else "pending"
    if ref:
        _update_tx_status(ref, status, payload)
    return jsonify({"received": True})


@app.post("/api/payments/webhooks/stripe")
def webhook_stripe():
    payload = request.get_json(force=True, silent=True) or {}
    obj = payload.get("data", {}).get("object", {})
    ref = obj.get("metadata", {}).get("reference") or payload.get("reference")
    event_type = str(payload.get("type", ""))
    status = "paid" if event_type in {"payment_intent.succeeded", "checkout.session.completed"} else "pending"
    if ref:
        _update_tx_status(ref, status, payload)
    return jsonify({"received": True})


@app.post("/api/payments/webhooks/bitcoin")
def webhook_bitcoin():
    payload = request.get_json(force=True, silent=True) or {}
    event = payload.get("event", {})
    data = event.get("data", {})
    ref = data.get("metadata", {}).get("reference") or payload.get("reference")
    timeline = data.get("timeline", [])
    latest_status = (timeline[-1].get("status", "") if timeline else "").upper()
    status = "paid" if latest_status in {"COMPLETED", "CONFIRMED", "RESOLVED"} else "pending"
    if ref:
        _update_tx_status(ref, status, payload)
    return jsonify({"received": True})


if __name__ == "__main__":
    init_app()
    port = int(os.getenv("PORT", "5050"))
    app.run(host="0.0.0.0", port=port, debug=False)









































