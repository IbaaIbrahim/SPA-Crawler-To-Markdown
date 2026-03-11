python3 -m spa_crawler \
  --start-url "https://dxbinteract.com" \
  --scrape true \
  --wait-until load \
  --wait-text-growth-ms 5000 \
  --timeout-ms 60000 \
  --concurrency 1 \
  --max-pages 1 \
  --out outputs/all-urls.json
  --stealth true

python3 -m spa_crawler \
  --start-url "https://dxbinteract.com" \
  --scrape true \
  --wait-until load \
  --wait-text-growth-ms 10000 \
  --timeout-ms 60000 \
  --concurrency 1 \
  --max-pages 1 \
  --stealth true \
  --headless false \
  --out outputs/all-urls.json

python3 -m spa_crawler \
  --start-url "https://www.propertyfinder.ae/en/new-projects/emaar-properties/creek-haven" \
  --scrape true \
  --wait-until load \
  --wait-text-growth-ms 5000 \
  --timeout-ms 60000 \
  --concurrency 1 \
  --max-pages 1 \
  --stealth true \
  --out outputs/all-urls.json