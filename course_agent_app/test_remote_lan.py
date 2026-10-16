"""LAN opt-in keeps listener, peer and browser URL boundaries explicit."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from remote_network import lan_config, allowed_peer
from remote_gateway import Gateway


class RemoteLANTests(unittest.TestCase):
    def test_only_configured_private_subnet_and_loopback_are_allowed(self):
        lan = lan_config({'lan_address': '192.168.0.124', 'lan_subnet': '192.168.0.0/24'})
        for address in ('127.0.0.1', '192.168.0.55'):
            self.assertTrue(allowed_peer(address, lan))
        for address in ('192.168.1.55', '10.11.11.6', '198.51.100.1', '::1', 'bad'):
            self.assertFalse(allowed_peer(address, lan))
        self.assertFalse(allowed_peer('192.168.0.55', None))
        for address, subnet in [('0.0.0.0','0.0.0.0/0'), ('198.51.100.1','198.51.100.0/24'),
                                ('192.168.1.1','192.168.0.0/24'), ('192.168.0.0','192.168.0.0/24'),
                                ('192.168.0.1','192.168.0.1/24')]:
            with self.assertRaises(ValueError):
                lan_config({'lan_address':address,'lan_subnet':subnet})

    def test_dual_listener_exact_binding_and_http_auth_host_guards(self):
        from http.server import ThreadingHTTPServer
        bindings = []
        def server(address, handler):
            bindings.append(address)
            # Avoid relying on a developer machine's actual LAN interface in tests.
            return ThreadingHTTPServer(address if address[0]=='127.0.0.1' else ('127.0.0.1', 0), handler)
        with tempfile.TemporaryDirectory() as directory, patch('remote_gateway.ThreadingHTTPServer', side_effect=server):
            gateway = Gateway(directory, {}, {'port':0,'lan_address':'192.168.0.124','lan_subnet':'192.168.0.0/24'}).start()
            try:
                self.assertEqual(bindings, [('127.0.0.1',0), ('192.168.0.124', gateway.server.server_port)])
                url = f'http://127.0.0.1:{gateway.lan_server.server_port}/v1/workspace'
                for host, status in [(f'192.168.0.124:{gateway.server.server_port}',401), ('evil.test',403)]:
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(Request(url, headers={'Host':host}),timeout=5)
                    self.assertEqual(raised.exception.code,status)
                    raised.exception.close()
            finally:
                gateway.close()

    def test_extension_accepts_private_http_but_rejects_public_http(self):
        node = shutil.which('node')
        self.assertIsNotNone(node)
        script = Path(__file__).resolve().parents[1]/'moodle_remote_extension/background.js'
        code = r'''const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const c={URL,chrome:{sidePanel:{setPanelBehavior:async()=>{}},storage:{local:{setAccessLevel:async()=>{}},session:{setAccessLevel:async()=>{}}},runtime:{onMessage:{addListener(){}}}}};
vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
for(const value of ['http://192.168.0.124:18791','http://10.1.2.3:18791','http://172.16.0.1:18791','http://127.0.0.1:18791','https://agent.example.edu']){c.value=value;assert.equal(vm.runInContext('serverOrigin(value)',c),value);}
for(const value of ['http://198.51.100.1:18791','http://172.32.0.1:18791','http://agent.example.edu','http://192.168.0.124:18791/evil','http://user:pass@192.168.0.124:18791']){c.value=value;assert.throws(()=>vm.runInContext('serverOrigin(value)',c));}'''
        result = subprocess.run([node, '-e', code, str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__ == '__main__':
    unittest.main()
