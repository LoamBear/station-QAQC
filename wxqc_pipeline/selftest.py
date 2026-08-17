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

# minimal variables: only the columns we created above
specs = [s for s in wxqc.load_variables("config/nevcan/variables.csv")
         if s.value_col in df.columns]
thr = wxqc.load_thresholds("config/nevcan/thresholds.csv")

df = wxqc.run_level15(df, specs, thr, "nep2")
assert "T_avg_C_L1" in df and "T_avg_flag_L1" in df
assert pd.isna(df.loc[200, "T_avg_C_L1"]), "out-of-range not removed"
assert pd.isna(df.loc[300, "T_avg_C_L1"]), "ROC spike not removed"
assert (df.loc[df["RH_avg_pct"] > 105, "RH_avg_pct_L1"] == 100).all(), "RH clamp failed"

df = wxqc.run_level2(df, specs, thr, "nep2")
assert df.loc[100:103, "T_avg_C_L2"].notna().all(), "short gap not filled at L2"
assert df.loc[510:530, "T_avg_C_L2"].isna().all(), "long gap should stay NaN"

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
print("OK: pipeline ran end to end")
