"""Kết nối thiết bị THẬT qua Modbus TCP; không có transport mô phỏng.

PLC cần được lập trình theo hợp đồng thanh ghi trong docs/KET_NOI_THIET_BI.md.
Kết nối chỉ đọc. Cho phép điều khiển là thao tác riêng, không lưu qua restart.
Không có quyền ghi vào hồ sơ/ảnh hoặc thay đổi trạng thái vùng D.
"""
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import socket
import struct
import threading
import os


class EquipmentError(ValueError):
    pass


class EquipmentIOError(EquipmentError):
    """Tách lỗi phản hồi/transport khỏi trường hợp luật điều khiển từ chối lệnh."""
    pass


class ModbusTCP:
    """Một kết nối/thiết bị, chỉ gọi dưới khóa của EquipmentController."""
    def __init__(self, config):
        self.config = config
        self.socket = None
        self.transaction = 0

    def close(self):
        if self.socket:
            self.socket.close()
        self.socket = None

    def _receive(self, length):
        data = bytearray()
        while len(data) < length:
            part = self.socket.recv(length - len(data))
            if not part:
                raise EquipmentIOError('PLC đã đóng kết nối.')
            data.extend(part)
        return bytes(data)

    def exchange(self, function, payload):
        try:
            if self.socket is None:
                self.socket = socket.create_connection((self.config['host'], self.config['port']), timeout=1)
                self.socket.settimeout(1)
            self.transaction = (self.transaction + 1) % 65536
            pdu = bytes([function]) + payload
            header = struct.pack('>HHHB', self.transaction, 0, len(pdu)+1, self.config['unit'])
            self.socket.sendall(header + pdu)
            tx, protocol, length, unit = struct.unpack('>HHHB', self._receive(7))
            if tx != self.transaction or protocol != 0 or unit != self.config['unit'] or not 2 <= length <= 254:
                raise EquipmentIOError('Phản hồi Modbus không khớp yêu cầu.')
            response = self._receive(length-1)
            if response[0] == function | 0x80:
                raise EquipmentIOError(f'PLC trả lỗi Modbus {response[1]}; kiểm tra địa chỉ và quyền thanh ghi.')
            if response[0] != function:
                raise EquipmentIOError('Sai mã chức năng trong phản hồi Modbus.')
            return response[1:]
        except (OSError, EquipmentError):
            self.close()
            raise

    def read_register(self, address):
        reply = self.exchange(3, struct.pack('>HH', address, 1))
        if len(reply) != 3 or reply[0] != 2:
            raise EquipmentIOError('Dữ liệu thanh ghi PLC không hợp lệ.')
        return struct.unpack('>H', reply[1:])[0]

    def read_input(self, address):
        reply = self.exchange(2, struct.pack('>HH', address, 1))
        if len(reply) != 2 or reply[0] != 1:
            raise EquipmentIOError('Dữ liệu cảm biến PLC không hợp lệ.')
        return bool(reply[1] & 1)

    def write_register(self, address, value):
        payload = struct.pack('>HH', address, value)
        if self.exchange(6, payload) != payload:
            raise EquipmentIOError('PLC không xác nhận đúng lệnh đã gửi.')


STATES = {
    'lights': {0:'ALL_RED', 1:'A_GREEN', 2:'B_GREEN'},
    'barrier': {0:'CLOSED', 1:'OPEN', 2:'OPENING', 3:'CLOSING', 4:'STOPPED'},
}


def validate_config(config, device):
    if device not in STATES or not isinstance(config, dict):
        raise EquipmentError('Thiết bị hoặc cấu hình không hợp lệ.')
    try:
        host = str(ipaddress.IPv4Address(config.get('host', '')))
    except ipaddress.AddressValueError:
        raise EquipmentError('Nhập địa chỉ IPv4 của PLC/tủ điều khiển.') from None
    address = ipaddress.ip_address(host)
    if not (address.is_private or address.is_loopback) or address.is_unspecified or address.is_multicast or address.is_link_local:
        raise EquipmentError('Chỉ kết nối địa chỉ PLC trong mạng nội bộ hoặc loopback.')
    result = {'host':host}
    fields = [('port',1,65535),('unit',1,247),('commandRegister',0,65535),
              ('statusRegister',0,65535),('heartbeatRegister',0,65535)]
    if device == 'barrier':
        fields.append(('obstacleInput',0,65535))
    for key, low, high in fields:
        value = config.get(key)
        if type(value) is not int or not low <= value <= high:
            raise EquipmentError(f'{key}: cần số nguyên từ {low} đến {high}.')
        result[key] = value
    if len({result[key] for key in ('commandRegister','statusRegister','heartbeatRegister')}) != 3:
        raise EquipmentError('Thanh ghi lệnh, trạng thái và heartbeat phải khác nhau.')
    if config.get('contractConfirmed') is not True:
        raise EquipmentError('Cần xác nhận PLC đã lập trình đúng bảng lệnh trước khi lưu cấu hình.')
    result['contractConfirmed'] = True
    return result


def now():
    return datetime.now(timezone.utc).isoformat()


class EquipmentController:
    def __init__(self, occupancy, path=None, transport=ModbusTCP, start=True):
        self.occupancy = occupancy  # Chỉ đọc; không có callback ghi DB.
        self.path = Path(path or Path(__file__).resolve().parents[1] / 'Data' / 'equipment-modbus.json')
        self.transport = transport
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.devices = {name:self._empty() for name in STATES}
        if self.path.exists():
            try:
                configs = json.loads(self.path.read_text(encoding='utf-8'))
                for name in STATES:
                    if configs.get(name):
                        self.devices[name]['config'] = validate_config(configs[name], name)
                first,second = (self.devices[name]['config'] for name in ('lights','barrier'))
                if first and second and self._overlaps(first,second):
                    raise EquipmentError('Cấu hình hai bộ trùng vùng thanh ghi.')
            except (ValueError, OSError):
                # Không tự dùng cấu hình lỗi và không tự kết nối/ghi ngõ ra.
                for device in self.devices.values():
                    device['config'] = None
                    device['error'] = 'Không đọc được cấu hình thiết bị. Hãy lưu lại cấu hình.'
        self.thread = None
        if start:
            self.thread = threading.Thread(target=self._run, daemon=True, name='equipment-modbus')
            self.thread.start()

    @staticmethod
    def _empty():
        return {'config':None,'client':None,'connected':False,'enabled':False,'mode':'MANUAL',
                'feedback':None,'obstacle':None,'lastReadAt':None,'lastCommand':None,
                'heartbeat':0,'error':'','autoBlocked':''}

    def snapshot(self):
        with self.lock:
            return {'available':True,'protocol':'modbus-tcp-register-v1',
                    'devices':{name:{key:value for key,value in device.items() if key not in ('client','heartbeat')}
                               for name,device in self.devices.items()}}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({name:device['config'] for name,device in self.devices.items()}, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, self.path)

    @staticmethod
    def _overlaps(first,second):
        keys = ('commandRegister','statusRegister','heartbeatRegister')
        return all(first[key]==second[key] for key in ('host','port','unit')) and bool({first[key] for key in keys} & {second[key] for key in keys})

    def configure(self, name, config):
        with self.lock:
            if name not in self.devices:
                raise EquipmentError('Thiết bị không hợp lệ.')
            if self.devices[name]['connected']:
                raise EquipmentError('Ngắt kết nối thiết bị trước khi sửa cấu hình.')
            config = validate_config(config, name)
            other = self.devices['barrier' if name=='lights' else 'lights']['config']
            if other and self._overlaps(config,other):
                # Không cho lệnh/heartbeat của một thiết bị đè thanh ghi của thiết bị khác.
                raise EquipmentError('Hai thiết bị dùng cùng PLC phải có vùng thanh ghi riêng, không trùng nhau.')
            old = self.devices[name]
            self.devices[name] = {**self._empty(),'config':config}
            try:
                self._save()
            except OSError:
                self.devices[name] = old
                raise EquipmentError('Không lưu được cấu hình thiết bị trên máy.') from None
            return self.snapshot()

    def _read(self, name):
        device = self.devices[name]
        try:
            raw = device['client'].read_register(device['config']['statusRegister'])
            if raw not in STATES[name]:
                raise EquipmentIOError(f'PLC trả trạng thái {raw} ngoài bảng lệnh {name}; chưa được phép điều khiển.')
            obstacle = device['client'].read_input(device['config']['obstacleInput']) if name=='barrier' else None
            device.update(feedback=STATES[name][raw],obstacle=obstacle,lastReadAt=now())
        except (OSError, EquipmentIOError) as error:
            self._fail(name,error)
            raise EquipmentIOError(device['error']) from None

    def _fail(self, name, error):
        device = self.devices[name]
        if device['client']:
            device['client'].close()
        device.update(client=None, connected=False, enabled=False, mode='MANUAL', feedback=None, obstacle=None,
                      error=str(error) if isinstance(error, EquipmentError) else 'Không liên lạc được PLC. Kiểm tra IP, cổng, dây mạng và nguồn.')

    def _write(self, name, value, label):
        device = self.devices[name]
        try:
            device['client'].write_register(device['config']['commandRegister'], value)
        except (OSError, EquipmentIOError) as error:
            self._fail(name,error)
            raise EquipmentIOError(device['error']) from None
        # Echo FC06 chỉ xác nhận nhận lệnh. Không gán feedback bằng lệnh yêu cầu.
        device['lastCommand'] = {'value':value,'label':label,'sentAt':now()}

    def connect(self, name):
        with self.lock:
            device = self.devices[name]
            if not device['config']:
                raise EquipmentError('Lưu đầy đủ cấu hình trước khi kết nối.')
            if device['connected']:
                return self.snapshot()
            device['client'] = self.transport(device['config'])
            try:
                self._read(name)  # Không ghi lệnh/heartbeat trong thao tác Kết nối.
                device.update(connected=True,error='',enabled=False,mode='MANUAL',autoBlocked='')
            except (OSError, EquipmentError) as error:
                self._fail(name,error)
                raise EquipmentError(device['error']) from None
            return self.snapshot()

    def _require(self, name):
        if name not in self.devices or not self.devices[name]['connected']:
            raise EquipmentError('Thiết bị chưa kết nối hoặc đã mất liên lạc.')
        return self.devices[name]

    def _priority(self, gate, occupancy):
        if not occupancy or occupancy.get('issue') or occupancy.get('signalPolicyVersion') != 1:
            raise EquipmentError('Chưa có dữ liệu D hợp lệ; không cấp xanh hoặc mở barrier.')
        priority = occupancy.get('vehicle') if occupancy.get('occupied') else occupancy.get('nextVehicle')
        if priority:
            if priority.get('entry_camera') != gate or occupancy.get('greenGate') != gate:
                raise EquipmentError('Hướng này chưa được ưu tiên theo xe trong D / hàng chờ.')
            other = 'B' if gate=='A' else 'A'
            if occupancy.get('signals') != {gate:'GREEN',other:'RED'}:
                raise EquipmentError('Trạng thái ưu tiên đèn không hợp lệ; không cấp xanh.')
        elif occupancy.get('occupied'):
            raise EquipmentError('D đang có xe nhưng chưa xác định được hướng ưu tiên.')

    def _green_allowed(self, gate):
        self._priority(gate,self.occupancy())
        barrier = self._require('barrier')
        self._read('barrier')
        current = self.devices['lights']['feedback']
        if barrier['feedback'] != 'CLOSED' and current != f'{gate}_GREEN':
            raise EquipmentError('Barrier chưa đóng; không chuyển hướng xanh.')

    def action(self, name, action):
        if name not in self.devices:
            raise EquipmentError('Thiết bị không hợp lệ.')
        with self.lock:
            device = self._require(name)
            try:
                self._read(name)
                if action=='disconnect':
                    if device['enabled']:
                        self._write(name,0 if name=='lights' else 3,'Hai đỏ' if name=='lights' else 'Dừng barrier')
                    device['client'].close()
                    device.update(client=None,connected=False,enabled=False,mode='MANUAL',feedback=None,obstacle=None,error='')
                    return self.snapshot()
                if action=='enable':
                    self._write(name,0 if name=='lights' else 3,'Cho phép điều khiển: hai đỏ' if name=='lights' else 'Cho phép điều khiển: dừng barrier')
                    device.update(enabled=True,mode='MANUAL',error='')
                    return self.snapshot()
                if not device['enabled']:
                    raise EquipmentError('Nhấn Cho phép điều khiển trước khi gửi lệnh.')
                if name=='lights':
                    if action=='auto':
                        device['mode']='AUTO'
                        self._automatic()
                    elif action in ('red','greenA','greenB'):
                        if action!='red':
                            self._green_allowed(action[-1])
                        self._write(name,{'red':0,'greenA':1,'greenB':2}[action],{'red':'Hai hướng đỏ','greenA':'A xanh, B đỏ','greenB':'B xanh, A đỏ'}[action])
                        device['mode']='MANUAL'
                    else:
                        raise EquipmentError('Lệnh đèn không hợp lệ.')
                elif action=='stop':
                    self._write(name,3,'Dừng barrier')
                elif action in ('open','close'):
                    if action=='close' and device['obstacle'] is not False:
                        raise EquipmentError('Cảm biến có vật cản hoặc chưa xác định; không đóng barrier.')
                    if device['feedback'] in ('OPENING','CLOSING'):
                        raise EquipmentError('Barrier đang chuyển động; dừng trước khi đổi lệnh.')
                    if action=='open':
                        lights = self._require('lights')
                        self._read('lights')
                        if not lights['enabled'] or lights['feedback'] not in ('A_GREEN','B_GREEN'):
                            raise EquipmentError('PLC đèn chưa xác nhận một hướng xanh; không mở barrier.')
                        self._priority(lights['feedback'][0],self.occupancy())
                    self._write(name,1 if action=='open' else 2,'Mở barrier' if action=='open' else 'Đóng barrier')
                else:
                    raise EquipmentError('Lệnh barrier không hợp lệ.')
            except OSError as error:
                self._fail(name,error)
                raise EquipmentError(device['error']) from None
            # Lỗi luật nghiệp vụ không đồng nghĩa rớt kết nối; lỗi transport
            # được client đóng socket và phát hiện khi đọc poll tiếp theo.
            return self.snapshot()

    def _automatic(self):
        device = self.devices['lights']
        try:
            occupancy = self.occupancy()
        except Exception:
            occupancy = None
        priority = (occupancy.get('vehicle') if occupancy.get('occupied') else occupancy.get('nextVehicle')) if occupancy else None
        gate = priority.get('entry_camera') if priority and priority.get('entry_camera') in ('A','B') else None
        if not occupancy or occupancy.get('issue') or occupancy.get('signalPolicyVersion')!=1:
            gate = None
        value = {'A':1,'B':2}.get(gate,0)
        device['autoBlocked'] = '' if occupancy else 'Không đọc được dữ liệu D; yêu cầu hai đỏ.'
        if gate:
            try:
                self._green_allowed(gate)
            except Exception:
                value = 0
                device['autoBlocked'] = 'Chờ barrier đóng / dữ liệu ưu tiên hợp lệ trước khi cấp xanh.'
        previous = device['lastCommand']
        if previous is None or previous['value'] != value:
            self._write('lights',value,'Tự động: '+{0:'hai đỏ',1:'A xanh, B đỏ',2:'B xanh, A đỏ'}[value])

    def poll(self):
        with self.lock:
            for name,device in self.devices.items():
                if not device['connected']:
                    continue
                try:
                    self._read(name)
                    if device['enabled']:
                        device['heartbeat'] = (device['heartbeat']+1) % 65536
                        device['client'].write_register(device['config']['heartbeatRegister'],device['heartbeat'])
                        if name=='lights' and device['mode']=='AUTO':
                            self._automatic()
                        elif name=='lights' and device['feedback'] in ('A_GREEN','B_GREEN'):
                            try:
                                self._priority(device['feedback'][0],self.occupancy())
                            except Exception:
                                self._write('lights',0,'Ưu tiên D thay đổi: yêu cầu hai đỏ')
                        elif name=='barrier':
                            if device['feedback']=='CLOSING' and device['obstacle'] is not False:
                                self._write('barrier',3,'Cảm biến có vật cản: yêu cầu dừng')
                            elif device['feedback']=='OPENING':
                                lights = self.devices['lights']
                                try:
                                    if not lights['connected'] or not lights['enabled'] or lights['feedback'] not in ('A_GREEN','B_GREEN'):
                                        raise EquipmentError('Chưa xác nhận đèn xanh.')
                                    self._priority(lights['feedback'][0],self.occupancy())
                                except Exception:
                                    self._write('barrier',3,'Mất quyền ưu tiên: yêu cầu dừng mở')
                except (OSError, EquipmentError) as error:
                    self._fail(name,error)

    def _run(self):
        while not self.closed.wait(1):
            self.poll()

    def close(self):
        self.closed.set()
        if self.thread:
            self.thread.join(timeout=4)
        with self.lock:
            for device in self.devices.values():
                if device['client']:
                    device['client'].close()
