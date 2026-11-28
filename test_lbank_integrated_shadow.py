import unittest
from lbank_integrated_shadow import IntegratedShadow, net, valid, MIN_RR
from lbank_native_entry import SOURCE


def bar(s,o,h,l,c):
    return dict(start=s,open=o,high=h,low=l,close=c,quality="stream_closed")


class IntegratedShadowTests(unittest.TestCase):
    def test_trend_persists_through_pause_and_invalidates_on_close(self):
        engine=IntegratedShadow()
        rows=[bar(300,60000,60020,59980,60000),bar(600,60000,60030,59990,60010),
              bar(900,60010,60090,60000,60080)]
        engine._advance(rows)
        self.assertEqual((engine.direction,engine.phase,engine.anchor),(1,"transition",59980))
        rows.append(bar(1200,60080,60100,60045,60060))
        engine._advance(rows)
        self.assertEqual((engine.direction,engine.phase),(1,"pullback"))
        rows.append(bar(1500,60060,60065,59930,59940))
        engine._advance(rows)
        self.assertEqual((engine.direction,engine.phase),(-1,"transition"))

    def test_nov28_completed_5m_incident_retains_uptrend_after_pause(self):
        # Actual 10:00--10:40 KST completed five-minute bars from Railway.
        # Only direction-state reconstruction, not a fill or trade backtest.
        raw=[(59912.9,59917.6,59870,59870.1),
             (59870.1,60010,59808,59972.1),
             (59972.1,60044.3,59940,60039.9),
             (60039.9,60117.3,60024,60041.4),
             (60041.4,60184.8,60041.3,60167.6),
             (60167.6,60167.7,60100,60110.8),
             (60110.8,60121.3,60079.3,60090.5),
             (60090.5,60201.8,60090.4,60195.5),
             (60195.5,60195.5,60158.2,60174)]
        bars=[bar(1795827300+i*300,*r) for i,r in enumerate(raw)]
        engine=IntegratedShadow()
        states=[]
        for i in range(3,len(bars)+1):
            engine._trend(bars[:i])
            states.append((engine.direction,engine.phase))
        self.assertEqual(states,[ (1,"transition"), (1,"continuation"),
            (1,"acceleration"),(1,"pullback"),(1,"pullback"),
            (1,"acceleration"),(1,"pullback") ])

    def test_structure_first_and_cost_threshold(self):
        engine=IntegratedShadow()
        one=[bar(60*i,60000,60005,59995,60000) for i in range(1,5)]
        one.append(bar(300,60000,60030,60000,60025))
        five=[bar(300*i,60000,60400,59900,60000) for i in range(1,7)]
        p=engine._plan("C",1,60025,one,five,10,30)
        self.assertEqual(p["reason"],"shadow_qualified")
        self.assertAlmostEqual(p["target"],60400-60025*.00015)
        self.assertGreaterEqual(p["net_rr"],MIN_RR)
        self.assertFalse(p["approval"])
        five[-1]["high"]=60100
        p=engine._plan("C",1,60025,one,five,10,30)
        self.assertEqual(p["reason"],"insufficient_market_room")
        self.assertFalse(p["eligible"])
        mirror=lambda rows:[dict(b,open=120000-b["open"],close=120000-b["close"],
                    high=120000-b["low"],low=120000-b["high"]) for b in rows]
        p=engine._plan("B",-1,59975,mirror(one),mirror(five),10,30)
        self.assertEqual(p["reason"],"insufficient_market_room")
        self.assertFalse(p["approval"])

    def test_unknown_target_never_invented(self):
        one=[bar(60*i,60000,60005,59995,60000) for i in range(1,5)]
        one.append(bar(300,60000,60030,60000,60025))
        five=[bar(300*i,60000,60010,59990,60000) for i in range(1,7)]
        p=IntegratedShadow()._plan("C",1,60025,one,five,10,30)
        self.assertEqual(p["reason"],"target_room_unknown")
        self.assertIsNone(p["target"])
        self.assertFalse(p["approval"])

    def test_no_forming_gap_or_wrong_source(self):
        one=[bar(60,60000,60005,59995,60000),bar(120,60000,60005,59995,60000)]
        self.assertTrue(valid(one,60,180,2))
        self.assertFalse(valid(one,60,179,2))
        self.assertFalse(valid(one,60,240,2))
        self.assertFalse(IntegratedShadow().step({"source":"spot"})[0]["ready"])
        self.assertFalse(IntegratedShadow().step(None)[0]["approval"])

    def test_forward_ambiguous_and_adverse_stop_gap(self):
        engine=IntegratedShadow()
        event=dict(direction=1,entry=60000,stop=59900,target=60400,next=120,end=240)
        engine.pending["x"]=event.copy()
        outcomes=engine._forward([bar(60,60000,60500,59800,60000),
                                  bar(120,60000,60420,59890,60000)],180)
        self.assertEqual(outcomes[0]["outcome"],"ambiguous_both")
        self.assertIsNone(outcomes[0]["net_r"])
        engine.pending["x"]=event.copy()
        outcomes=engine._forward([bar(120,59880,59910,59850,59890)],180)
        self.assertEqual(outcomes[0]["outcome"],"stop")
        self.assertLess(outcomes[0]["net_r"],-1)
        self.assertFalse(outcomes[0]["execution_verified"])

    def test_native_step_is_read_only_and_idempotent(self):
        at=21600
        one=[bar(s,60000,60020,59980,60000) for s in range(at-65*60,at,60)]
        five=[bar(s,60000,60040,59960,60000) for s in range(at-20*300,at,300)]
        state=dict(source=SOURCE,observed_at=at,quote=60000,minute_bars=one,five_bars=five)
        engine=IntegratedShadow()
        result,outcomes=engine.step(state)
        self.assertTrue(result["ready"])
        self.assertFalse(result["approval"])
        self.assertEqual(len(result["plans"]),3)
        self.assertEqual(outcomes,[])
        self.assertEqual(engine.step(state)[0],result)


if __name__=="__main__":
    unittest.main()
