# Pickora

Shopee Affiliate product discovery site for `pickora.hotelcare.com`.

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

```bash
docker compose build
docker compose up -d scheduler web
```

Run the first update manually:

```bash
docker compose run --rm worker
```

Test:

```bash
curl http://SERVER_IP:8080/health
curl http://SERVER_IP:8080/data/feed-status.json
curl http://SERVER_IP:8080/data/products.json | head
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
sudo certbot --nginx -d pickora.hotelcare.com
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
