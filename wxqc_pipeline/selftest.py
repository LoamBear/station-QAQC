"""Generate synthetic NevCAN-shaped data and run the full pipeline."""
import numpy as np
import pandas as pd
from pathlib import Path

import wxqc

rng = np.random.default_rng(0)
n = 2000
idx = pd.date_range("2018-10-01", periods=n, freq="10min")

df = pd.DataFrame({"datetime_PST": idx, "stationid": "nep2"})
df["SR_avg_Wm2"] = np.clip(rng.normal(300, 200, n), 0, None)
df["SWin_Wm2"] = np.clip(rng.normal(280, 180, n), -5, None)
df["SWout_Wm2"] = np.clip(rng.normal(80, 60, n), -5, None)
df["T_avg_C"] = rng.normal(10, 8, n)
df["T_max_C"] = df["T_avg_C"] + np.abs(rng.normal(3, 1, n))
df["T_min_C"] = df["T_avg_C"] - np.abs(rng.normal(3, 1, n))
df["T2m_avg_C"] = df["T_avg_C"] + rng.normal(0, 1, n)
df["RH_avg_pct"] = np.clip(rng.normal(60, 25, n), 0, 130)   # some > 100
df["BP_avg_mbar"] = rng.normal(780, 3, n)     # within nep2's BP range [752.2, 796.2]
df["Precip_mm"] = np.cumsum(np.clip(rng.normal(0.0, 0.3, n), 0, None))
df["Geonor_mm"] = np.cumsum(np.clip(rng.normal(0.0, 0.4, n), 0, None))
df["P_geonor_hzstdv"] = np.abs(rng.normal(0.5, 0.8, n))
df["snowdepth_avg_mm"] = np.clip(np.cumsum(rng.normal(0, 5, n)) + 500, 0, None)
df["swe_mm"] = np.clip(df["snowdepth_avg_mm"] * 0.3, 0, None)

# inject some bad values + gaps
df.loc[100:103, "T_avg_C"] = np.nan                  # short gap (fillable at L2)
df.loc[500:540, "T_avg_C"] = np.nan                  # long gap (not filled)
df.loc[200, "T_avg_C"] = 200.0                       # out of range
df.loc[300, "T_avg_C"] = df.loc[299, "T_avg_C"] + 99 # ROC spike

# BP discontinuity check: BP_roc=1.3, discontinuity_limit=3 -> a 2-sample gap is
# allowed a jump of up to 1.3*2=2.6 before it's treated as a sensor discontinuity
# rather than a bridgeable gap. Flat buffers around each gap (instead of the iid
# noise the base series uses) keep every neighboring step under BP_roc so the
# plain ROC re-check at L2 doesn't wipe the very points the test depends on.
df.loc[690:701, "BP_avg_mbar"] = 780.0
df.loc[700:701, "BP_avg_mbar"] = np.nan
df.loc[702:713, "BP_avg_mbar"] = 782.0               # jump=2.0 <= 2.6 -> fillable
df.loc[790:801, "BP_avg_mbar"] = 780.0
df.loc[800:801, "BP_avg_mbar"] = np.nan
df.loc[802:813, "BP_avg_mbar"] = 790.0               # jump=10 > 2.6 -> discontinuity

# interp_limit=6 is the real fill window; discontinuity_limit=3 only adds the
# free-floating-anchor requirement for gaps longer than that (still <= 6).
df.loc[890:899, "BP_avg_mbar"] = 780.0
df.loc[900:904, "BP_avg_mbar"] = np.nan
df.loc[905:915, "BP_avg_mbar"] = 781.0               # gap=5, jump=1.0 <= 6.5,
                                                      # stable anchors -> fillable
df.loc[918:919, "BP_avg_mbar"] = np.nan              # isolates 920 on its left
df.loc[920, "BP_avg_mbar"] = 781.0                   # free-floating single point
df.loc[921:925, "BP_avg_mbar"] = np.nan              # gap=5, jump=1.0 <= 6.5 but
df.loc[926:936, "BP_avg_mbar"] = 782.0               # anchored on a lone sample -> blocked

# min/max/avg consistency check (sensor_group "T"): a flat buffer keeps ROC
# from wiping the samples the test depends on, same trick as the BP buffers
# above. avg=20 at 600 is outside [min=7, max=13] but still within Tavg's own
# range/ROC bounds, so it survives the per-variable checks and reaches the
# group-consistency pass, which should flag all three as Suspect.
df.loc[595:605, "T_max_C"] = 13.0
df.loc[595:605, "T_min_C"] = 7.0
df.loc[595:605, "T_avg_C"] = 10.0
df.loc[600, "T_avg_C"] = 20.0

# incomplete min/max/avg trio: a sibling that normally exists (T_max_C) goes
# missing for a few samples while its group-mates are present -- should flag
# the whole trio Suspect even though nothing about T_avg_C/T_min_C's own
# consistency is wrong. Only meaningful before L2's gap-fill (a short enough
# gap would otherwise get filled back in), so this is checked at L1.
df.loc[1395:1405, "T_avg_C"] = 10.0
df.loc[1395:1405, "T_min_C"] = 7.0
df.loc[1395:1405, "T_max_C"] = 13.0
df.loc[1400:1402, "T_max_C"] = np.nan

# interpolated (gap-filled) values are checked against sibling min/max too --
# already covered by _apply_group_consistency running after gap-fill at L2,
# but worth a dedicated case: T_avg_C's own gap-fill endpoints (15) are
# unrelated to T_max_C/T_min_C's flat value (8/4) across that same window, so
# the interpolated avg should still be caught once filled.
df.loc[1480:1500, "T_max_C"] = 8.0
df.loc[1480:1500, "T_min_C"] = 4.0
df.loc[1480:1489, "T_avg_C"] = 15.0
df.loc[1490:1494, "T_avg_C"] = np.nan                # 5-sample gap, fillable
df.loc[1495:1500, "T_avg_C"] = 15.0

# timestamp continuity: a genuine multi-hour gap in the raw timestamp sequence
# (rows simply absent, not NaN'd) must not get bridged by treating the next
# real reading as though it arrived 10 minutes later -- the exact nep8
# 2023-05-21 bug. regrid_timestamps (inside run_level2) inserts real NaN rows
# for the gap so ROC/fill_short_gaps compare true time-adjacent samples.
df.loc[df.index[-1], "T_avg_C"] = 10.0               # last original reading, known value
gap_end = idx[-1] + pd.Timedelta("2D")
extra_idx = pd.date_range(gap_end, periods=10, freq="10min")
extra = pd.DataFrame({"datetime_PST": extra_idx, "stationid": "nep2", "T_avg_C": 10.5})
for c in df.columns:
    if c not in extra.columns:
        extra[c] = np.nan
df = pd.concat([df, extra], ignore_index=True)

# minimal variables: only the columns we created above
specs = [s for s in wxqc.load_variables("config/nevcan/variables.csv")
         if s.value_col in df.columns]
thr = wxqc.load_thresholds("config/nevcan/thresholds.csv")

df = wxqc.run_level15(df, specs, thr, "nep2")
assert "T_avg_C_L1" in df and "T_avg_flag_L1" in df
assert pd.isna(df.loc[200, "T_avg_C_L1"]), "out-of-range not removed"
assert pd.isna(df.loc[300, "T_avg_C_L1"]), "ROC spike not removed"
clamp_candidates = df["RH_avg_pct"] > 105
clamp_survivors = clamp_candidates & df["RH_avg_pct_L1"].notna()
assert clamp_survivors.any(), "test setup should leave some clamp-worthy RH values unremoved by ROC"
assert (df.loc[clamp_survivors, "RH_avg_pct_L1"] == 100).all(), "RH clamp failed"

assert (df.loc[600, ["T_avg_flag_L1", "T_max_flag_L1", "T_min_flag_L1"]] == "S").all(), \
    "avg outside [min, max] should flag all three sensor_group members Suspect"
assert not (df.loc[599, ["T_avg_flag_L1", "T_max_flag_L1", "T_min_flag_L1"]] == "S").any(), \
    "consistent neighboring sample should not be flagged by the group check"

# cross-sensor dependency: RH_avg_pct depends_on "T". At 600 nothing about the
# (perfectly ordinary) RH reading itself is wrong -- it should be flagged
# Suspect purely because T's own group-consistency check flagged T there.
assert df.loc[600, "RH_avg_flag_L1"] == "S", \
    "RH should be flagged Suspect when its depends_on group (T) is Suspect"
assert df.loc[599, "RH_avg_flag_L1"] != "S", \
    "RH should not be flagged Suspect when T is not Suspect"

assert (df.loc[1400:1402, ["T_avg_flag_L1", "T_min_flag_L1", "T_max_flag_L1"]] == "S").all().all(), \
    "a missing sibling (T_max_C) with others present should flag the whole trio Suspect"
assert not (df.loc[1395, ["T_avg_flag_L1", "T_min_flag_L1", "T_max_flag_L1"]] == "S").any(), \
    "a fully complete trio should not be flagged by the incompleteness check"

df = wxqc.run_level2(df, specs, thr, "nep2")
assert df.loc[100:103, "T_avg_C_L2"].notna().all(), "short gap not filled at L2"
assert df.loc[510:530, "T_avg_C_L2"].isna().all(), "long gap should stay NaN"

assert (df.loc[1490:1494, "T_avg_flag_L2"] == "S").all(), \
    "an interpolated (gap-filled) value that ends up outside sibling min/max should be caught"

gap_region = df[(df["datetime_PST"] > idx[-1]) & (df["datetime_PST"] < gap_end)]
assert len(gap_region) == 287, f"expected 287 inserted rows for the 2-day gap, got {len(gap_region)}"
assert gap_region["timestamp_generated"].all(), "inserted gap rows should be marked timestamp_generated"
assert gap_region["T_avg_C_L2"].isna().all(), \
    "a multi-hour real gap must never be bridged into a single fabricated value, even after regridding"
post_gap = df[df["datetime_PST"] == extra_idx[0]]
assert not post_gap.empty and not bool(post_gap["timestamp_generated"].iloc[0]), \
    "a genuinely-present post-gap row should not be marked generated"

assert df.loc[700:701, "BP_avg_mbar_L2"].notna().all(), "small jump should still fill"
assert df.loc[800:801, "BP_avg_mbar_L2"].isna().all(), "discontinuous jump should not be interpolated"
assert (df.loc[800:801, "BP_avg_flag_L2"] == "S").all(), "discontinuous gap should be flagged S for review"

assert df.loc[900:904, "BP_avg_mbar_L2"].notna().all(), "5-sample gap with stable anchors should still fill up to interp_limit=6"
assert df.loc[921:925, "BP_avg_mbar_L2"].isna().all(), "5-sample gap anchored on a free-floating point should not fill"
assert (df.loc[921:925, "BP_avg_flag_L2"] == "S").all(), "free-floating-anchor gap should be flagged S for review"

# flags
# Removals (value -> NaN) classify as S; modifications (clamp/fill) classify as E.
f = df["T_avg_flag_L1"]
print("L1 T_avg flag counts:", dict(f.value_counts()))
assert (f == "S").any(), "removed/missing values should be flagged S"
rh = df["RH_avg_flag_L1"]
print("L1 RH_avg flag counts:", dict(rh.value_counts()))
assert (rh == "E").any(), "clamped RH values should be flagged E"

# L3 manual edits
edits = wxqc.load_manual_edits("config/nevcan/manual_edits.csv")
df = wxqc.apply_manual_edits(df, edits, specs, "nep2")
assert "SR_avg_Wm2_L3" in df
# the scale edit (x0.505) after 2019-05-16 -- our synthetic data is 2018 only,
# so just confirm the column exists and flags derived
print("L3 SR flag counts:", dict(df["SR_avg_flag_L3"].value_counts()))
print("L3 ST2 flag counts:", dict(df["ST2_avg_flag_L3"].value_counts()) if "ST2_avg_flag_L3" in df else "n/a")

out = wxqc.export_columns(df, specs, "_L3")
print("L3 export columns:", len(out.columns))
# hover-reason annotations: the RH clamp at L1 (action clamp_high_100) is
# kept-but-Edited, so it should get a marker whose text names the check.
rh_reason = wxqc.explain_flag(df, next(s for s in specs if s.value_col == "RH_avg_pct"),
                              specs, thr, "nep2", "_L1", "")
clamped = (df["RH_avg_pct"] > 100) & df["RH_avg_pct_L1"].notna()
assert clamped.any(), "test setup should have produced some clamped RH values"
assert rh_reason[clamped].str.contains("range check").all(), \
    "a clamped-but-kept value should explain itself via the range check, not silently"

print("OK: pipeline ran end to end")
