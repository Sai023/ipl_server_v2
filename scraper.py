name: IPL 2026 Daily Sync

on:
  workflow_dispatch:
  schedule:
    - cron: '0 0 * * *'

jobs:
  sync:
    runs-on: ubuntu-latest
    container:
      image: ://microsoft.com
      options: --shm-size=2gb

    permissions:
      contents: write

    steps:
      - name: 1. Checkout repository
        uses: actions/checkout@v4

      - name: 2. Fix Directory Permissions
        run: chown -R $(id -u):$(id -g) $GITHUB_WORKSPACE

      - name: 3. Install Project Dependencies
        run: |
          pip install --upgrade pip
          pip install playwright flask
          playwright install chromium --with-deps

      - name: 4. Self-Healing: Seed Database
        # This ensures the DB is NEVER empty, even if the git push was 'kak'
        run: |
          mkdir -p data/matches
          python Seed_Matches.py

      - name: 5. Run Super Scraper
        run: |
          export PYTHONPATH=$PYTHONPATH:.
          python scraper.py

      - name: 6. Atomic Data Sync
        run: |
          git config --global --add safe.directory "$GITHUB_WORKSPACE"
          git config --local user.email "action@github.com"
          git config --local user.name "GitHub Action"
          git add data/fantasy.db data/matches/*.json 2>/dev/null || true
          if ! git diff --staged --quiet; then
            git commit -m "data: sync $(date +%Y-%m-%d)"
            git push origin main
          fi
