"""Compact LBank manual entry presentation and risk-label regressions."""
import unittest
from lbank_compact_alert import format_compact_entry_alert
import lbank_futures_monitor as monitor


class CompactAlertTests(unittest.TestCase):
    def setUp(self):
        self.plan = dict(side="롱", direction=1, strategy="trend_retest",
                         entry_route="continuation", price=82112.9,
                         stop=81946.8, target1=82653.7, target2=82943.0,
                         margin=69.55, notional=2086.50, net_loss=9.35,
                         net_gain1=16.00, net_gain2=18.22)
        self.kw = dict(reference_balance=1871.9017, leverage=30,
                       max_margin_fraction=.15)
        self.args = self.kw  # Existing profit-protection integration fixture.

    def render(self, p=None):
        return format_compact_entry_alert(p or self.plan, "2026-10-16T04:25:01Z",
                                          reference_number=5, **self.kw)

    def test_one_direction_one_route_and_clear_prices(self):
        msg = self.render()
        self.assertTrue(msg.startswith("[LBank BTCUSDT | 🟢 롱 | B · 추세지속형]"))
        self.assertIn("✅ 조건부 진입 검토 가능 · 2단계 확인 통과", msg)
        self.assertIn("10/16 13:25 KST", msg)
        self.assertIn("13:30:01까지 참고", msg)
        self.assertIn("신호번호: #5", msg)
        self.assertIn("진입가: 82,112.9 | 🛑 손절가: 81,946.8", msg)
        self.assertIn("목표가1: 82,653.7 | 목표가2: 82,943.0", msg)
        self.assertIn("1차 순RR 1.71 | 분할 순RR 1.83", msg)
        self.assertIn("예상 총손실 -9.35 USDT", msg)
        self.assertIn("실전 주문 지시 아님", msg)
        self.assertIn("자동주문 없음", msg)
        self.assertLessEqual(len(msg.splitlines()), 10)

    def test_abc_are_routes_not_prices_or_sides(self):
        self.assertIn("A · 돌파형", self.render(dict(self.plan, strategy="breakout")))
        self.assertIn("C · 눌림/지지반등형",
                      self.render(dict(self.plan, entry_route="pullback")))
        short=dict(self.plan, side="숏", direction=-1, price=82000.,
                   stop=82100., target1=81400., target2=81000.)
        self.assertIn("🔴 숏", self.render(short))

    def test_rr_below_minimum_is_no_entry_even_if_levels_are_shown(self):
        self.assertIn("⛔ 진입 금지",self.render(dict(self.plan, net_gain1=10.85)))
        self.assertIn("1차 순RR 1.16",self.render(dict(self.plan, net_gain1=10.85)))
        self.assertIn("⛔ 진입 금지",self.render(dict(self.plan, stop=82500.)))

    def test_countertrend_warning_appears_above_entry(self):
        msg=self.render(dict(self.plan, sampled_context={
            "trend15_pct":-.12, "reversal_confirmed":False}))
        self.assertLess(msg.index("역추세·반전 미확인"),msg.index("📍 진입가:"))
        self.assertLessEqual(len(msg.splitlines()),10)

    def test_risk_engine_policy_unchanged(self):
        self.assertAlmostEqual(monitor.LEVERAGE,30)
        self.assertAlmostEqual(monitor.MAX_MARGIN_FRACTION,.15)
        self.assertAlmostEqual(monitor.RISK_FRACTION,.005)
        prices=[80000.+25*i+(45 if i%2 else 0) for i in range(60)]
        p=monitor.classify(prices)
        self.assertIsNotNone(p)
        self.assertLessEqual(p["margin"],monitor.REF_BALANCE*.15+1e-8)
        self.assertLessEqual(p["net_loss"],monitor.REF_BALANCE*.005+1e-8)
        self.assertAlmostEqual(p["margin"]*30,p["notional"],places=6)
        msg=monitor.format_entry_alert(p,"2026-10-16T04:25:01Z",reference_number=99)
        self.assertIn(f"{p['margin']:,.2f} USDT",msg)
        self.assertIn("신호번호: #99",msg)


if __name__=="__main__":
    unittest.main()
