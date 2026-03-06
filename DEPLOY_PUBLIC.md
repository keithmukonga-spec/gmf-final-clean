# Public Launch Guide (No Coding)

## Option A: Render (Recommended)
1. Create GitHub account (if you don't have one).
2. Create a new private repository on GitHub.
3. Upload all files from this folder: `E:\Windows\gig-market-farm-website\backend`.
4. Go to https://render.com and sign in with GitHub.
5. Click **New +** -> **Web Service**.
6. Select your repository.
7. Use these settings:
   - Environment: `Python 3`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT wsgi:app`
8. Add environment variables:
   - `SECRET_KEY` = any long random text
   - `PUBLIC_BASE_URL` = your Render URL (example: `https://your-service.onrender.com`)
   - `JWT_SECRET` = any long random text
9. Click **Create Web Service**.
10. Wait until status is **Live**.

## Connect Your Domain
1. Buy a domain (Namecheap/GoDaddy/etc.) if needed.
2. In Render service: **Settings** -> **Custom Domains** -> add your domain.
3. In your domain DNS panel, add the DNS records Render shows.
4. Wait for SSL certificate to become active automatically.

## Show On Google
1. Open Google Search Console: https://search.google.com/search-console
2. Add your domain property.
3. Verify ownership (DNS method).
4. Request indexing for home page and important pages.

## Important
- Keep your wallet/API secrets out of frontend files.
- This project currently uses SQLite; for heavy production traffic move to PostgreSQL.
