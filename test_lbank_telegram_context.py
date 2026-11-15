"""Concise Telegram market context is conservative and never an entry signal."""
import unittest

from lbank_telegram_context import compact_trend
from lbank_one_minute_prealert import format_one_minute_prealert, evaluate_one_minute_move
from lbank_fixed_level_alerts import format_level_alert, evaluate_level_cross


class TelegramContextTests(unittest.TestCase):
    def fives(self, direction=-1):
        anchor=1794700000//300*300
        rows=[]
        price=65000.
        for i in range(80):
            close=price+direction*8.
            rows.append(dict(start=anchor+i*300,period=300,
                             open=price,close=close,
                             high=max(price,close)+15.,
                             low=min(price,close)-15.,volume=50.,
                             quality="stream_closed",received_at=anchor+(i+1)*300+2.))
            price=close
        return rows

    def test_missing_history_never_asserts_trend(self):
        self.assertIn("미확인",compact_trend([]))
        self.assertIn("미확인",compact_trend(self.fives()[:20]))

    def test_aligned_15m_and_1h_downtrend_is_reported(self):
        self.assertIn("하락 우세",compact_trend(self.fives(-1)))

    def test_aligned_15m_and_1h_uptrend_is_reported(self):
        self.assertIn("상승 우세",compact_trend(self.fives(1)))

    def test_gap_fails_closed(self):
        rows=self.fives(-1)
        rows[30]["start"]+=300
        self.assertIn("미확인",compact_trend(rows))

    def test_verbose_move_is_now_short_but_actionable_fields_remain_unqualified(self):
        first=1794700000//60*60
        m=[]
        for i in range(21):
            op=100.0;close=100.0
            if i==20:
                close=99.5
            m.append(dict(start=first+i*60,period=60,open=op,
                          high=max(op,close)+.1,low=min(op,close)-.1,
                          close=close,volume=20. if i==20 else 10.,
                          quality="stream_closed",received_at=first+(i+1)*60+2))
        event=evaluate_one_minute_move(m)
        self.assertEqual(event["status"],"candidate")
        msg=format_one_minute_prealert(event,checked_at=first+22*60,
                                       market_trend="하락 우세 (완료 15분·1시간봉)")
        for word in ("진입가:", "손절가:", "목표가:", "판정: 진입대기",
                     "상위 추세: 하락 우세","거래량 2.0배","정식 진입 신호 아님"):
            self.assertIn(word,msg)

    def test_fixed_alert_has_actual_causal_crossing_evidence(self):
        first=1794700000//300*300
        rows=[dict(start=first+i*300,period=300,open=p,close=p,
                   high=p+1,low=p-1,volume=50,quality="stream_closed",
                   received_at=first+(i+1)*300+2)
              for i,p in enumerate((62600.,62490.))]
        event=evaluate_level_cross(rows,300,first+603)
        msg=format_level_alert(event,market_trend="상위 추세 미확인")
        self.assertIn("62,600.0 → 62,490.0",msg)
        self.assertIn("진입가: 미확정",msg)
        self.assertIn("목표가: 미확정",msg)
        self.assertIn("진입대기",msg)


if __name__=="__main__":
    unittest.main()
