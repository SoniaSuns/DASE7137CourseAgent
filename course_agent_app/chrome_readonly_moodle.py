"""Authenticated evidence broker: observed read URLs, no clicks or generated JS.

Uses GET without following redirects. HTML is parsed without running its scripts.
Only existing course/module identities and links found on their pages can enter
the catalog. No tokens, raw hidden controls or browser credentials reach Codex.
"""
import json
import re
from urllib.parse import parse_qs, urljoin, urlparse, unquote, urlencode, urlunparse

from lxml import html
from playwright.sync_api import Error as BrowserTransportError
from chrome_course_scope import course, MoodleLoginRequired
from chrome_file_library import sha

MAX_BYTES = 32 * 1024 * 1024
MODULE_TYPES = {'resource', 'folder', 'page', 'book', 'assign', 'url', 'quiz', 'choice'}


class ReadOnlyMoodle:
    def __init__(self, browser, course_ids):
        self.browser = browser
        self.course_ids = set(map(str, course_ids))
        self.links = {}
        self.receipts = {}

    def initial(self, key):
        target = self.browser.targets.get(key)
        if not target or target['course_id'] not in self.course_ids:
            raise ValueError('Exploration target is not in the current course inventory.')
        selected = course(target['course_id'])
        if target['kind'] == 'module' and target.get('module_type') in MODULE_TYPES:
            path = '/mod/' + target['module_type'] + '/view.php'
            query = {'id': target['id']}
            if target['module_type'] == 'resource':
                query['forceview'] = '1'
        elif target['kind'] == 'participants':
            path, query = '/user/index.php', {'id': selected.id, 'perpage': '200', 'page': '0'}
        elif target['kind'] == 'gradebook':
            path, query = '/grade/report/grader/index.php', {'id': selected.id, 'perpage': '100', 'page': '0'}
        else:
            raise ValueError('This target has no read-only exploration page; use its existing inspect tool.')
        return {'id': key, 'course_id': selected.id, 'name': target['title'],
                'url': selected.url_for(path, query), 'kind': 'moodle_page'}

    def allowed(self, url, cid, contexts=()):
        selected = course(cid)
        try:
            selected.check_origin(url)
        except ValueError:
            return False
        parsed = urlparse(url)
        path = unquote(parsed.path)
        if path != parsed.path or '\\' in path or any(x in path.split('/') for x in ('.', '..')):
            # Encoded filenames are fine for downloads, but encoded endpoint or
            # traversal components are not. Decode and validate that lane below.
            if not parsed.path.startswith('/pluginfile.php/'):
                return False
        query = parse_qs(parsed.query, keep_blank_values=True)
        if any(len(v) != 1 for v in query.values()):
            return False
        if path.startswith('/pluginfile.php/'):
            parts = path.split('/')
            return (len(parts) >= 7 and parts[2] in contexts and
                    parts[3] in {'mod_resource', 'mod_folder', 'mod_page', 'mod_book'} and
                    parts[4] == 'content' and not any(x in parts for x in ('.', '..')) and
                    '\\' not in path and '\x00' not in path and
                    set(query) <= {'forcedownload'} and
                    query.get('forcedownload', ['1'])[0] in {'0', '1'})
        if path == '/user/index.php':
            return (set(query) <= {'id', 'page', 'perpage', 'tifirst', 'tilast'} and
                    query.get('id') == [cid] and query.get('tifirst', ['']) == [''] and
                    query.get('tilast', ['']) == [''] and self.paging(query))
        if path == '/grade/report/grader/index.php':
            return (set(query) <= {'id', 'page', 'perpage'} and query.get('id') == [cid] and self.paging(query))
        match = re.fullmatch(r'/mod/([a-z]+)/view.php', path)
        if match and match[1] in MODULE_TYPES and set(query) <= {'id', 'forceview'}:
            mid = query.get('id', [''])[0]
            target = self.browser.targets.get(f'{cid}:module:{mid}', {})
            return (target.get('module_type') == match[1] and target.get('course_id') == cid and
                    query.get('forceview', ['1'])[0] == '1')
        return False

    @staticmethod
    def paging(query):
        return all(re.fullmatch(r'\d{1,4}', query.get(k, [default])[0]) and
                   int(query.get(k, [default])[0]) <= limit
                   for k, default, limit in [('page', '0', 999), ('perpage', '100', 200)])

    def catalog(self):
        rows = []
        for key in self.browser.targets:
            try:
                item = self.initial(key)
                rows.append({'id': key, 'name': item['name'], 'course_id': item['course_id']})
            except ValueError:
                pass
        rows.extend({k: item[k] for k in ('id', 'name', 'course_id')} for item in self.links.values())
        return rows

    def fetch(self, item):
        course(item['course_id']).check_origin(item['url'])
        try:
            response = self.browser.page.context.request.get(item['url'], max_redirects=0, timeout=45000)
        except BrowserTransportError:
            # Playwright transport errors include request headers and cookies.
            raise RuntimeError('Moodle exploration transport failed; check connection and session before retrying.') from None
        try:
            if response.status in {301, 302, 303, 307, 308}:
                raise MoodleLoginRequired('Moodle exploration received a redirect; inspect login or the source link. No redirect was followed.')
            if response.status != 200:
                raise ValueError('Moodle exploration could not read the selected source (HTTP ' + str(response.status) + ').')
            if int(response.headers.get('content-length', '0')) > MAX_BYTES:
                raise ValueError('Moodle exploration source exceeds the download limit.')
            try:
                data = response.body()
            except BrowserTransportError:
                raise RuntimeError('Moodle exploration response could not be read; no evidence was accepted.') from None
            if len(data) > MAX_BYTES:
                raise ValueError('Moodle exploration source exceeds the download limit.')
            return data
        finally:
            response.dispose()

    def page_data(self, item, data):
        try:
            document = html.fromstring(data, parser=html.HTMLParser(no_network=True))
        except (ValueError, TypeError):
            raise ValueError('Moodle exploration did not receive an HTML page.') from None
        bodies = document.xpath('//body')
        identities = set(re.findall(r'(?:^|\s)course-(\d+)(?=\s|$)', bodies[0].get('class', '') if bodies else ''))
        if identities != {item['course_id']}:
            raise ValueError('Exploration page does not identify the exact selected course.')
        if document.xpath('//form[@id="login"]'):
            raise MoodleLoginRequired('Moodle requires login before exploration.')
        contexts = set(re.findall(r'(?:^|\s)context-(\d+)(?=\s|$)', bodies[0].get('class', '')))
        core = document.xpath('//*[@id="region-main"]') or bodies
        root = core[0]
        # Moodle renders relative last-access ages ("58 secs", then "1 min").
        # They are not stable roster evidence. Identify the exact column by its
        # observed sort key, never by position or by matching arbitrary numbers.
        if urlparse(item['url']).path == '/user/index.php':
            for table in root.xpath('.//table[@id="participants"]'):
                for header in table.xpath('.//th[@scope="col"]'):
                    if not any(parse_qs(urlparse(a.get('href', '')).query).get('tsort') == ['lastaccess']
                               for a in header.xpath('.//a[@href]')):
                        continue
                    columns = set(re.findall(r'(?:^|\s)(c\d+)(?=\s|$)', header.get('class', '')))
                    for cell in table.xpath('.//td'):
                        if columns.intersection(cell.get('class', '').split()):
                            cell.clear()
                            cell.text = '[Dynamic last-access value omitted]'
        for node in root.xpath('.//script|.//style|.//template|.//noscript'):
            node.drop_tree()
        for node in root.xpath('.//input|.//textarea|.//select|.//button'):
            if node.get('type', '').casefold() in {'hidden','password'} or re.search(
                    r'sesskey|token|password|secret|csrf|auth', node.get('name','')+' '+node.get('id',''), re.I):
                node.drop_tree()
        observed = []
        for node in root.xpath('.//*[@href or @src or @data]'):
            value = node.get('href') or node.get('src') or node.get('data')
            url = urljoin(item['url'], value)
            parsed = urlparse(url)
            url = urlunparse(parsed._replace(fragment=''))
            if self.allowed(url, item['course_id'], contexts):
                key = 'moodle-read:' + sha((item['course_id'] + url).encode())[:24]
                name = ' '.join(node.text_content().split())[:200] or unquote(parsed.path.rsplit('/', 1)[-1])
                kind = 'moodle_file' if parsed.path.startswith('/pluginfile.php/') else 'moodle_page'
                self.links[key] = {'id': key, 'url': url, 'name': name, 'kind': kind,
                                   'course_id': item['course_id'], 'parent': item['id']}
                observed.append({'id': key, 'name': name, 'kind': kind})
                node.set('data-source-id', key)
        # Keep useful table/input values, discard event handlers, URLs, tokens,
        # scripts and all hidden metadata. No supplied HTML is executed.
        for node in root.iter():
            for attr in list(node.attrib):
                if attr not in {'colspan', 'rowspan', 'value', 'selected', 'checked', 'data-source-id'}:
                    del node.attrib[attr]
        safe = html.tostring(root, encoding='utf-8')
        return safe, observed

    def read(self, key, *, record=True):
        item = self.links.get(key) or self.initial(key)
        if item['course_id'] not in self.course_ids:
            raise ValueError('Source belongs to another course scope.')
        data = self.fetch(item)
        observed = []
        if item['kind'] == 'moodle_page':
            data, observed = self.page_data(item, data)
        name = item['name'] + '.html' if item['kind'] == 'moodle_page' else unquote(urlparse(item['url']).path.rsplit('/', 1)[-1])
        result = {'id': key, 'name': name, 'kind': item['kind'], 'data': data,
                  'course_id': item['course_id'], 'links': observed}
        if record:
            if key in self.receipts and sha(data) != self.receipts[key]['sha256']:
                raise ValueError('Moodle exploration evidence changed during analysis; begin fresh planning.')
            self.receipts[key] = {'sha256': sha(data), 'item': dict(item)}
        return result

    def validate(self):
        for key, receipt in list(self.receipts.items()):
            # Re-read observed sources before accepting a plan or any save.
            result = self.read(key, record=False)
            if sha(result['data']) != receipt['sha256']:
                raise ValueError('Moodle exploration evidence changed; inspect again and obtain a fresh Plan.')
