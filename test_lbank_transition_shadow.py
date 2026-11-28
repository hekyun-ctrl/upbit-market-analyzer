import unittest
from lbank_transition_shadow import transition_evidence

# Railway NATIVE_CANDLE_CLOSED, 2026-11-26 KST 16:10..17:50.
# Time labels are bar END times; the log was emitted seconds afterward.
ROWS = [(60482.2,60505.8,60388,60388.1),(60388.1,60487.1,60388,60454.6),(60454.6,60527.9,60449,60515.6),(60515.6,60613.2,60500,60557.9),(60557.9,60599,60541.7,60550),(60550,60550,60459.7,60539.3),(60539.3,60632.8,60528.8,60589.7),(60589.7,60644,60578.4,60615.9),(60615.9,60638.1,60572,60583.7),(60583.7,60588.5,60525.3,60556.4),(60556.4,60566.3,60485.6,60497.9),(60497.9,60623,60497.9,60595.9),(60595.9,60696.7,60566.8,60654.3),(60654.3,60666.3,60580,60580),(60580,60639,60541.9,60633.3),(60633.3,60734,60633.2,60650),(60650,60675.2,60621.3,60667.6),(60667.6,60694.5,60581.1,60609.1),(60609.1,60715.3,60609.1,60684.1),(60684.1,60738.3,60668.8,60728.3),(60728.3,61030,60720.2,60999.5)]
BASE = 1795676700

def bars(rows=ROWS):
    return [dict(start=BASE+i*300,quality='stream_closed',**dict(zip(('open','high','low','close'),r))) for i,r in enumerate(rows)]

def replay(rows=ROWS):
    b=bars(rows)
    return [transition_evidence(b[:i+1],b[i]['start']+300) for i in range(len(b))]

class TransitionTests(unittest.TestCase):
    def test_incident_first_break(self):
        r=replay(); first=next(i for i,x in enumerate(r) if x['stage']=='structure_break' and x['direction']==1)
        self.assertEqual(first,3); self.assertEqual(r[first]['close'],60557.9)
    def test_recovery_retest(self):
        r=replay(); self.assertEqual(r[12]['stage'],'structure_break'); self.assertEqual(r[12]['direction'],1)
    def test_symmetric(self):
        m=[(120000-o,120000-l,120000-h,120000-c) for o,h,l,c in ROWS]
        a,b=replay(),replay(m)
        self.assertEqual([(x['stage'],x['direction']) for x in a],[(x['stage'],-x['direction']) for x in b])
    def test_no_future(self):
        b=bars(); self.assertFalse(transition_evidence(b[:4],b[3]['start']+299)['ready'])
    def test_gap(self):
        b=bars()[:4]; b[1]['start']-=300; self.assertFalse(transition_evidence(b,b[-1]['start']+300)['ready'])
    def test_forming(self):
        b=bars()[:4]; b[-1]['quality']='forming'; self.assertFalse(transition_evidence(b,b[-1]['start']+300)['ready'])
    def test_invalid_ohlc(self):
        b=bars()[:4]; b[-1]['high']=1; self.assertFalse(transition_evidence(b,b[-1]['start']+300)['ready'])
    def test_never_approves(self):
        for x in replay(): self.assertFalse(x['approval']); self.assertIsNone(x['entry_prices']); self.assertEqual(x['risk_gate'],'unchanged')
    def test_wick_not_break(self):
        b=bars()[:4]; b[-1]['close']=60520; self.assertNotEqual(transition_evidence(b,b[-1]['start']+300)['stage'],'structure_break')

if __name__=='__main__': unittest.main()
