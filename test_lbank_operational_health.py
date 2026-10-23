"""Storage outages, recovery and status delivery with no network or sleep."""
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lbank_operational_health import (JournalRecovery, OperationalNotices,
                                      StorageReserve, blocked_diagnostic)
from lbank_performance import PerformanceBook


class OperationalHealthTests(unittest.TestCase):
    def test_reserve_uses_available_bytes_and_rechecks_for_recovery(self):
        with tempfile.TemporaryDirectory() as path, patch('os.statvfs') as stat:
            stat.return_value=SimpleNamespace(f_bavail=4,f_frsize=1024)
            guard=StorageReserve(path,reserve_bytes=8192)
            self.assertFalse(guard.check(0)['research_writable'])
            stat.return_value=SimpleNamespace(f_bavail=10,f_frsize=1024)
            self.assertFalse(guard.check(59)['research_writable'])
            self.assertTrue(guard.check(60)['research_writable'])
            self.assertEqual(stat.call_count,2)

    def test_unreadable_storage_only_disables_optional_research(self):
        with patch('os.statvfs',side_effect=OSError('unavailable')):
            status=StorageReserve('/tmp').check(0)
        self.assertEqual(status['status'],'unknown')
        self.assertFalse(status['research_writable'])
        self.assertIsNone(blocked_diagnostic(1,True,True,{}))

    def test_journal_retries_are_bounded_and_keep_same_healthy_connection(self):
        book=object();factory=Mock(side_effect=[OSError('full'),book])
        recovery=JournalRecovery(factory)
        self.assertIsNone(recovery.get(0))
        self.assertIsNone(recovery.get(59))
        self.assertIs(recovery.get(60),book)
        self.assertIs(recovery.get(1000),book)
        self.assertEqual(factory.call_count,2)

    def test_failed_database_initialization_closes_connection(self):
        db=Mock();db.execute.side_effect=sqlite3.OperationalError('disk full')
        with patch('lbank_performance.sqlite3.connect',return_value=db):
            with self.assertRaises(sqlite3.OperationalError):PerformanceBook(':memory:',1000)
        db.close.assert_called_once()

    def test_journal_and_native_outage_are_not_strategy_rejections(self):
        for journal,native,reason in [(False,False,'entry_journal_unavailable'),
                                     (True,False,'native_data_unavailable')]:
            result=blocked_diagnostic(123,journal,native,{'reason':'stale'})
            self.assertEqual(result['reason'],reason)
            self.assertEqual(result['at'],123)
            self.assertFalse(result['strategy_evaluated'])

    def test_healthy_startup_and_short_gap_do_not_create_notices(self):
        sender=Mock(return_value=True);notices=OperationalNotices(sender)
        for at,issue in [(0,None),(1,'native'),(119,'native'),(120,None),(300,None)]:
            notices.observe(at,issue)
        sender.assert_not_called()

    def test_persistent_outage_and_stable_recovery_send_once_each(self):
        sender=Mock(return_value=True);notices=OperationalNotices(sender)
        for at,issue in [(0,'journal'),(120,'journal'),(180,'journal'),
                         (300,None),(419,None),(420,None),(900,None)]:
            notices.observe(at,issue)
        self.assertEqual(sender.call_count,2)
        self.assertIn('보류',sender.call_args_list[0].args[0])
        self.assertIn('해소',sender.call_args_list[1].args[0])

    def test_unacknowledged_notice_is_retried_without_false_recovery(self):
        sender=Mock(side_effect=[False,True]);notices=OperationalNotices(sender)
        for at in (0,120,180,419,420,900):notices.observe(at,'native')
        self.assertEqual(sender.call_count,2)
        self.assertEqual(notices.announced,'native')

    def test_delivery_exception_does_not_interrupt_monitor(self):
        notices=OperationalNotices(Mock(side_effect=OSError('network')))
        notices.observe(0,'native');notices.observe(120,'native')
        self.assertIsNone(notices.announced)

    def test_low_storage_notice_does_not_claim_entries_are_disabled(self):
        sender=Mock(return_value=True);notices=OperationalNotices(sender)
        notices.observe(0,'storage_low');notices.observe(120,'storage_low')
        self.assertIn('연구 기록을 일시 중단',sender.call_args.args[0])
        self.assertNotIn('신규 진입 판단을 보류',sender.call_args.args[0])


if __name__=='__main__':unittest.main()
