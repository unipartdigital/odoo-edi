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

        with patch.object(type(Leave), 'search', autospec=True) as search:
            self.doc.action_close_issues()

        search.assert_not_called()
        self.assertEqual(issue.working_hours_close, 0.0)
