"""Relative display ages must not invalidate otherwise identical roster evidence."""
import unittest
from test_autonomous_exploration import browser_fixture, CID
from chrome_readonly_moodle import ReadOnlyMoodle

PAGE = b'''<html><body class="course-140147 context-700"><main id="region-main">
<table id="participants"><thead><tr><th scope="col" class="header c2">Groups</th>
<th scope="col" class="header c4"><a href="/user/index.php?id=140147&amp;tsort=lastaccess">Last access</a></th>
</tr></thead><tbody><tr><td class="cell c2">Group 1</td><td class="cell c4">58 secs</td></tr></tbody></table>
<p>Submission window: 58 secs</p></main></body></html>'''


class RosterFreshnessTests(unittest.TestCase):
    def setUp(self):
        browser, self.response = browser_fixture()
        self.key = CID + ':participants'
        browser.targets[self.key] = {'id': CID, 'course_id': CID, 'kind': 'participants', 'title': 'Participants'}
        self.broker = ReadOnlyMoodle(browser, [CID])
        self.response.body.return_value = PAGE

    def test_changing_relative_age_does_not_invalidate_roster(self):
        first = self.broker.read(self.key)
        self.assertIn(b'Dynamic last-access value omitted', first['data'])
        self.assertIn(b'Submission window: 58 secs', first['data'])
        self.response.body.return_value = PAGE.replace(b'>58 secs</td>', b'>1 min</td>')
        self.broker.validate()

    def test_membership_change_still_invalidates_roster(self):
        self.broker.read(self.key)
        self.response.body.return_value = PAGE.replace(b'Group 1', b'Group 2')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.broker.validate()

    def test_without_observed_lastaccess_sort_key_no_column_is_removed(self):
        self.response.body.return_value = PAGE.replace(b'tsort=lastaccess', b'tsort=groups')
        first = self.broker.read(self.key)
        self.assertNotIn(b'Dynamic last-access value omitted', first['data'])
        self.response.body.return_value = self.response.body.return_value.replace(b'>58 secs</td>', b'>1 min</td>')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.broker.validate()
