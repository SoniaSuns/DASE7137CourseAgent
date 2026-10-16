"""Private stdio analysis tool. Never exposes a host shell or browser."""
import json
from pathlib import Path
import sys
from exploration_python import AnalysisPython


def main():
    worker = AnalysisPython(Path(sys.argv[1]))
    for line in sys.stdin:
        request = json.loads(line)
        if 'id' not in request:
            continue
        method = request.get('method')
        error = None
        if method == 'initialize':
            result = {'protocolVersion':request.get('params',{}).get('protocolVersion','2024-11-05'),
                      'capabilities':{'tools':{}},'serverInfo':{'name':'course-analysis','version':'1'}}
        elif method == 'tools/list':
            result = {'tools':[{'name':'python','description':'Execute Python freely in isolated WASI. First read /work/input.json. Files are in /work; use os.chdir("/work"). Standard library and pure-Python installed packages are available. No host filesystem, network or subprocesses. Print observations/calculations. Write temporary scripts under /work if useful.',
                       'inputSchema':{'type':'object','properties':{'code':{'type':'string'}},'required':['code'],'additionalProperties':False}}]}
        elif method == 'tools/call':
            try:
                if request['params']['name'] != 'python':
                    raise ValueError('Unknown analysis tool.')
                output = worker.run(request['params']['arguments']['code'])
                result = {'content':[{'type':'text','text':json.dumps(output,ensure_ascii=False)}],
                          'isError':output['exit_code'] != 0}
            except Exception as exc:
                result = {'content':[{'type':'text','text':'Analysis execution failed: '+type(exc).__name__}], 'isError':True}
        elif method == 'ping':
            result = {}
        else:
            error = {'code':-32601,'message':'Method not available'}
        response = {'jsonrpc':'2.0','id':request['id']}
        response.update({'error':error} if error else {'result':result})
        print(json.dumps(response,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
