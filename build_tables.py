"""
build_tables.py
Builds the tidy CSVs for the Tableau and Power BI dashboards from the Steam
review study. Reads copies only; never changes the source files.

Reads   local_original/reviews_raw.csv            (218,380 Apex Legends reviews)
        source_repo/01_data/raw/baseline_summaries.csv  (14 games, lifetime summary)
Writes  dashboard_data/*.csv  and prints the checks against the published figures.

The window, the review-bomb months and the playtime bands copy the rules in
source_repo/02_code/analyse_apex.py so the numbers are the study's own.
"""
import os
import re
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "local_original", "reviews_raw.csv")
BASE = os.path.join(HERE, "source_repo", "01_data", "raw", "baseline_summaries.csv")
OUT = os.path.join(HERE, "dashboard_data")
os.makedirs(OUT, exist_ok=True)

BOMB_START, BOMB_END = "2024-07-01", "2024-09-30"
BINS = [0, 20, 100, 500, 10 ** 9]
LABELS = ["Under 20h", "20 to 100h", "100 to 500h", "Over 500h"]
LANG_NAME = {"english": "English", "schinese": "Simplified script",
             "tchinese": "Traditional script", "japanese": "Japanese",
             "koreana": "Korean", "russian": "Russian", "all": "All languages"}
GAME = "Apex Legends"

df = pd.read_csv(RAW, dtype={"review_text": str})
df["review_text"] = df["review_text"].fillna("")
df["date"] = pd.to_datetime(df["timestamp_created"], unit="s")
df["hours"] = df["playtime_at_review_min"] / 60
df["month"] = df["date"].dt.to_period("M")
df["language_name"] = df["language"].map(LANG_NAME)

start = df.groupby("language")["date"].min().max()
end = df.groupby("language")["date"].max().min()
in_window = df.date.between(start, end)
in_bomb = df.date.between(BOMB_START, BOMB_END)
window = df[in_window]
clean = window[~in_bomb[in_window]].copy()

# ---- table 1: reviews by game, language, month (Apex only: the raw set is Apex)
m = (df.assign(in_shared_window=in_window, review_bomb_period=in_bomb)
       .groupby(["language_name", "month"])
       .agg(reviews=("voted_up", "size"), positive_reviews=("voted_up", "sum"))
       .reset_index())
m["month"] = m["month"].dt.to_timestamp().dt.strftime("%Y-%m-01")
m["positive_share"] = (m.positive_reviews / m.reviews * 100).round(1)
m["review_bomb_period"] = m.month.between("2024-07-01", "2024-09-01")
m["in_shared_window"] = pd.to_datetime(m.month) >= start.to_period("M").to_timestamp()
m.insert(0, "game", GAME)
m = m.rename(columns={"language_name": "language"})
m.to_csv(os.path.join(OUT, "reviews_by_game_language_month.csv"), index=False)

# ---- table 2: positive share by hours played (shared window, bomb removed)
clean["bucket"] = pd.cut(clean["hours"], bins=BINS, labels=LABELS, right=False)
p = (clean.groupby(["language_name", "bucket"], observed=True)
          .agg(reviews=("voted_up", "size"), positive_reviews=("voted_up", "sum"))
          .reset_index())
p["positive_share"] = (p.positive_reviews / p.reviews * 100).round(1)
p.insert(0, "game", GAME)
p = p.rename(columns={"language_name": "language", "bucket": "hours_played_bucket"})
p["bucket_order"] = p.hours_played_bucket.map({l: i + 1 for i, l in enumerate(LABELS)})
p.to_csv(os.path.join(OUT, "playtime_buckets.csv"), index=False)

# ---- table 3: baseline, positive share by game and language (all 14 games)
b = pd.read_csv(BASE, encoding="utf-8")
b["game"] = b["game_name_expected"]
b["language"] = b["language"].map(LANG_NAME)
b["positive_share"] = (b.total_positive / b.total_reviews * 100).round(1)
b = b[["game", "language", "total_reviews", "total_positive", "positive_share"]]
b = b.rename(columns={"total_reviews": "reviews", "total_positive": "positive_reviews"})
eng = b[b.language == "English"].set_index("game").positive_share
b["gap_vs_english_pts"] = (b.game.map(eng) - b.positive_share).round(1)
b.to_csv(os.path.join(OUT, "baseline_by_game_language.csv"), index=False)

# ---- table 4: data quality, one row per cleaning step
tz = {  # connection-topic pattern for Traditional script, before and after the fix
    "after": r"伺服器|伺服|延遲|卡頓|掉線|斷線|網路|丟包|亞服|直連|ping",
    "before": r"伺服器|伺服|延遲|卡頓|掉線|網路|丟包|亞服|直連|ping",
}
neg_t = clean[(clean.voted_up == 0) & (clean.language == "tchinese")].review_text
hit_before = int(neg_t.str.contains(tz["before"], case=False, na=False).sum())
hit_after = int(neg_t.str.contains(tz["after"], case=False, na=False).sum())
removed_bomb = len(window) - len(clean)
dq = pd.DataFrame([
    [1, "Reviews collected from the Steam API (Apex Legends, 5 languages)", "reviews",
     len(df), len(df), "Starting point."],
    [2, "Keep only the window all five languages cover", "reviews",
     len(df), len(window),
     "The collector pulls newest first and stops at a cap, so each language reaches back a different distance. "
     "Comparing raw data compares different time periods. Window: %s to %s." % (start.date(), end.date())],
    [3, "Exclude the review-bombing months (July to September 2024)", "reviews",
     len(window), len(clean),
     "A protest in July 2024 spiked volume and collapsed the score in every language. "
     "It was faking a regional gap (Simplified script gap 20.8 pts with it, 4.1 without)."],
    [4, "Widen the connection-complaint keywords to include 斷線 (Traditional script negative reviews)",
     "negative reviews matching the connection topic", hit_before, hit_after,
     "A sampled review said the game always disconnects using 斷線, a term the keyword list missed, "
     "so the connection theme was undercounted. Rows here are matched reviews, not rows dropped."],
], columns=["step", "cleaning_step", "unit", "rows_before", "rows_after", "reason"])
dq["rows_removed"] = dq.rows_before - dq.rows_after
dq.to_csv(os.path.join(OUT, "data_quality.csv"), index=False, encoding="utf-8-sig")

# ---- checks against the published write-up
print("shared window: %s to %s" % (start.date(), end.date()))
g = lambda lang, d: d[d.language == lang].voted_up.mean() * 100
gap = lambda d, lang: g("english", d) - g(lang, d)
a = b[b.game == GAME].set_index("language")
checks = [
    ("Total reviews collected", 218380, len(df)),
    ("Games in the baseline", 14, b.game.nunique()),
    ("Languages in the Apex review set", 5, df.language.nunique()),
    ("Apex English positive % (Steam lifetime)", 76.6, a.loc["English", "positive_share"]),
    ("Apex Traditional positive %", 40.1, a.loc["Traditional script", "positive_share"]),
    ("Apex gap, English minus Traditional (pts)", 36.5, round(a.loc["English", "positive_share"] - a.loc["Traditional script", "positive_share"], 1)),
    ("Simplified script gap with bomb (pts)", 20.8, round(gap(window, "schinese"), 1)),
    ("Simplified script gap without bomb (pts)", 4.1, round(gap(clean, "schinese"), 1)),
    ("Reviews in shared window, bomb removed", 114739, len(clean)),
    ("English reviews, Steam lifetime (page says 450,782)", 450782, int(a.loc["English", "reviews"])),
]
print("\n%-55s %10s %10s  match" % ("check", "expected", "found"))
for name, exp, found in checks:
    print("%-55s %10s %10s  %s" % (name, exp, found, "yes" if exp == found else "NO"))
print("\nTable rows:", {"months": len(m), "playtime": len(p), "baseline": len(b), "dq": len(dq)})
print(dq[["step", "rows_before", "rows_after", "rows_removed"]].to_string(index=False))


