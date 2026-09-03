"""
build_absentee_json.py
Queries absentee.dbo on INSTANCE-1 and writes JSON files
for the DPVA Absentee Dashboard hosted on GitHub Pages.

The election is auto-detected from Daily_Absentee_List, and the election day
and page label are written into data/metadata.json, which index.html reads at
load time. Switching cycles means updating ELECTION_CONFIG below - nothing in
the HTML needs to change.

Run: py -3.12 build_absentee_json.py
"""

import pyodbc
import pandas as pd
import json
import os
from datetime import datetime

# ── CONFIG ───────────────────────────────────────────────────────────────────
SERVER      = r"INSTANCE-1"
DATABASE    = "absentee"
ODBC_DRIVER = "ODBC Driver 17 for SQL Server"

REPO_ROOT   = r"C:\Scripts\Python\Python_Absentee\dashboards\va-absentee"
DATA_DIR    = os.path.join(REPO_ROOT, "data")

# Per-cycle settings, keyed by the ELECTION_NAME that VA SBE puts in
# Daily_Absentee_List. Add an entry at the start of each cycle.
#
#   election_day - drives the countdown in index.html
#   label        - page title / header text
#   cycle_start  - first day of early voting (46 days out, matching the April
#                  convention). Ballots received before this are binned onto it
#                  so a handful of early UOCAVA returns don't stretch the daily
#                  chart across months.
ELECTION_CONFIG = {
    "2026 November General": {
        "election_day": "2026-11-03",
        "label":        "2026 November General",
        "cycle_start":  "2026-09-18",
    },
    "2026 April 21 Special": {
        "election_day": "2026-04-21",
        "label":        "April Referendum",
        "cycle_start":  "2026-03-06",
    },
}
# Congressional districts targeted this cycle. Drives the By CD tab.
TARGET_CDS = ["01", "02", "05"]

# CONG_CODE_VALUE carries stray records; county x CD rows below this many
# ballots are suppressed unless they are a county's dominant row or a target.
MIN_CD_BALLOTS = 10

DEFAULT_LABEL = "Absentee Tracker"
# ─────────────────────────────────────────────────────────────────────────────

def get_election_name(conn):
    """Auto-detect the active election from Daily_Absentee_List.
    Picks the election with the most recent ballot activity.
    No config change needed when switching elections.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT TOP 1 ELECTION_NAME
        FROM Daily_Absentee_List
        WHERE ELECTION_NAME IS NOT NULL
        GROUP BY ELECTION_NAME
        ORDER BY MAX(BALLOT_RECEIPT_DATE) DESC
    """)
    row = cursor.fetchone()
    if not row:
        raise ValueError("No election found in Daily_Absentee_List")
    return row[0]

def build_summary_query(election_name):
    return f"""
-- Deduplicate Daily_Absentee_List: one row per voter, most resolved status wins.
-- Tiebreaker priority: Marked > Pre-Processed > On Machine > Unmarked > other
WITH dal_deduped AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY IDENTIFICATION_NUMBER
               ORDER BY
                   CASE BALLOT_STATUS
                       WHEN 'Marked'        THEN 1
                       WHEN 'Pre-Processed' THEN 2
                       WHEN 'On Machine'    THEN 3
                       WHEN 'Unmarked'      THEN 4
                       ELSE                      5
                   END,
                   BALLOT_RECEIPT_DATE DESC
           ) AS rn
    FROM Daily_Absentee_List
    WHERE ELECTION_NAME = '{election_name}'
      AND BALLOT_STATUS NOT IN ('Deleted', 'Not Issued')
)
SELECT
    COALESCE(van.CountyName,  ct.CountyName)   AS CountyName,
    COALESCE(van.PrecinctName, dal.PRECINCT_NAME) AS PrecinctName,
    dal.CONG_CODE_VALUE                        AS CD,
    COUNT(van.VoterFileVANID) AS TotalMatchedVoters,

    SUM(CASE WHEN dal.BALLOT_RECEIPT_DATE IS NOT NULL
             AND dal.BALLOT_STATUS IN ('Marked','Pre-Processed','On Machine')
             AND (van.likelyparty IN ('sd','ld')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore >= 50))
             THEN 1 ELSE 0 END) AS DemVotedCount,

    SUM(CASE WHEN dal.BALLOT_RECEIPT_DATE IS NOT NULL
             AND dal.BALLOT_STATUS IN ('Marked','Pre-Processed','On Machine')
             AND (van.likelyparty IN ('sr','lr')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore < 50))
             THEN 1 ELSE 0 END) AS RepVotedCount,

    SUM(CASE WHEN dal.BALLOT_RECEIPT_DATE IS NOT NULL
             AND dal.BALLOT_STATUS IN ('Marked','Pre-Processed','On Machine')
             AND (van.likelyparty IS NULL
                 OR van.likelyparty NOT IN ('sd','ld','sr','lr','nd','U','I')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore IS NULL))
             THEN 1 ELSE 0 END) AS UnknownVotedCount,

    -- In-person vs mail breakdown
    SUM(CASE WHEN dal.BALLOT_STATUS = 'On Machine' THEN 1 ELSE 0 END) AS InPersonCount,
    SUM(CASE WHEN dal.BALLOT_STATUS IN ('Marked','Pre-Processed') THEN 1 ELSE 0 END) AS MailCount,
    SUM(CASE WHEN dal.BALLOT_STATUS = 'On Machine'
             AND (van.likelyparty IN ('sd','ld')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore >= 50))
             THEN 1 ELSE 0 END) AS InPersonDem,
    SUM(CASE WHEN dal.BALLOT_STATUS = 'On Machine'
             AND (van.likelyparty IN ('sr','lr')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore < 50))
             THEN 1 ELSE 0 END) AS InPersonRep,
    SUM(CASE WHEN dal.BALLOT_STATUS IN ('Marked','Pre-Processed')
             AND (van.likelyparty IN ('sd','ld')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore >= 50))
             THEN 1 ELSE 0 END) AS MailDem,
    SUM(CASE WHEN dal.BALLOT_STATUS IN ('Marked','Pre-Processed')
             AND (van.likelyparty IN ('sr','lr')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore < 50))
             THEN 1 ELSE 0 END) AS MailRep,

    SUM(CASE WHEN dal.BALLOT_RECEIPT_DATE IS NULL AND dal.ONGOING = 'True'
             AND (van.likelyparty IN ('sd','ld')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore >= 50))
             THEN 1 ELSE 0 END) AS DemOutCount,

    SUM(CASE WHEN dal.BALLOT_RECEIPT_DATE IS NULL AND dal.ONGOING = 'True'
             AND (van.likelyparty IN ('sr','lr')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore < 50))
             THEN 1 ELSE 0 END) AS RepOutCount,

    SUM(CASE WHEN dal.BALLOT_RECEIPT_DATE IS NULL AND dal.ONGOING = 'True'
             AND (van.likelyparty IS NULL
                 OR van.likelyparty NOT IN ('sd','ld','sr','lr','nd','U','I')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore IS NULL))
             THEN 1 ELSE 0 END) AS UnknownOutCount

FROM van
RIGHT JOIN dal_deduped dal
    ON van.StateFileID = dal.IDENTIFICATION_NUMBER
LEFT JOIN Voter.dbo.County_Twist ct
    ON dal.LOCALITY_NAME = ct.LOCALITYNAME
WHERE dal.rn = 1

GROUP BY COALESCE(van.CountyName, ct.CountyName),
         COALESCE(van.PrecinctName, dal.PRECINCT_NAME),
         dal.CONG_CODE_VALUE
ORDER BY COALESCE(van.CountyName, ct.CountyName),
         COALESCE(van.PrecinctName, dal.PRECINCT_NAME);
"""

def build_daily_query(election_name, cycle_start):
    return f"""
-- Deduplicate Daily_Absentee_List: one row per voter, most resolved status wins.
WITH dal_deduped AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY IDENTIFICATION_NUMBER
               ORDER BY
                   CASE BALLOT_STATUS
                       WHEN 'Marked'        THEN 1
                       WHEN 'Pre-Processed' THEN 2
                       WHEN 'On Machine'    THEN 3
                       WHEN 'Unmarked'      THEN 4
                       ELSE                      5
                   END,
                   BALLOT_RECEIPT_DATE DESC
           ) AS rn
    FROM Daily_Absentee_List
    WHERE ELECTION_NAME = '{election_name}'
      AND BALLOT_STATUS NOT IN ('Deleted', 'Not Issued')
)
SELECT
    CASE
        WHEN CAST(dal.BALLOT_RECEIPT_DATE AS DATE) < '{cycle_start}'
        THEN CAST('{cycle_start}' AS DATE)
        ELSE CAST(dal.BALLOT_RECEIPT_DATE AS DATE)
    END AS ReturnDate,

    DATENAME(WEEKDAY,
        CASE
            WHEN CAST(dal.BALLOT_RECEIPT_DATE AS DATE) < '{cycle_start}'
            THEN CAST('{cycle_start}' AS DATE)
            ELSE CAST(dal.BALLOT_RECEIPT_DATE AS DATE)
        END
    ) AS DayOfWeek,

    COUNT(*) AS TotalReturned,

    SUM(CASE WHEN dal.BALLOT_STATUS = 'On Machine' THEN 1 ELSE 0 END) AS InPersonTotal,
    SUM(CASE WHEN dal.BALLOT_STATUS = 'On Machine'
             AND (van.likelyparty IN ('sd','ld')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore >= 50))
             THEN 1 ELSE 0 END) AS InPersonDem,
    SUM(CASE WHEN dal.BALLOT_STATUS = 'On Machine'
             AND (van.likelyparty IN ('sr','lr')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore < 50))
             THEN 1 ELSE 0 END) AS InPersonRep,
    SUM(CASE WHEN dal.BALLOT_STATUS = 'On Machine'
             AND (van.likelyparty IS NULL
                 OR van.likelyparty NOT IN ('sd','ld','sr','lr','nd','U','I')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore IS NULL))
             THEN 1 ELSE 0 END) AS InPersonUnknown,

    SUM(CASE WHEN dal.BALLOT_STATUS IN ('Marked','Pre-Processed') THEN 1 ELSE 0 END) AS MailTotal,
    SUM(CASE WHEN dal.BALLOT_STATUS IN ('Marked','Pre-Processed')
             AND (van.likelyparty IN ('sd','ld')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore >= 50))
             THEN 1 ELSE 0 END) AS MailDem,
    SUM(CASE WHEN dal.BALLOT_STATUS IN ('Marked','Pre-Processed')
             AND (van.likelyparty IN ('sr','lr')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore < 50))
             THEN 1 ELSE 0 END) AS MailRep,
    SUM(CASE WHEN dal.BALLOT_STATUS IN ('Marked','Pre-Processed')
             AND (van.likelyparty IS NULL
                 OR van.likelyparty NOT IN ('sd','ld','sr','lr','nd','U','I')
                 OR (van.likelyparty IN ('nd','U','I') AND van.SupportScore IS NULL))
             THEN 1 ELSE 0 END) AS MailUnknown

FROM van
RIGHT JOIN dal_deduped dal
    ON van.StateFileID = dal.IDENTIFICATION_NUMBER

WHERE dal.rn = 1
  AND dal.BALLOT_RECEIPT_DATE IS NOT NULL
  AND CAST(dal.BALLOT_RECEIPT_DATE AS DATE) < CAST(GETDATE() AS DATE)
  AND dal.BALLOT_STATUS IN ('Marked', 'Pre-Processed', 'On Machine')

GROUP BY
    CASE
        WHEN CAST(dal.BALLOT_RECEIPT_DATE AS DATE) < '{cycle_start}'
        THEN CAST('{cycle_start}' AS DATE)
        ELSE CAST(dal.BALLOT_RECEIPT_DATE AS DATE)
    END,
    DATENAME(WEEKDAY,
        CASE
            WHEN CAST(dal.BALLOT_RECEIPT_DATE AS DATE) < '{cycle_start}'
            THEN CAST('{cycle_start}' AS DATE)
            ELSE CAST(dal.BALLOT_RECEIPT_DATE AS DATE)
        END
    )

ORDER BY ReturnDate;
"""

NUM_COLS = [
    "TotalMatchedVoters",
    "DemVotedCount","RepVotedCount","UnknownVotedCount",
    "InPersonCount","MailCount","InPersonDem","InPersonRep","MailDem","MailRep",
    "DemOutCount","RepOutCount","UnknownOutCount",
]

def connect():
    conn_str = (
        f"DRIVER={{{ODBC_DRIVER}}};"
        f"SERVER={SERVER};"
        f"DATABASE={DATABASE};"
        "Trusted_Connection=yes;"
    )
    return pyodbc.connect(conn_str)

def add_totals(df):
    df = df.copy()
    df["VotedCount"] = df["DemVotedCount"] + df["RepVotedCount"] + df["UnknownVotedCount"]
    df["OutCount"]   = df["DemOutCount"]   + df["RepOutCount"]   + df["UnknownOutCount"]
    return df

def add_cumulative(df):
    df = df.copy()
    df = df.sort_values("ReturnDate").reset_index(drop=True)
    cum_cols = ["TotalReturned","InPersonTotal","MailTotal",
                "InPersonDem","InPersonRep","InPersonUnknown",
                "MailDem","MailRep","MailUnknown"]
    for col in cum_cols:
        df[f"Cum{col}"] = df[col].cumsum().astype(int)
    df["ReturnDate"] = df["ReturnDate"].astype(str)
    return df

def df_to_records(df):
    return json.loads(df.to_json(orient="records"))

def aggregate_counties(df):
    grp_cols = NUM_COLS + ["VotedCount","OutCount"]
    return (
        df.groupby("CountyName")[grp_cols]
        .sum()
        .reset_index()
        .sort_values("CountyName")
    )

def aggregate_cds(df):
    """One row per congressional district, with the counties feeding it."""
    grp_cols = NUM_COLS + ["VotedCount", "OutCount"]
    cd = (
        df.groupby("CD")[grp_cols]
        .sum()
        .reset_index()
        .sort_values("CD")
    )
    counties = (
        df[df["VotedCount"] + df["OutCount"] > 0]
        .groupby("CD")["CountyName"]
        .nunique()
        .rename("CountyCount")
        .reset_index()
    )
    cd = cd.merge(counties, on="CD", how="left")
    cd["CountyCount"] = cd["CountyCount"].fillna(0).astype(int)
    cd["Target"] = cd["CD"].isin(TARGET_CDS)
    return cd


def aggregate_county_by_cd(df):
    """County x CD, for drilling into a district.

    The precinct -> CD attribution is exact (precincts do not split CDs), but
    CONG_CODE_VALUE itself is noisy: a county that is really one or two CDs
    picks up single stray records in others. A county keeps its dominant row;
    every other row needs MIN_CD_BALLOTS.

    Unlike the cure dashboard there are NO exemptions here - not for target
    CDs, and not for a county's dominant row. Cure records are scarce enough
    that three of them in CD 02 is worth surfacing; ballots are not. And the
    Summary tab already lists every county, so this view has no duty to make
    each one appear; a county whose only ballots are strays should simply not
    show up in a district it is not in.
    """
    grp_cols = NUM_COLS + ["VotedCount", "OutCount"]
    out = (
        df.groupby(["CD", "CountyName"])[grp_cols]
        .sum()
        .reset_index()
    )
    out["Ballots"] = out["VotedCount"] + out["OutCount"]
    out = out[out["Ballots"] > 0]

    kept = out[out["Ballots"] >= MIN_CD_BALLOTS].drop(columns=["Ballots"])
    return kept.sort_values(["CD", "CountyName"])


def aggregate_statewide(df):
    grp_cols = NUM_COLS + ["VotedCount","OutCount"]
    return df[grp_cols].sum().to_dict()

def write_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    print(f"  wrote {path}")

def main():
    print(f"Connecting to {SERVER}\\{DATABASE} ...")
    conn = connect()

    election_name = get_election_name(conn)
    print(f"Election:  {election_name}")

    cfg = ELECTION_CONFIG.get(election_name)
    if cfg is None:
        raise ValueError(
            f"No ELECTION_CONFIG entry for {election_name!r}. "
            f"Add one to build_absentee_json.py before running."
        )
    cycle_start = cfg["cycle_start"]
    print(f"Cycle start: {cycle_start}  (earlier returns bin onto this date)")

    print("Running summary query ...")
    df = pd.read_sql(build_summary_query(election_name), conn)
    print(f"  {len(df):,} precinct rows")
    df = add_totals(df)

    print("Running daily trend query ...")
    daily_df = pd.read_sql(build_daily_query(election_name, cycle_start), conn)
    print(f"  {len(daily_df):,} daily rows")
    conn.close()

    os.makedirs(DATA_DIR, exist_ok=True)

    write_json(df_to_records(df),                                os.path.join(DATA_DIR, "precincts.json"))
    write_json(df_to_records(aggregate_counties(df)),            os.path.join(DATA_DIR, "counties.json"))

    cd_df = aggregate_cds(df)
    write_json({
        "targets":     TARGET_CDS,
        "min_ballots": MIN_CD_BALLOTS,
        "districts":   df_to_records(cd_df),
        "by_county":   df_to_records(aggregate_county_by_cd(df)),
    },                                                           os.path.join(DATA_DIR, "cds.json"))
    print(f"  {len(cd_df)} congressional districts "
          f"({int(cd_df['Target'].sum())} targeted)")

    statewide = aggregate_statewide(df)
    statewide["CountyCount"]   = int(df["CountyName"].nunique())
    statewide["PrecinctCount"] = int(len(df))
    write_json(statewide,                                        os.path.join(DATA_DIR, "summary.json"))

    daily_df = add_cumulative(daily_df)
    write_json(df_to_records(daily_df),                          os.path.join(DATA_DIR, "daily.json"))

    write_json({
        "last_updated":   datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source":         f"{SERVER} / {DATABASE}",
        "election_name":  election_name,
        "election_label": cfg.get("label", DEFAULT_LABEL),
        "election_day":   cfg["election_day"],
        "cycle_start":    cycle_start,
    },                                                           os.path.join(DATA_DIR, "metadata.json"))

    print("\nDone. Commit and push with GitHub Desktop to update the live dashboard.")

if __name__ == "__main__":
    main()
