# Gig Market Farm - Extended Launch MVP

Now includes:
- Freelance marketplace (jobs, applications, assignments, completion)
- Seller marketplace (products, orders)
- Owner commission engine
- Payment provider connectors: M-Pesa, Card (Stripe), PayPal, Bitcoin, Bank transfer
- Referral earnings:
  - signup bonus via referral code
  - login-click bonus via referral code
- Blog system with owner publishing
- Sample jobs/products/blogs seeded automatically

## Run

```powershell
cd E:\Windows\gig-market-farm-website\backend
Copy-Item .\.env.example .\.env -ErrorAction SilentlyContinue
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

Open:
- http://127.0.0.1:5050

## Owner Seed Login
- Email: `keithmukonga@gmail.com`
- Password: `Owner@12345`

## Payment Setup
Fill `.env` with your real provider keys:
- M-Pesa Daraja (`MPESA_*`)
- Stripe card (`STRIPE_SECRET_KEY`)
- PayPal (`PAYPAL_*`)
- Bitcoin (`COINBASE_COMMERCE_API_KEY`)
- Bank transfer details (`BANK_*`)

Without keys, the API still works and returns `needs_config` with setup guidance.

## Main New APIs
- `POST /api/auth/login-click`
- `GET /api/referrals/summary`
- `GET /api/features`
- `GET /api/payments/methods`
- `POST /api/payments/initiate`
- `GET /api/blogs`
- `POST /api/blogs` (owner)

- GET /api/payments`n- POST /api/payments/{reference}/mark-paid (owner)
- POST /api/payments/webhooks/mpesa`n- POST /api/payments/webhooks/paypal`n- POST /api/payments/webhooks/stripe`n- POST /api/payments/webhooks/bitcoin`n
