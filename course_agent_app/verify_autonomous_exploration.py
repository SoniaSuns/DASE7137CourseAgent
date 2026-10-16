"""Opt-in real Codex/WASI check using entirely fictional course data."""
import json
from pathlib import Path

from run_shared_chrome_agent import load_config
from chrome_exploration import CodexExplorer


def main():
    _,env=load_config()
    root=Path(__file__).parent/'reports'/'exploration_validation'
    result=CodexExplorer(env,root,300)({
        'run_id':'synthetic-exploration','task':'Check whether each group has one consistent recorded score.',
        'question':'Use Python to read the two synthetic files. Identify group scores and any mismatch. Quote the numeric group means. No Moodle action.',
        'conversation':[],
        'sources':[
            {'id':'file:roster','name':'participants.html','kind':'library',
             'data':b'<table><tr><th>Student</th><th>Group</th></tr><tr><td>Fictional One</td><td>1</td></tr><tr><td>Fictional Two</td><td>1</td></tr><tr><td>Fictional Three</td><td>2</td></tr></table>'},
            {'id':'file:marks','name':'nonstandard-scores.dat','kind':'library',
             'data':b'Student|Score\nFictional One|80\nFictional Two|80\nFictional Three|90\n'}],
        'available_sources':[]})
    events=(Path(result['workspace'])/'events.jsonl').read_text(encoding='utf-8')
    assert 'mcp_tool_call' in events, 'No actual analysis tool execution observed'
    assert 'command_execution' not in events, 'Host shell unexpectedly exposed'
    assert {'file:roster','file:marks'} <= set(result['source_ids'])
    assert '80' in result['findings'] and '90' in result['findings']
    (root/'synthetic-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['findings','source_ids','unresolved','workspace']},ensure_ascii=False))


if __name__ == '__main__':
    main()
