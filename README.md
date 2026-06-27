# Animetro Education Consulting

Static bilingual website for Animetro Education Consulting / 艾美加教育顧問.

## Structure

- `/` is the language landing page.
- `/en/` contains English pages.
- `/zh/` contains Traditional Chinese pages.
- `/assets/styles.css` contains shared design styles.

## Source of Truth

The Google Sheet named **Animetro Website Content Master** is the single source of truth for website content.

Required tabs:

- `Global` (shared navigation and footer)
- `Home` (homepage content)
- `Services` (Services page content)
- `Logo Package` (approved logo filenames and usage)
- `Brand Identity`
- `Website Images` (general website image inventory and service image fallback)
- `Service Images` (structured service-card image metadata)

The sync workflow reads those tabs, exports them into `content/`, regenerates the static website files, verifies the approved homepage and service-image output, commits the results to `main`, and lets Vercel auto-deploy from GitHub.

## Logo And Brand Asset Rules

Do not add, display, generate, redraw, approximate, or restore an Animetro logo unless the exact file name is approved in the Google Sheet source of truth.

Before using any logo or branded visual, verify the file in these Sheet tabs:

- `Logo Package`
- `Brand Identity`
- `Website Images`

This applies to the header, footer, favicon, app icon, hero images, service images, business card previews, mockups, and any image that contains Animetro branding.

If the approved logo file cannot be confirmed in the Sheet and present in the repo, leave the website text-only and ask before making a logo change.

Do not use Git submodules.

## Automatic Sync

The workflow is:

```text
Google Sheet
  -> GitHub Actions
  -> generated static files committed to main
  -> Vercel auto-deploy
```

The workflow runs:

- manually from GitHub Actions using **Run workflow**
- hourly on a schedule

Workflow file:

```text
.github/workflows/sync-google-sheet.yml
```

The sync script generates or updates:

- `index.html`
- `en/index.html`
- `zh/index.html`
- `en/services/index.html`
- `zh/services/index.html`
- `assets/`
- `content/`

## GitHub Secrets

Add these secrets in GitHub:

- `GOOGLE_SHEET_ID`: the spreadsheet ID for **Animetro Website Content Master**
- `GOOGLE_SERVICE_ACCOUNT_JSON`: full service-account JSON credentials that can read the spreadsheet
- `GOOGLE_WORKLOAD_IDENTITY_PROVIDER`: optional GitHub OIDC provider used by `google-github-actions/auth@v2`

This repository is already configured to use this spreadsheet by default:

```text
1MjWHgypp0SItMQI6AHiUoTAqTzhe8pyeFVUblcnSDTg
```

You only need to set `GOOGLE_SHEET_ID` if you want to override that default. The spreadsheet ID is the long value in a Google Sheet URL:

```text
https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
```

Share the Google Sheet with the service account email as a Viewer.

## Local Preview

From this folder, run:

```bash
pip install -r requirements.txt
python3 -m http.server 4173
```

Then open:

```text
http://localhost:4173/
```

To test the sync locally, export credentials and run:

```bash
export GOOGLE_SHEET_ID="your-sheet-id"
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/google-credentials.json"
python scripts/sync_google_sheet_site.py
```

## Vercel Deployment

This is a plain static HTML/CSS/JavaScript website. It is not Vite, React, or Next.js.

Use these Vercel settings:

- Framework Preset: `Other`
- Build Command: leave blank
- Output Directory: leave blank
- Install Command: leave blank

Vercel should deploy the static files directly from the repository root whenever GitHub receives a commit on `main`.

## GitHub Upload

Upload these files and folders to GitHub:

- `index.html`
- `en/`
- `zh/`
- `assets/`
- `content/`
- `vercel.json`
- `.gitignore`
- `README.md`
- `requirements.txt`
- `scripts/`
- `.github/workflows/`

Do not upload:

- `dist/`
- `node_modules/`
- `.DS_Store`
- `package.json`

## Domain

After the Vercel project is deployed:

1. Open the Vercel project dashboard.
2. Go to Settings > Domains.
3. Add `animetro.ca`.
4. Follow Vercel's DNS instructions for the domain registrar.
5. Add the recommended `A` record or `CNAME` record shown by Vercel.
6. Wait for DNS verification to finish.
