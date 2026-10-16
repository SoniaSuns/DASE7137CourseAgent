"""Opt-in real Codex planning smoke; fictional evidence, no browser or Moodle writes."""
import json
from pathlib import Path

from remote_planner import RemotePlanner
from remote_workspace import validate_plan
from run_shared_chrome_agent import load_config


def main():
    _, env = load_config()
    root = Path(__file__).parent/'reports/remote_workspace_validation'
    root.mkdir(parents=True, exist_ok=True)
    binding = {'origin': 'https://moodle.example.test', 'course_id': '7', 'actor_id': '41', 'tab_id': 3}
    url = binding['origin']+'/course/modedit.php?update=12'
    evidence = [{'url': url, 'title': 'Fictional resource', 'text': 'Name: Old', 'links': [],
                 'form': {'signature': 'fictional-snapshot', 'fields': [
                     {'name': 'name', 'label': 'Name', 'type': 'text', 'value': 'Old', 'disabled': False, 'options': []}]}}]
    context = {'text': 'Rename this fictional inspected resource from Old to New. Make no other changes.',
               'binding': binding, 'evidence': evidence, 'files': [], 'file_evidence': [], 'messages': [], 'analysis': []}
    result = RemotePlanner(env, root/'model')(context)
    assert result['action'] == 'plan', result
    plan = validate_plan({'binding': binding, 'evidence': evidence}, result['operations'])
    assert len(plan) == 1 and plan[0]['changes'][0]['value'] == 'New'
    report = {'passed': True, 'source': 'fictional form only', 'action': result['action'],
              'changes': plan[0]['changes'], 'moodle_writes': 0}
    (root/'model-smoke.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
