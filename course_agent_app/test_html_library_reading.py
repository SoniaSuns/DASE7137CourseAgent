import tempfile
import unittest
from pathlib import Path
from chrome_file_library import FileLibrary,PAGE_SIZE


class HtmlLibraryReadingTests(unittest.TestCase):
    def test_participant_tables_keep_columns_unicode_and_ignore_scripts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'sources';source.mkdir()
            file=source/'participants.html'
            file.write_text('<html><head><meta charset="utf-8"><title>courseid_140147_participants</title><script>secretScript</script></head><body><table><tr><th>Name</th><th>Email</th><th>Groups</th></tr><tr><td>张三</td><td>one@example.test</td><td>Group 1 &amp; lab</td></tr><tr><td>Missing</td><td></td><td>Group 2</td></tr></table></body></html>',encoding='utf-8')
            original=file.read_bytes();library=FileLibrary(source,root/'index.json');snapshot=library.snapshot()
            self.assertTrue(snapshot['files'][0]['text_reader'])
            result=library.inspect(snapshot,snapshot['files'][0]['id'])
            self.assertEqual(result['status'],'read');self.assertIn('张三 | one@example.test | Group 1 & lab',result['text'])
            self.assertIn('Missing |  | Group 2',result['text']);self.assertNotIn('secretScript',result['text'])
            self.assertEqual(file.read_bytes(),original)

    def test_html_pagination_does_not_drop_late_participant_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'sources';source.mkdir()
            (source/'people.htm').write_text('<table>'+''.join('<tr><td>'+str(i)+' '+('x'*100)+'</td></tr>' for i in range(250))+'</table>')
            library=FileLibrary(source,root/'index.json');snapshot=library.snapshot();fid=snapshot['files'][0]['id']
            first=library.inspect(snapshot,fid);self.assertEqual(first['next_offset'],PAGE_SIZE)
            second=library.inspect(snapshot,fid,first['next_offset']);self.assertIn('249 ',second['text'])


if __name__=='__main__':unittest.main()
