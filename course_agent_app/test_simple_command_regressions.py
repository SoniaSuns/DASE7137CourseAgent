"""Exact-operation regressions, using synthetic locally routed Moodle pages.

Command text documents intent; typed operations are supplied explicitly. These
tests do NOT measure the planner's understanding of natural language.
"""
import copy
import tempfile
import unittest
from pathlib import Path

import test_course_structure as fixtures
from chrome_approval import ChromeApproval


class SimpleCommandRegressions(unittest.TestCase):
    def setUp(self):
        # Compose the immutable existing fixture without inheriting its tests.
        self.fx = fixtures.StructureFixture(methodName='runTest')
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.flow = ChromeApproval(Path(self.temp.name) / 'approval.json', 'fixture')

    def propose(self, command, operations):
        self.flow.begin(command, 'teacher', 'request-1')
        self.flow.offer({}, operations)
        self.flow.mark_sent()

    def approve(self):
        self.flow.approve('OK ' + self.flow.state['code'], 'teacher', 'reply-1')

    def form_op(self, key, fields):
        self.fx.browser.inspect(key)
        return self.fx.browser.resolve({
            'id': 'edit', 'capability': 'update_moodle_form', 'target': key,
            'summary': 'Change the specified setting only', 'scope': 'requested',
            'reason': 'Explicit teacher request', 'depends_on': [],
            'fields': fields, 'params': {},
        })

    def save(self, op, tool=None):
        tool = tool or self.fx.browser
        result = tool.execute(op, lambda: self.flow.before_save(op))
        self.flow.receipt(result)
        self.assertTrue(result['verified'])
        self.assertEqual(self.flow.state['phase'], 'DONE')
        self.assertEqual(self.fx.posts, 1)
        return result

    def test_rename_exact_activity_preserves_other_names_order_and_visibility(self):
        before_order = copy.deepcopy(self.fx.order)
        before_visibility = dict(self.fx.visible)
        op = self.form_op(fixtures.module(2), [{'key': 'id_name', 'value': 'Human Perception'}])
        self.propose('Rename activity 2 to Human Perception.', [op])
        self.approve()
        self.save(op)
        self.assertEqual(self.fx.names, {'1': 'Lecture 4', '2': 'Human Perception', '3': 'Assignment'})
        self.assertEqual(self.fx.order, before_order)
        self.assertEqual(self.fx.visible, before_visibility)

    def test_hide_exact_activity_preserves_title_and_placement(self):
        before_order = copy.deepcopy(self.fx.order)
        before_names = dict(self.fx.names)
        op = self.form_op(fixtures.module(2), [{'key': 'id_visible', 'value': '0'}])
        self.propose('Hide activity 2 from students.', [op])
        self.approve()
        self.save(op)
        self.assertEqual(self.fx.visible, {'1': '0', '2': '0', '3': '1'})
        self.assertEqual(self.fx.names, before_names)
        self.assertEqual(self.fx.order, before_order)

    def test_rename_section_preserves_all_activities(self):
        before = copy.deepcopy((self.fx.order, self.fx.names, self.fx.visible))
        op = self.form_op(fixtures.section(102), [{'key': 'id_name', 'value': 'Week 5: XR'}])
        self.propose('Rename section 102 to Week 5: XR.', [op])
        self.approve()
        self.save(op)
        self.assertEqual(self.fx.section_names, {'101': 'Week 4', '102': 'Week 5: XR'})
        self.assertEqual((self.fx.order, self.fx.names, self.fx.visible), before)

    def test_move_to_exact_anchor_keeps_hidden_state_and_verification_never_reposts(self):
        op = self.fx.op(1, 102, 3)
        self.propose('Move activity 1 before activity 3 in section 102; keep it hidden.', [op])
        self.approve()
        self.save(op, self.fx.struct)
        self.assertEqual(self.fx.order, {'101': ['2'], '102': ['1', '3']})
        self.assertEqual(self.fx.visible['1'], '0')
        self.assertTrue(self.fx.struct.verify(op)['verified'])
        self.assertTrue(self.fx.struct.verify(op)['verified'])
        self.assertEqual(self.fx.posts, 1)

    def test_another_requester_cannot_authorize_a_clear_command(self):
        op = self.fx.op(2, 102)
        self.propose('Move activity 2 to section 102.', [op])
        with self.assertRaises(ValueError):
            self.flow.approve('OK ' + self.flow.state['code'], 'another-teacher', 'reply-1')
        with self.assertRaises(ValueError):
            self.fx.struct.execute(op, lambda: self.flow.before_save(op))
        self.assertEqual(self.fx.posts, 0)
        self.assertEqual(self.fx.order, {'101': ['1', '2'], '102': ['3']})

    def test_restart_invalidates_delivered_confirmation_without_moving(self):
        op = self.fx.op(2, 102)
        self.propose('Move activity 2 to section 102.', [op])
        old_code = self.flow.state['code']
        self.flow = ChromeApproval(self.flow.path, 'fixture')
        self.assertEqual(self.flow.state['phase'], 'INTERRUPTED')
        with self.assertRaises(ValueError):
            self.flow.approve('OK ' + old_code, 'teacher', 'reply-1')
        with self.assertRaises(ValueError):
            self.fx.struct.execute(op, lambda: self.flow.before_save(op))
        self.assertEqual(self.fx.posts, 0)

    def test_title_changed_after_confirmation_prevents_overwriting(self):
        op = self.form_op(fixtures.module(2), [{'key': 'id_name', 'value': 'New title'}])
        self.propose('Rename activity 2 to New title.', [op])
        self.approve()
        self.fx.names['2'] = 'Another teacher changed this'
        with self.assertRaises(ValueError):
            self.fx.browser.execute(op, lambda: self.flow.before_save(op))
        self.assertEqual(self.fx.posts, 0)
        self.assertEqual(self.fx.names['2'], 'Another teacher changed this')


if __name__ == '__main__':
    unittest.main()
