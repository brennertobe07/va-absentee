# Virginia April Referendum — Absentee Ballot Tracker

Interactive dashboard hosted on GitHub Pages. Data is generated locally from
INSTANCE-1 and pushed to this repo.

## Repo structure

```
index.html                      ← the dashboard (single-file app)
build_absentee_json.py    ← run this locally to refresh data
data/
  summary.json                  ← statewide totals
  counties.json                 ← one row per county
  precincts.json                ← one row per county/precinct
  metadata.json                 ← last_updated timestamp + source
```

## First-time setup

1. Create a GitHub repo (e.g. `va-absentee`)
2. Copy all files from this starter into it
3. Enable GitHub Pages:
   - Repo → Settings → Pages
   - Source: **Deploy from a branch** → `main` / `(root)`
4. Open `build_absentee_json.py` and set `REPO_ROOT` to the local path
   where you cloned the repo, e.g.:
   ```python
   REPO_ROOT = r"C:\Users\YourName\Documents\GitHub\va-absentee"
   ```
5. Install dependencies if needed:
   ```
   py -3.12 -m pip install pandas pyodbc
   ```

## Refreshing the data

Each morning (or whenever you want to update):

1. Run the Python script:
   ```
   py -3.12 build_absentee_json.py
   ```
2. Open GitHub Desktop
3. Commit the changed `data/*.json` files
4. Push to origin

The live dashboard at `https://brennertobe07.github.io/va-absentee/`
will update within ~1 minute.

## Dashboard features

- **Statewide view** — summary cards + D/R/U returned vs. still-out charts
- **County selector** — click any county in the sidebar to filter all charts
  and the precinct table to that county
- **County search** — type to filter the sidebar list
- **Precinct table** — shows all precincts for the selected county; searchable
- **Total rows** — pinned at the top of every table

## Notes

- GitHub Pages sites are **publicly accessible**. The data published here is
  aggregated totals only (no individual voter records).
- The dashboard reads JSON from `data/` using relative paths — it works
  correctly whether viewed on GitHub Pages or opened locally as a file.
