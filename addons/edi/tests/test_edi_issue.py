"""EDI issue tests"""

from unittest.mock import patch
from odoo.exceptions import UserError, ValidationError
from .common import EdiCase
from ..models import edi_issues


class TestEdiIssue(EdiCase):
    """EDI issue tests"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        EdiDocument = cls.env['edi.document']

        # Create document
        cls.doc = EdiDocument.create({
            'name': "ToDo list",
            'doc_type_id': cls.doc_type_unknown.id,
            'state': 'draft',
        })

    def test01_action_close_issues(self):
        """Test action close issues"""
        with patch.object(edi_issues._logger, 'error', autospec=True):
            issue = self.doc.raise_issue("Test issue: %s",
                                         UserError("Test close issues"))
        self.assertIn(issue, self.doc.issue_ids)
        with patch.object(self.env.cr, 'commit'):
            self.doc.action_close_issues()
        self.assertEqual(len(self.doc.issue_ids), 0)

    def test02_action_view_issues(self):
        """Test action view issues"""
        ProjectTask = self.env['project.task']
        action = self.doc.action_view_issues()
        self.assertEqual(len(ProjectTask.search(action['domain'])), 0)
        issue = ProjectTask.with_context(action['context']).create({
            'name': "Test context issue",
        })
        self.assertIn(issue, self.doc.issue_ids)
        self.assertEqual(len(ProjectTask.search(action['domain'])), 1)

    def test03_raise_issue_non_usererror(self):
        """Test issue raised non UserError"""
        with patch.object(edi_issues._logger, 'error', autospec=True):
            issue = self.doc.raise_issue("Test issue: %s",
                                         ValidationError("Test non UserError"))
        self.assertIn(issue, self.doc.issue_ids)

    def test_closing_issues_does_not_compute_their_working_time(self):
        """Test closing issues does not search the calendar leaves of each working day"""
        Leave = self.env['resource.calendar.leaves']
        with patch.object(edi_issues._logger, 'error', autospec=True):
            issue = self.doc.raise_issue("Test issue: %s",
                                         UserError("Test working time"))
        self.assertTrue(issue.use_edi_fields)
        self.env.cr.execute(
            "UPDATE project_task SET create_date = now() - interval '30 days' WHERE id = %s",
            (issue.id,))
        issue.invalidate_cache()

        with patch.object(type(Leave), 'search', autospec=True) as search, \
                patch.object(self.env.cr, 'commit'):
            self.doc.action_close_issues()

        search.assert_not_called()
        self.assertEqual(issue.working_hours_close, 0.0)

    def test_closing_issues_writes_them_in_batches(self):
        """Test closing issues closes all of them with one write per batch"""
        ProjectTask = self.env['project.task']
        context = self.doc.action_view_issues()['context']
        for number in range(5):
            ProjectTask.with_context(context).create({'name': "Batch issue %d" % number})
        self.assertEqual(len(self.doc.issue_ids), 5)
        write = type(ProjectTask).write

        with patch.object(edi_issues, 'CLOSE_ISSUES_BATCH_SIZE', 2), \
                patch.object(type(ProjectTask), 'write', autospec=True,
                             side_effect=write) as spy, \
                patch.object(self.env.cr, 'commit') as commit:
            self.doc.action_close_issues()

        self.assertEqual(len(self.doc.issue_ids), 0)
        stage_writes = [call for call in spy.call_args_list if 'stage_id' in call[0][1]]
        self.assertEqual([len(call[0][0]) for call in stage_writes], [2, 2, 1])
        self.assertEqual(commit.call_count, 3)

    def test_closing_issues_without_the_action_does_not_commit(self):
        """Test closing issues as part of other processing does not commit them"""
        ProjectTask = self.env['project.task']
        context = self.doc.action_view_issues()['context']
        for number in range(3):
            ProjectTask.with_context(context).create({'name': "Batch issue %d" % number})

        with patch.object(edi_issues, 'CLOSE_ISSUES_BATCH_SIZE', 2), \
                patch.object(self.env.cr, 'commit') as commit:
            self.doc.close_issues()

        self.assertEqual(len(self.doc.issue_ids), 0)
        commit.assert_not_called()
