"""Telegram W-stage messages cannot present stale targets as executable trades."""
import unittest

from lbank_double_bottom_alerts import format_double_bottom


class WTelegramBriefTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "low1": 64149.9, "low2": 64300.0,
            "neckline": 64615.7, "atr": 173.9,
            "volume_softened": 0,
        }
        self.bar = {"start": 1794728100, "close": 64042.1}

    def test_invalidated_W_cancels_long_and_old_targets(self):
        msg = format_double_bottom(
            self.state, "invalidated", self.bar,
            market_trend="하락 우세 (완료 15분·1시간봉)")
        for text in ("판정: 진입대기", "기존 롱 후보 취소",
                     "진입가: 미확정", "손절가: 미확정",
                     "목표가: 미확정", "쌍바닥 저점 방어 실패",
                     "상위 추세: 하락 우세", "새 숏은 별도 손익비 검증",
                     "정식 진입 알람 아님"):
            self.assertIn(text, msg)
        self.assertNotIn("65,006.4", msg)
        self.assertNotIn("64,089.0", msg)
        self.assertNotIn("측정폭 목표", msg)
        self.assertLess(len(msg.splitlines()), 10)

    def test_breakout_remains_unqualified_without_book_validation(self):
        self.bar["close"] = 64800.0
        for phase in ("defense", "rebound", "breakout", "hold"):
            with self.subTest(phase=phase):
                msg = format_double_bottom(self.state, phase, self.bar)
                self.assertIn("진입대기", msg)
                self.assertIn("진입가: 미확정", msg)
                self.assertIn("손절가: 미확정", msg)
                self.assertIn("목표가: 미확정", msg)
                self.assertIn("상위 추세: 미확인", msg)
                self.assertIn("정식 진입 알람 아님", msg)

    def test_invalidated_after_breakout_describes_neckline_failure(self):
        self.bar["close"] = 64450.0
        msg = format_double_bottom(self.state, "invalidated", self.bar)
        self.assertIn("돌파 후 넥라인 방어 실패", msg)
        self.assertIn("기존 롱 후보 취소", msg)


if __name__ == "__main__":
    unittest.main()
