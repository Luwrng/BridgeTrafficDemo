"""PLC socket fixture chỉ nghe loopback/cổng ngẫu nhiên, không dùng thiết bị thật.
Không tạo lượt xe hoặc truy cập SQL Server của ứng dụng.
"""
import json
from pathlib import Path
import socket
import socketserver
import struct
import sys
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'tools'))
from modbus_equipment import EquipmentController, EquipmentError, EquipmentIOError, ModbusTCP, validate_config
from vehicle_record_server import RecordHandler, RecordHTTPServer


def occupancy(gate=None):
    return {'occupied':bool(gate),'vehicle':{'entry_camera':gate} if gate else None,'nextVehicle':None,
            'greenGate':gate,'signalPolicyVersion':1,'signals':{name:'GREEN' if name==gate else 'RED' for name in ('A','B')}}


class PLCServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class PLCHandler(socketserver.BaseRequestHandler):
    def read_exact(self,length):
        data=b''
        while len(data)<length:
            try:part=self.request.recv(length-len(data))
            except ConnectionError:return None
            if not part:return None
            data+=part
        return data

    def handle(self):
        while True:
            header=self.read_exact(7)
            if not header:return
            tx,protocol,length,unit=struct.unpack('>HHHB',header)
            body=self.read_exact(length-1)
            if not body:return
            function=body[0];address,value=struct.unpack('>HH',body[1:])
            self.server.calls.append((function,address,value))
            if self.server.bad_transaction:
                tx=(tx+1)%65536
            if self.server.exception:
                reply=bytes([function|128,2])
            elif function==3:
                reply=bytes([3,2])+struct.pack('>H',self.server.registers.get(address,0))
            elif function==2:
                reply=bytes([2,1,int(self.server.obstacle)])
            elif function==6:
                self.server.registers[address]=value
                reply=bytes([6])+struct.pack('>HH',address,value+1) if self.server.bad_write_echo else body
            else:
                reply=bytes([function|128,1])
            packet=struct.pack('>HHHB',tx,protocol,len(reply)+1,unit)+reply
            # MBAP và PDU cố ý tách gói: recv không được giả định nhận đủ một lần.
            self.request.sendall(packet[:5]);self.request.sendall(packet[5:])


class EquipmentTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.server=PLCServer(('127.0.0.1',0),PLCHandler)
        self.server.calls=[];self.server.registers={101:0,201:0}
        self.server.obstacle=False;self.server.bad_transaction=False;self.server.exception=False
        self.server.bad_write_echo=False
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.state=occupancy()
        self.controller=EquipmentController(lambda:self.state,Path(self.temp.name)/'devices.json',start=False)
        for name in ('lights','barrier'):
            self.controller.configure(name,self.config(name))

    def tearDown(self):
        self.controller.close();self.server.shutdown();self.server.server_close();self.thread.join(timeout=2);self.temp.cleanup()

    def config(self,name):
        base=100 if name=='lights' else 200
        config={'host':'127.0.0.1','port':self.server.server_address[1],'unit':1,
                'commandRegister':base,'statusRegister':base+1,'heartbeatRegister':base+2,'contractConfirmed':True}
        if name=='barrier':config['obstacleInput']=0
        return config

    def ready(self):
        for name in ('lights','barrier'):
            self.controller.connect(name);self.controller.action(name,'enable')

    def writes(self):
        return [(address,value)for function,address,value in self.server.calls if function==6]

    def test_configuration_and_connect_are_read_only_and_restart_never_arms(self):
        for name in ('lights','barrier'):self.controller.connect(name)
        self.controller.poll()
        self.assertEqual(self.writes(),[])
        restarted=EquipmentController(lambda:self.state,self.controller.path,start=False)
        try:
            for device in restarted.snapshot()['devices'].values():
                self.assertFalse(device['connected']);self.assertFalse(device['enabled']);self.assertIsNotNone(device['config'])
        finally:restarted.close()

    def test_requires_explicit_enable_and_register_echo_does_not_fake_feedback(self):
        self.controller.connect('lights')
        with self.assertRaises(EquipmentError):self.controller.action('lights','greenA')
        self.ready();self.controller.action('lights','greenA')
        device=self.controller.snapshot()['devices']['lights']
        self.assertEqual(device['feedback'],'ALL_RED')
        self.assertEqual(device['lastCommand']['value'],1)
        self.server.registers[101]=1;self.controller.poll()
        self.assertEqual(self.controller.snapshot()['devices']['lights']['feedback'],'A_GREEN')

    def test_priority_and_closed_barrier_are_checked_on_server(self):
        self.ready();self.state=occupancy('A')
        with self.assertRaises(EquipmentError):self.controller.action('lights','greenB')
        self.server.registers[201]=1
        with self.assertRaises(EquipmentError):self.controller.action('lights','greenA')
        self.server.registers[201]=0;self.controller.action('lights','greenA')
        self.assertEqual(self.server.registers[100],1)
        self.server.registers[101]=1;self.state=occupancy('B');self.controller.poll()
        self.assertEqual(self.server.registers[100],0)

    def test_barrier_open_requires_real_green_feedback_and_close_respects_sensor(self):
        self.ready()
        with self.assertRaises(EquipmentError):self.controller.action('barrier','open')
        self.controller.action('lights','greenA')
        with self.assertRaises(EquipmentError):self.controller.action('barrier','open')
        self.server.registers[101]=1
        self.controller.action('barrier','open')
        self.assertEqual(self.server.registers[200],1)
        self.assertEqual(self.controller.snapshot()['devices']['barrier']['feedback'],'CLOSED')
        self.server.registers[201]=1;self.server.obstacle=True
        with self.assertRaises(EquipmentError):self.controller.action('barrier','close')
        self.server.obstacle=False;self.controller.action('barrier','close')
        self.assertEqual(self.server.registers[200],2)
        self.server.registers[201]=3;self.server.obstacle=True;self.controller.poll()
        self.assertEqual(self.server.registers[200],3)

    def test_auto_runs_from_server_without_a_browser_and_sends_red_on_database_failure(self):
        self.ready();self.state=occupancy('B')
        self.controller.action('lights','auto');self.assertEqual(self.server.registers[100],2)
        self.server.registers[101]=2;self.controller.poll()
        self.assertIn((102,1),self.writes());self.assertIn((202,1),self.writes())
        self.state=occupancy('A');self.server.registers[201]=1;self.controller.poll()
        self.assertEqual(self.server.registers[100],0)
        self.controller.occupancy=lambda:(_ for _ in ()).throw(RuntimeError('db unavailable'))
        self.controller.poll();self.assertEqual(self.server.registers[100],0)

    def test_stop_is_sent_if_lights_are_lost_while_barrier_is_opening(self):
        self.ready();self.controller.action('lights','greenA');self.server.registers[101]=1
        self.server.registers[201]=2
        self.server.registers[101]=99  # Không coi phản hồi sai là trạng thái đèn hợp lệ.
        self.controller.poll()
        self.assertFalse(self.controller.snapshot()['devices']['lights']['connected'])
        self.assertEqual(self.server.registers[200],3)

    def test_bad_modbus_frame_is_not_reported_as_connected(self):
        self.server.bad_transaction=True
        with self.assertRaises(EquipmentError):self.controller.connect('lights')
        self.assertFalse(self.controller.snapshot()['devices']['lights']['connected'])
        self.assertEqual(self.writes(),[])

    def test_modbus_exception_and_out_of_contract_feedback_reject_connection(self):
        self.server.exception=True
        with self.assertRaises(EquipmentError):self.controller.connect('lights')
        self.server.exception=False;self.server.registers[101]=17
        with self.assertRaises(EquipmentError):self.controller.connect('lights')
        self.assertFalse(self.controller.snapshot()['devices']['lights']['connected'])

    def test_overlap_and_missing_mapping_are_rejected_without_network_writes(self):
        config=self.config('barrier');config['commandRegister']=101
        with self.assertRaises(EquipmentError):self.controller.configure('barrier',config)
        for value in [None,'',False,-1,65536]:
            config=self.config('lights');config['commandRegister']=value
            with self.assertRaises(EquipmentError):validate_config(config,'lights')
        config=self.config('lights');config['contractConfirmed']=False
        with self.assertRaises(EquipmentError):validate_config(config,'lights')
        self.assertEqual(self.writes(),[])

    def test_disconnect_sends_safe_request_and_does_not_claim_feedback_after_disconnect(self):
        self.ready();self.controller.action('lights','disconnect');self.controller.action('barrier','disconnect')
        self.assertEqual(self.writes()[-2:],[(100,0),(200,3)])
        for device in self.controller.snapshot()['devices'].values():
            self.assertFalse(device['connected']);self.assertFalse(device['enabled']);self.assertIsNone(device['feedback'])

    def test_write_ack_error_disables_device_and_poll_keeps_processing_other_controller(self):
        self.ready();self.state=occupancy('A');self.server.bad_write_echo=True
        with self.assertRaises(EquipmentIOError):self.controller.action('lights','auto')
        self.assertFalse(self.controller.snapshot()['devices']['lights']['connected'])
        self.server.bad_write_echo=False;self.controller.poll()
        self.assertTrue(self.controller.snapshot()['devices']['barrier']['connected'])

    def test_http_routes_validate_origin_confirmation_and_read_only_connection(self):
        class Handler(RecordHandler):pass
        Handler.equipment=self.controller
        http=RecordHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=http.serve_forever,daemon=True);worker.start()
        root=f'http://127.0.0.1:{http.server_address[1]}'
        def request(path,payload=None,origin='http://127.0.0.1:5080'):
            body=json.dumps(payload).encode() if payload is not None else None
            req=Request(root+path,data=body,headers={'Content-Type':'application/json','Origin':origin})
            with urlopen(req,timeout=3) as response:return json.load(response)
        try:
            self.assertTrue(request('/api/equipment')['available'])
            with self.assertRaises(HTTPError) as denied:
                request('/api/equipment/connect',{'device':'lights','confirm':True},'https://outside.invalid')
            self.assertEqual(denied.exception.code,403)
            with self.assertRaises(HTTPError) as unconfirmed:
                request('/api/equipment/connect',{'device':'lights'})
            self.assertEqual(unconfirmed.exception.code,400)
            result=request('/api/equipment/connect',{'device':'lights','confirm':True})
            self.assertTrue(result['devices']['lights']['connected'])
            self.assertFalse(result['devices']['lights']['enabled']);self.assertEqual(self.writes(),[])
            with self.assertRaises(HTTPError) as unknown:
                request('/api/equipment/action',{'device':'other','action':'open','confirm':True})
            self.assertEqual(unknown.exception.code,400)
        finally:http.shutdown();http.server_close();worker.join(timeout=2)


if __name__=='__main__':unittest.main()
