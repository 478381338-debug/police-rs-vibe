"""平台气候增强接口的最小契约测试。

不重跑遥感流水线，只验证读取层：候选总数、坐标范围、排序分字段和报告可用性。
"""
from __future__ import annotations

import unittest

from app import yantai_api


class ClimateApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = yantai_api._climate()
        if cls.data.get("status") != "ok":
            raise unittest.SkipTest("data/yantai_climate 尚未生成")

    def test_status_ok(self):
        self.assertEqual(self.data["status"], "ok")

    def test_candidate_count_matches_aois(self):
        expected = sum(int(a.get("n_candidates") or 0) for a in self.data["aois"])
        self.assertEqual(self.data["candidate_count"], expected)
        self.assertEqual(len(self.data["candidates"]["features"]), expected)

    @staticmethod
    def _positions(coords):
        if not coords:
            return []
        if isinstance(coords[0], (int, float)):
            return [coords]
        out = []
        for part in coords:
            out.extend(ClimateApiTest._positions(part))
        return out

    def test_candidates_are_wgs84_and_explainable(self):
        for feat in self.data["candidates"]["features"]:
            positions = self._positions((feat.get("geometry") or {}).get("coordinates"))
            self.assertTrue(positions, "候选缺少几何")
            for lon, lat in positions[:8]:
                self.assertLess(abs(float(lon)), 180.0, "几何可能仍是 UTM 米制坐标")
                self.assertLess(abs(float(lat)), 90.0, "几何可能仍是 UTM 米制坐标")
            props = feat["properties"]
            self.assertIn("climate_tier", props)
            self.assertIn("climate_adjusted_score", props)
            self.assertIn("climate_observation_level", props)
            self.assertTrue(props.get("reason"), "候选缺少页面解释字段")
            self.assertIn("排序分", props["reason"][-1]["value"])

    def test_types_and_models(self):
        self.assertEqual(len(self.data["types"]), 6)
        self.assertGreaterEqual(self.data["model_total"], len(self.data["aois"]))
        self.assertTrue(all("applied" in m for m in self.data["models"]))

    def test_report_exists_when_ok(self):
        self.assertTrue(yantai_api.CLIMATE_REPORT.exists())


if __name__ == "__main__":
    unittest.main()