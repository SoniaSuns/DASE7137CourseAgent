"""WASI Python execution: no host sockets, environment, process or ambient files."""
from pathlib import Path
import sys
import tempfile

RUNTIME = Path(__file__).resolve().parents[1]/'.analysis_runtime'


class AnalysisPython:
    def __init__(self, workspace):
        import wasmtime
        self.wasmtime = wasmtime
        self.workspace = Path(workspace).resolve()
        config = wasmtime.Config()
        config.consume_fuel = True
        self.engine = wasmtime.Engine(config)
        modules = list(RUNTIME.rglob('python.wasm'))
        if len(modules) != 1:
            raise RuntimeError('Install the pinned analysis runtime with setup_exploration_runtime.py.')
        self.module = wasmtime.Module.from_file(self.engine, str(modules[0]))
        self.runtime_root = modules[0].parent

    def run(self, code):
        if not isinstance(code, str) or len(code) > 100000:
            raise ValueError('Python analysis code must be a string of at most 100000 characters.')
        wasm = self.wasmtime
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)/'stdout.txt';errors = Path(temp)/'stderr.txt'
            wasi = wasm.WasiConfig()
            wasi.argv = ['python', '-c', code]
            wasi.env = [('PYTHONHOME','/runtime'), ('PYTHONPATH','/packages'), ('PYTHONDONTWRITEBYTECODE','1')]
            wasi.preopen_dir(str(self.runtime_root), '/runtime', fs_mutable=False)
            packages = Path(sys.prefix)/('Lib/site-packages' if sys.platform == 'win32' else f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages')
            wasi.preopen_dir(str(packages), '/packages', fs_mutable=False)
            wasi.preopen_dir(str(self.workspace), '/work')
            captured = {'stdout':bytearray(), 'stderr':bytearray()}
            def collect(key, data):
                captured[key].extend(data[:max(0,64000-len(captured[key]))])
                return len(data)
            wasi.stdout_custom = lambda data: collect('stdout',data)
            wasi.stderr_custom = lambda data: collect('stderr',data)
            store = wasm.Store(self.engine)
            store.set_limits(memory_size=512*1024*1024, instances=4, memories=4)
            store.set_fuel(20_000_000_000)
            store.set_wasi(wasi)
            linker = wasm.Linker(self.engine);linker.define_wasi()
            status = 0
            try:
                instance = linker.instantiate(store, self.module)
                instance.exports(store)['_start'](store)
            except wasm.ExitTrap as exc:
                status = exc.code
            except wasm.Trap as exc:
                status = 1
                collect('stderr', ('WASI execution stopped: '+str(exc)[:2000]).encode())
            finally:
                store.close()
                linker.close()
            return {'exit_code':status, **{key:bytes(value).decode('utf-8',errors='replace') for key,value in captured.items()}}
