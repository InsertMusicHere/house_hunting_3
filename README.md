# Housing Market Intelligence Platform

Personal housing research app: Streamlit + hosted PostgreSQL (Neon) + SQLAlchemy.
Status: **Phase 2.1** (Add Property page with paste and saved-page upload for Realtor.ca and HouseSigma). Next: AI extraction (Phase 3). See `HANDOFF.md` for the full project history.

## Windows setup (PowerShell, from the project folder)

### 1. Create the virtual environment
```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
```
If PowerShell blocks the activate script, run this once, then retry:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```
Your prompt should now start with `(.venv)`. In VS Code: Ctrl+Shift+P, "Python: Select Interpreter", choose the `.venv` one.

### 2. Install dependencies
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Create the Neon database
1. Sign up at https://console.neon.tech (free plan, no credit card).
2. Create a project. Name it e.g. `housing-market`. Choose the Postgres version offered by default and the region closest to you.
3. On the project dashboard click **Connect**, make sure the role and database are the defaults, and copy the **connection string** (it looks like `postgresql://user:password@ep-xxxx.neon.tech/neondb?sslmode=require`).
4. In the console, check the project's storage limit under your plan details and note it.

### 4. Create your secrets file
```powershell
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
```
Open `.streamlit\secrets.toml` and paste the Neon connection string into `url`.

Generate your admin password hash:
```powershell
python scripts/make_password_hash.py
```
Paste the printed `password_hash = "..."` line into the `[auth]` section. Change `username` if you want.

### 5. Test the database connection
```powershell
python scripts/check_db_connection.py
```
Expected: `OK in X.Xs` plus the database name and a PostgreSQL version. The first call after idle can take several seconds (Neon wakes from suspend).

### 6. Run the app
```powershell
streamlit run app.py
```
Sign in, then click **Test Neon connection**.

### 7. Run unit tests
```powershell
pytest
```

## Phase 1: create the database tables

Run from the project root with the virtual environment active.

```powershell
pip install -r requirements.txt        # adds alembic
alembic upgrade head                    # creates the 6 tables in Neon
python scripts/seed_data_sources.py    # inserts the 5 reviewed sources
python scripts/check_schema.py         # every table should show OK; Migration version: 0001
pytest                                  # all tests
```
If `alembic upgrade head` fails to connect when your Neon string uses a host containing `-pooler`, try the direct (non-pooled) connection string from the Neon **Connect** dialog in `secrets.toml`.

Optional: run the tests against a real PostgreSQL scratch database too (it is wiped by the tests, so never point this at your real data):
```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://user:pass@host/scratch_db"
pytest
```

### Tables
| Table | Purpose |
|---|---|
| `data_sources` | Websites/origins, with terms status and review date (all four sites: automated retrieval not permitted) |
| `properties` | Listing facts, data-quality columns, raw extraction JSONB, description text |
| `property_features` | Detailed characteristics (kitchen, flooring, sunlight...) with per-field source and confidence in `ai_detail` |
| `listing_history` | Append-only price/status observations |
| `financial_details` | Your financial assumptions, one row per named scenario |
| `extraction_logs` | One row per attempt, including rejections (no raw page content) |

### Rules built into the schema
- Missing information is NULL and listed in `missing_fields`. Nothing is guessed.
- Property types: Detached, Semi-Detached, Link, Freehold Townhouse, Condo Townhouse. Apartment condos are rejected by validation.
- Duplicates: the app checks MLS number, normalized address + unit + postal code, and cleaned URL, then asks whether to update the existing record (which appends a history row). Unique indexes in the database are the backstop.
- Size is stored as `interior_size_min` / `interior_size_max` in square feet only.
- `date_added_to_app` is automatic; `listing_date` is NULL unless the listing gives it.
- The listing URL is stored as a reference link only. The app never fetches it.
- Fields you edit by hand are recorded in `manual_overrides` and are not overwritten by later updates.

## Changing the schema later
Edit `database/models.py`, then:
```powershell
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

## Phase 2: Add Property page

```powershell
pip install -r requirements.txt        # adds beautifulsoup4
streamlit run app.py                    # sign in, then open "Add Property" in the sidebar
pytest                                  # all tests
```

### How it works
1. **Input.** Paste the text of a listing page, or upload a saved `.html` page, for up to two sources of the same property (for example Realtor.ca plus HouseSigma). Optionally add the listing URL; it is stored as a reference link and never opened.
2. **Review.** Every extracted field is editable. If two sources disagree, a note shows both values; the form is pre-filled from Realtor.ca first (it carries the more precise values) and you can change anything. Notes also tell you when something was inferred (for example property type from Building Type + Title) or may not be the original listing text (HouseSigma's description tabs).
3. **Save.** Required: street address and city. Everything else is optional; missing facts stay empty. Use **Save and add another** to keep going.
4. **Duplicates.** Matches on MLS number, normalized address + unit + postal code, or cleaned URL show a side-by-side comparison with **Update existing record** (adds a history row, keeps fields you edited by hand) or **Cancel**.

### Supported layouts
- Realtor.ca and HouseSigma page text (auto-detected, or pick the site).
- Other sites (Condos.ca, Zolo): not parsed yet. Choose a source, paste, and fill the form by hand, or use **Enter manually**.
- Apartment condos are rejected and the rejection is logged.

### Saved-page uploads
Save the listing page in your browser (Ctrl+S, "Webpage, Complete" or "HTML only"), then upload the `.html` file. It is converted to plain text in memory (scripts and hidden elements dropped, the page's declared encoding respected) and parsed like pasted text; the file itself is never stored. Uploads from both supported sites have been checked against real saved pages: they give the same facts as pasting, and HouseSigma uploads also give the agent's original description and the full listing history. If a saved page ever misses fields, paste the visible text instead, and tell Claude which fields were missed.

### What gets stored
Listing facts in `properties`, features in `property_features` (with where each value came from), optional past listing history in `listing_history`, the extracted fields plus extras such as room dimensions in `raw_extraction` (never full HTML), and one `extraction_logs` row per attempt.

### Tests use invented data
`tests/fixtures/` contains synthetic listings that copy the page layouts with made-up addresses and text. No real listing content is in the repository.

## Git (secrets safety)
```powershell
git init
git status
```
Confirm `.streamlit/secrets.toml` does **not** appear in `git status`. It is listed in `.gitignore`. Never commit it.

## Layout
- `app.py` - entry point (login gate + connection check)
- `auth/` - password hashing (`passwords.py`) and Streamlit login gate (`gate.py`)
- `database/` - engine (`connection.py`), models, enums, repository (duplicate checks, create/update), seed data
- `alembic/` - migrations (`versions/0001_initial_schema.py`)
- `schemas/` - Pydantic validation models (`property.py`, `features.py`)
- `utils/normalization.py` - cleaning helpers (postal code, money, size, property type, address key)
- `config.py` - reads secrets (Streamlit secrets, then local secrets file, then `DATABASE_URL`)
- `parsers/` - paste/upload parsers (`realtor_ca.py`, `housesigma.py`, `html_to_text.py`); they read text you supply and never fetch
- `services/ingestion.py` - merge sources into a draft, validate, save, update, log
- `pages/1_Add_Property.py` - the Add Property page
- `ai/` - placeholder for Phase 3
- `scripts/` - `make_password_hash.py`, `check_db_connection.py`, `seed_data_sources.py`, `check_schema.py`
- `tests/` - pytest suite

## Notes
- Login: single admin. The username and a salted PBKDF2 hash live in secrets; comparison uses `hmac.compare_digest`. After 5 failed attempts the login locks for 5 minutes (counter lives in the running app process and resets if the app restarts).
- Streamlit Community Cloud later: paste the contents of `secrets.toml` into the app's Secrets settings. No code change is needed.
