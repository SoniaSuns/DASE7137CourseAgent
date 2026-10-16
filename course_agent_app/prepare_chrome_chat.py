"""Build a local unpacked extension; its machine-specific token is never committed."""
import json
from pathlib import Path
import shutil
from chrome_chat_bridge import connection

ROOT=Path(__file__).resolve().parents[1]


def prepare():
    report=ROOT/'course_agent_app/reports/chrome_chat'
    output=report/'extension';output.mkdir(parents=True,exist_ok=True)
    source=ROOT/'moodle_chrome_extension'
    for name in ('manifest.json','background.js','panel.html','panel.css','panel.js'):
        shutil.copy2(source/name,output/name)
    (output/'connection.json').write_text(json.dumps(connection(report),indent=2),encoding='utf-8')
    print('Chrome extension folder: '+str(output))
    print('Open chrome://extensions, enable Developer mode, choose Load unpacked and select this folder.')
    return output


if __name__=='__main__':prepare()
