"""FIFO và interlock A/B trên SQLite/SQL Server UUID, không ghi DB vận hành."""
from concurrent.futures import ThreadPoolExecutor
import os
import unittest

import test_record_management as management
from test_vehicle_records import RecordFixture
from vehicle_records import VisitStore
from sqlserver_store import SqlServerVisitStore


class GatePriorityCases:
    event=RecordFixture.event

    def check_signals(self, state, gate):
        self.assertEqual(state['greenGate'],gate)
        self.assertEqual(state['signals'],{g:'GREEN' if g==gate else 'RED' for g in ('A','B')})
        self.assertLessEqual(sum(v=='GREEN' for v in state['signals'].values()),1)

    def test_no_vehicle_means_both_red_not_both_green(self):
        self.check_signals(self.store.occupancy(),None)

    def test_a_arrives_first_then_waiting_b_gets_green_after_a_exits(self):
        first=self.store.record(self.event('a-first','A'))
        self.check_signals(first['occupancy'],'A')
        waiting=self.store.record(self.event('b-waits','B','51F00651',1))
        self.check_signals(waiting['occupancy'],'A')
        out=self.store.record(self.event('a-out','C',seconds=2))
        self.assertFalse(out['occupancy']['occupied'])
        self.check_signals(out['occupancy'],'B')
        self.assertEqual(out['occupancy']['nextVehicle']['id'],waiting['visitIds'][0])
        admitted=self.store.record(self.event('b-in','B','51F00651',3))
        self.check_signals(admitted['occupancy'],'B')
        self.assertEqual(admitted['state'],'entry')
        closed=self.store.record(self.event('b-out','E','51F00651',4))
        self.check_signals(closed['occupancy'],None)
        visit=self.store.detail(waiting['visitIds'][0])
        self.assertEqual((visit['entry_camera'],visit['exit_camera']),('B','E'))
        self.assertEqual(visit['entered_at'],'2026-10-14T03:00:01.000+00:00')

    def test_b_arrives_first_and_a_cannot_take_green_until_b_exits(self):
        self.store.record(self.event('b-first','B'))
        self.store.record(self.event('a-waits','A','51F00651',1))
        self.check_signals(self.store.occupancy(),'B')
        self.check_signals(self.store.record(self.event('b-out','C',seconds=2))['occupancy'],'A')

    def test_fifo_prevents_later_vehicle_or_new_camera_from_jumping_queue(self):
        self.store.record(self.event('owner','A'))
        older=self.store.record(self.event('older-b','B','51F00651',1))
        later=self.store.record(self.event('later-a','A','51H12345',2))
        self.store.record(self.event('owner-out','C',seconds=3))
        blocked=self.store.record(self.event('a-tries','A','51H12345',4))
        self.assertEqual(blocked['state'],'waiting'); self.assertFalse(blocked['occupancy']['occupied'])
        self.check_signals(blocked['occupancy'],'B')
        newer=self.store.record(self.event('new-a','A','51G54321',5))
        self.assertEqual(newer['state'],'waiting')
        self.assertEqual(newer['occupancy']['nextVehicle']['id'],older['visitIds'][0])
        self.store.record(self.event('b-in','B','51F00651',6))
        next_state=self.store.record(self.event('b-out','E','51F00651',7))['occupancy']
        self.check_signals(next_state,'A')
        self.assertEqual(next_state['nextVehicle']['id'],later['visitIds'][0])
        self.assertEqual(self.store.record(self.event('later-in','A','51H12345',8))['state'],'entry')

    def test_exit_of_waiting_vehicle_does_not_switch_the_owner_green(self):
        owner=self.store.record(self.event('owner','A'))
        self.store.record(self.event('wait','B','51F00651',1))
        state=self.store.record(self.event('wait-out','C','51F00651',2))['occupancy']
        self.check_signals(state,'A')
        self.assertEqual(state['vehicle']['id'],owner['visitIds'][0])
        self.assertTrue(state['occupied'])

    def test_c_and_e_have_no_invented_mapping_to_a_or_b(self):
        self.store.record(self.event('c-in','C'))
        self.check_signals(self.store.occupancy(),None)
        self.store.record(self.event('e-wait','E','51F00651',1))
        state=self.store.record(self.event('c-out','B',seconds=2))['occupancy']
        self.check_signals(state,None)
        self.assertEqual(state['nextVehicle']['entry_camera'],'E')
        self.assertEqual(self.store.record(self.event('e-in','E','51F00651',3))['state'],'entry')
        self.check_signals(self.store.occupancy(),None)

    def test_restart_and_archiving_do_not_change_signal_ownership_or_fifo(self):
        first=self.store.record(self.event('owner','A'))
        waiting=self.store.record(self.event('wait','B','51F00651',1))
        self.store.set_deleted(first['visitIds'][0],True)
        self.store.set_deleted(waiting['visitIds'][0],True)
        self.check_signals(self.store.occupancy(),'A')
        self.store.record(self.event('owner-out','C',seconds=2))
        restarted=SqlServerVisitStore(self.settings,self.temp.name) if hasattr(self,'settings') else VisitStore(self.temp.name)
        self.check_signals(restarted.occupancy(),'B')
        self.assertEqual(restarted.occupancy()['nextVehicle']['id'],waiting['visitIds'][0])

    def test_simultaneous_a_b_confirmations_grant_only_one_green(self):
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(self.store.record,[self.event('one','A'),self.event('two','B','51F00651')]))
        self.assertEqual({r['state'] for r in results},{'entry','waiting'})
        state=self.store.occupancy()
        self.check_signals(state,state['vehicle']['entry_camera'])
        other='B' if state['greenGate']=='A' else 'A'
        self.check_signals(self.store.record(self.event('out','C',state['vehicle']['plate'],2))['occupancy'],other)

    def test_ambiguous_multiple_inside_visits_force_both_red(self):
        self.store.record(self.event('one','A'))
        with self.store.connection() as conn:
            conn.execute("""INSERT INTO visits(id,realm,plate,entered_at,entry_camera,status,created_at)
                VALUES ('legacy','real','51F00651','2026-10-14T03:00:00.000+00:00','B','INSIDE','2026-10-14T03:00:00.000+00:00')""")
        state=self.store.occupancy()
        self.assertIsNotNone(state['issue'])
        self.check_signals(state,None)


class SQLiteGatePriorityTests(GatePriorityCases,RecordFixture):
    pass


@unittest.skipUnless(os.environ.get('CAMERA_SQLSERVER_TESTS')=='1','SQL Server integration opt-in')
class SqlGatePriorityTests(GatePriorityCases,unittest.TestCase):
    setUpClass=classmethod(management.SqlManagementTests.setUpClass.__func__)
    drop_database=classmethod(management.SqlManagementTests.drop_database.__func__)
    setUp=management.SqlManagementTests.setUp


if __name__=='__main__': unittest.main()
