"""mining_watch.climate 单元测试（unittest，无网络依赖）。"""
from __future__ import annotations

import math
import unittest
from datetime import date, timedelta

import numpy as np

from mining_watch import climate as cl


class DailyFeatureTests(unittest.TestCase):
    def test_windows_snow_and_freeze(self):
        doc = {
            "daily": {
                "time": ["2025-01-01", "2025-01-02", "2025-01-03"],
                "precipitation_sum": [0.0, 10.0, 0.0],
                "snowfall_sum": [0.0, 2.0, 0.0],
                "snow_depth_max": [0.0, 0.1, 0.0],
                "cloud_cover_mean": [10.0, 90.0, 30.0],
                "temperature_2m_mean": [-1.0, 0.5, 2.0],
                "temperature_2m_min": [-5.0, -2.0, 0.0],
                "temperature_2m_max": [1.0, 3.0, 5.0],
                "soil_moisture_0_to_7cm_mean": [0.2, 0.3, 0.25],
                "et0_fao_evapotranspiration": [0.1, 0.0, 0.2],
            }
        }
        rows = cl.build_daily_features(doc)
        self.assertEqual(len(rows), 3)
        self.assertAlmostEqual(rows[2]["precip_7d"], 10.0)
        self.assertAlmostEqual(rows[1]["snowfall_7d"], 2.0)
        self.assertAlmostEqual(rows[1]["snow_depth_cm"], 10.0)
        self.assertTrue(rows[1]["freeze_thaw"])
        self.assertEqual(rows[2]["dry_spell_days"], 1)

    def test_rain_on_snow(self):
        doc = {
            "daily": {
                "time": ["2025-01-01"],
                "precipitation_sum": [5.0],
                "snowfall_sum": [0.0],
                "snow_depth_max": [0.2],
                "cloud_cover_mean": [80.0],
                "temperature_2m_mean": [2.0],
                "temperature_2m_min": [1.0],
                "temperature_2m_max": [3.0],
                "soil_moisture_0_to_7cm_mean": [0.35],
                "et0_fao_evapotranspiration": [0.1],
            }
        }
        row = cl.build_daily_features(doc)[0]
        self.assertTrue(row["rain_on_snow"])
        self.assertAlmostEqual(row["snow_depth_cm"], 20.0)


class QualityTests(unittest.TestCase):
    def test_clamp_nan(self):
        self.assertEqual(cl.clamp(float("nan")), 0.0)
        self.assertEqual(cl.clamp(2.0), 1.0)
        self.assertEqual(cl.clamp(-1.0), 0.0)

    def test_scl_fractions(self):
        arr = np.array([[4, 5, 7, 8, 9, 10, 3, 11, 6, 0, 1, 2, 255]])
        frac = cl.scl_fractions(arr)
        self.assertAlmostEqual(frac["valid"], 0.25, places=3)
        self.assertAlmostEqual(frac["cloud"], 2 / 12, places=3)
        self.assertAlmostEqual(frac["thin_cloud"], 1 / 12, places=3)
        self.assertAlmostEqual(frac["shadow"], 1 / 12, places=3)
        self.assertAlmostEqual(frac["snow"], 1 / 12, places=3)
        self.assertAlmostEqual(frac["nodata"], 2 / 12, places=3)
        self.assertAlmostEqual(frac["water"], 1 / 12, places=3)

    def test_quality_prefers_scl(self):
        q = cl.build_quality(valid_frac=0.1, scl={"valid": 0.8, "cloud": 0.1, "thin_cloud": 0.05, "snow": 0.0}, cloud_pct=99)
        self.assertEqual(q["source"], "scl")
        self.assertAlmostEqual(q["cloud_frac"], 0.15)
        self.assertAlmostEqual(q["valid_frac"], 0.8)

    def test_snow_hard_constraint(self):
        profile = cl.profile_for("open_pit")
        q = cl.build_quality(scl={"valid": 0.5, "cloud": 0.1, "snow": 0.6})
        obs = cl.scene_observation(profile, {"snow_depth_cm": 0.0, "precip_7d": 0.0, "soil_moisture": 0.2}, q)
        self.assertEqual(obs["optical_suitability"], 0.0)
        self.assertFalse(obs["usable_optical"])

    def test_detection_power(self):
        profile = cl.profile_for("open_pit")
        adequate = cl.detection_power(profile, 12, 15)
        self.assertEqual(adequate["level"], "adequate")
        self.assertGreater(adequate["power"], 0.75)
        self.assertEqual(cl.detection_power(profile, 0, 0)["level"], "insufficient")
        penalized = cl.detection_power(profile, 12, 120)
        self.assertLess(penalized["power"], adequate["power"])
        self.assertLess(penalized["gap_factor"], 1.0)


class CandidateTests(unittest.TestCase):
    def test_signal_without_delta_uses_area_and_persistence(self):
        profile = cl.profile_for("open_pit")
        signal = cl.signal_strength(profile, {"area_ha": 10.0, "persistent_bare": True}, float("nan"))
        self.assertAlmostEqual(signal, 0.35, places=4)

    def test_static_candidate_uses_aggregate_observation(self):
        profile = cl.profile_for("open_pit")
        props = {
            "evidence_score": 18.0,
            "evidence_tier": "C",
            "area_ha": 10.0,
            "persistent_bare": True,
            "delta_bsi": None,
        }
        out = cl.adjust_candidate(
            profile,
            props,
            alarm_weather={"month": None, "cloud_cover": 40.0, "snow_depth_cm": 0.0},
            reference_weather={},
            n_usable=10,
            gap_days=5,
            observation_basis="target_year_summer_aggregate",
        )
        self.assertEqual(out["climate_observation_basis"], "target_year_summer_aggregate")
        self.assertEqual(out["climate_observation_samples"], 10)
        self.assertNotIn("detection_insufficient", out["climate_flags"])
        self.assertNotIn("no_usable_optical", out["climate_flags"])
        self.assertGreater(out["climate_adjusted_score"], 0.0)

    def test_rank_adjusted(self):
        def feat(score, evidence, area):
            return {
                "type": "Feature",
                "properties": {
                    "climate_adjusted_score": score,
                    "evidence_score": evidence,
                    "area_ha": area,
                },
            }

        ranked = cl.rank_adjusted([feat(10, 80, 1), feat(30, 20, 2), feat(20, 50, 3)])
        self.assertEqual(ranked[0]["properties"]["climate_rank"], 1)
        self.assertEqual(ranked[0]["properties"]["area_ha"], 2)
        self.assertGreater(ranked[0]["properties"]["climate_rank_change"], 0)


class WeatherModelTests(unittest.TestCase):
    def test_weather_model_applies_when_covariate_helps(self):
        start = date(2025, 1, 1)
        dates, values, index = [], [], {}
        for i in range(0, 360, 6):
            d = start + timedelta(days=i)
            rain = 0.0 if i % 30 < 15 else 15.0
            y = 0.002 * i + (0.02 if rain > 0 else 0.0)
            row = {
                "date": d.isoformat(),
                "precip_7d": rain,
                "soil_moisture": 0.2 + (0.1 if rain else 0.0),
                "snow_depth_cm": 0.0,
                "cloud_cover": 20.0 + (50.0 if rain else 0.0),
            }
            dates.append(d.isoformat())
            values.append(y)
            index[d.isoformat()] = row
        model = cl.fit_weather_model(dates, values, index, "bsi", min_n=30)
        self.assertTrue(model["applied"])
        self.assertGreater(model["improvement_pct"], 2.0)
        effect_wet = cl.weather_model_effect(model, index[dates[-1]])
        effect_dry = cl.weather_model_effect(model, {**index[dates[-1]], "precip_7d": 0.0, "soil_moisture": 0.2})
        self.assertNotAlmostEqual(effect_wet, effect_dry)

    def test_model_falls_back_without_signal(self):
        start = date(2025, 1, 1)
        dates = [(start + timedelta(days=i)).isoformat() for i in range(0, 120, 4)]
        values = [math.sin(i / 10.0) * 0.05 for i in range(len(dates))]
        index = {
            d: {"date": d, "precip_7d": 0.0, "soil_moisture": 0.25, "snow_depth_cm": 0.0, "cloud_cover": 30.0}
            for d in dates
        }
        model = cl.fit_weather_model(dates, values, index, "bsi", min_n=30)
        self.assertFalse(model["applied"])
        self.assertEqual(cl.weather_model_effect(model, index[dates[0]]), 0.0)


if __name__ == "__main__":
    unittest.main()