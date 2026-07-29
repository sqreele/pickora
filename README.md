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
- `/analytics/` shows on-device counters only; use GA4 for aggregate reporting
- Run `python3 scripts/seo-audit.py` before deployment

## Aggregate analytics

Create a Google Cloud service account, enable the Google Analytics Data API and
Google Search Console API, then grant its email:

- Viewer access to the GA4 property
- Full or restricted access to the Search Console property

Set `GA4_PROPERTY_ID`, `SEARCH_CONSOLE_SITE_URL`, and
`GOOGLE_SERVICE_ACCOUNT_JSON_B64` in `.env`. Also set a long alphanumeric
`ANALYTICS_DASHBOARD_TOKEN`; it is required when the dashboard fetches the
aggregate summary. Encode the JSON without line wraps:

```bash
base64 -w 0 service-account.json
```

The credential is used only by the worker. The frontend reads aggregate,
credential-free data from `/data/analytics-summary.json`; Nginx protects that
file with the dashboard token request header.
