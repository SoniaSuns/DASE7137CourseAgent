import copy
import hashlib
import unittest
from playwright.sync_api import sync_playwright
from chrome_draft_content import inspect_draft_content,draft_urls
from chrome_moodle import form_fingerprint,changed_form_fields


def field(item=11,content=b'image',filename='image.jpg'):
    url=f'https://moodle.hku.hk/draftfile.php/7/user/draft/{item}/{filename}'
    return {'key':'id_introeditor','name':'introeditor[text]','label':'Description','type':'textarea',
            'disabled':False,'rich':True,'value':f'<p>Keep text<img src="{url}" alt="diagram"></p>',
            'draft_content':{url:{'sha256':hashlib.sha256(content).hexdigest(),'size':len(content)}}}


def snapshot(f):return {'fields':[f],'submit':[]}


class DraftComparisonTests(unittest.TestCase):
    def test_rotating_draft_id_with_identical_bytes_is_equal(self):
        old=snapshot(field());new=snapshot(field(22));before=copy.deepcopy(old)
        self.assertEqual(form_fingerprint(old),form_fingerprint(new));self.assertEqual(old,before)
        self.assertEqual(changed_form_fields(old,new),[])

    def test_content_filename_text_context_and_host_changes_are_not_ignored(self):
        old=snapshot(field())
        variants=[field(22,b'different'),field(22,filename='other.jpg')]
        for original,replacement in [('Keep text','New text'),('alt="diagram"','alt="other"'),('/draftfile.php/7/','/draftfile.php/8/'),('moodle.hku.hk','other.example')]:
            f=field(22);f['value']=f['value'].replace(original,replacement);variants.append(f)
        for f in variants:
            with self.subTest(value=f['value']):
                self.assertNotEqual(form_fingerprint(old),form_fingerprint(snapshot(f)))
                self.assertEqual(changed_form_fields(old,snapshot(f)),['Description (id_introeditor)'])

    def test_missing_hash_never_ignores_changed_draft_address(self):
        old=field();new=field(22);old.pop('draft_content');new.pop('draft_content')
        self.assertNotEqual(form_fingerprint(snapshot(old)),form_fingerprint(snapshot(new)))

    def test_only_same_origin_draft_attribute_urls_are_collected(self):
        value=field()['value']+' plain https://moodle.hku.hk/draftfile.php/7/user/draft/8/plain.jpg'
        value+='<img src="https://other.example/draftfile.php/7/user/draft/8/external.jpg">'
        self.assertEqual(len(draft_urls(value)),1)


class DraftReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=sync_playwright().start();cls.browser=cls.p.chromium.launch(headless=True)
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.p.stop()
    def setUp(self):
        self.context=self.browser.new_context();self.addCleanup(self.context.close)
        self.page=self.context.new_page();self.bytes=b'image bytes';self.fail=False;self.requests=[]
        def route(r):
            self.requests.append((r.request.method,r.request.url))
            if '/draftfile.php/' in r.request.url:r.fulfill(status=403 if self.fail else 200,content_type='image/jpeg',body=self.bytes)
            else:r.fulfill(content_type='text/html',body='<body></body>')
        self.page.route('**/*',route);self.page.goto('https://moodle.hku.hk/course/modedit.php?update=1')

    def inspect(self,item):
        f=field(item);f.pop('draft_content');inspect_draft_content(self.page,[f]);return snapshot(f)

    def test_real_browser_fetch_confirms_rotation_but_detects_replacement(self):
        old=self.inspect(11);same=self.inspect(22)
        self.assertEqual(form_fingerprint(old),form_fingerprint(same))
        self.bytes=b'replaced image bytes';changed=self.inspect(33)
        self.assertNotEqual(form_fingerprint(old),form_fingerprint(changed))
        self.assertTrue(all(method=='GET' for method,_ in self.requests))

    def test_failed_content_read_does_not_return_trusted_fingerprint(self):
        from playwright.sync_api import Error
        self.fail=True
        with self.assertRaises(Error):self.inspect(11)


if __name__=='__main__':unittest.main()
