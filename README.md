# Pickora

Shopee Affiliate product discovery site for `pickora.hotelcarepro.com`.

## Architecture

- `scheduler`: downloads the private Shopee CSV feed and refreshes products daily
- `worker`: manual one-off feed update
- `web`: serves the Pickora frontend and generated JSON
- `public/products.json`: selected products
- `public/feed-status.json`: pipeline status

## Install on DigitalOcean

```bash
unzip pickora-project.zip
cd pickora-project
cp .env.example .env
nano .env
```

Set the private Shopee Data Feed URL in `.env`.
Set `GA4_MEASUREMENT_ID` to the ID from your GA4 web data stream (format `G-...`).

```bash
docker compose build
docker compose up -d scheduler web
```

Run the first update manually:

```bash
docker compose run --rm worker
```

`scheduler` and `worker` share the same `pickora-app:latest` image. After
updating files under `app/`, rebuild that image before running the worker:

```bash
docker compose build --no-cache scheduler
docker compose run --rm worker
```

Test:

```bash
curl http://SERVER_IP:8080/health
curl http://SERVER_IP:8080/data/feed-status.json
curl http://SERVER_IP:8080/data/products.json | head
curl http://SERVER_IP:8080/sitemap.xml | head
curl http://SERVER_IP:8080/data/seo-status.json
curl http://SERVER_IP:8080/data/link-health.json
```

Open:

```text
http://SERVER_IP:8080
```

## Domain

Create this DNS record:

```text
Type: A
Name: pickora
Value: DIGITALOCEAN_SERVER_IP
```

For an existing host Nginx, copy:

```bash
sudo cp nginx/host-pickora.conf.example /etc/nginx/sites-available/pickora
sudo ln -s /etc/nginx/sites-available/pickora /etc/nginx/sites-enabled/pickora
sudo nginx -t
sudo systemctl reload nginx
```

Add HTTPS:

```bash
sudo certbot --nginx -d pickora.hotelcarepro.com
```

When Cloudflare proxy is enabled, set SSL/TLS mode to Full (strict) after the certificate is active.

## Commands

```bash
docker compose ps
docker compose logs -f scheduler
docker compose logs -f web
docker compose run --rm worker
docker compose up -d --build
docker compose down
```

## Important

- Never publish `.env`
- The Shopee feed URL should remain private
- Column names can vary; the worker detects common column names automatically
- If detection fails, inspect the first line of the CSV and update the candidate names in `app/run_pipeline.py`
- Every feed refresh regenerates product detail pages and `sitemap.xml`
- Affiliate button clicks are sent to GA4 as the `affiliate_click` event
- Search, share, comparison, sign-up, and CTA experiment events are sent to GA4
- `EMAIL_SUBSCRIBE_ENDPOINT` must accept cross-origin JSON POST requests
- `/analytics/` is a private, server-rendered GA4 Data API dashboard
- Run `python3 scripts/seo-audit.py` before deployment

## Production analytics dashboard

The dashboard is served at `/analytics/`. Nginx routes `/api/analytics/` to the
private `analytics-api` container; missing dashboard assets and API paths return
404 instead of the home-page SPA fallback.

Create a Google Cloud service account, enable the Google Analytics Data API,
and grant its email Viewer access to the GA4 property. Set these values in
`.env`:

```env
GA4_PROPERTY_ID=123456789
GOOGLE_SERVICE_ACCOUNT_JSON_B64=...
ANALYTICS_DASHBOARD_TOKEN=a-long-random-secret
ANALYTICS_SESSION_SECONDS=28800
ANALYTICS_COOKIE_SECURE=true
```

Encode the JSON without line wraps:

```bash
base64 -w 0 service-account.json
```

The base64 value is decoded only by `analytics-api`. The service-account JSON,
private key, and configured dashboard token are never returned to the browser.
Login is a native form POST; the server returns an HttpOnly, SameSite session
cookie shared safely across Gunicorn workers.

Deploy:

```bash
docker compose build --no-cache scheduler
docker compose up -d --force-recreate scheduler analytics-api web
docker compose ps
curl -i http://127.0.0.1:${WEB_PORT:-8080}/analytics
curl -i http://127.0.0.1:${WEB_PORT:-8080}/analytics/missing.js
curl -i http://127.0.0.1:${WEB_PORT:-8080}/api/analytics/missing
```

Expected results are `308` for `/analytics`, then `404` for both missing paths.
Open the HTTPS production URL `/analytics/` and enter
`ANALYTICS_DASHBOARD_TOKEN`. For local HTTP-only browser testing, temporarily
set `ANALYTICS_COOKIE_SECURE=false`; never use that setting in production.

Run automated checks:

```bash
python -m unittest discover -s app/tests -v
python3 scripts/seo-audit.py
python3 scripts/production-seo-audit.py https://pickora.hotelcarepro.com
```
