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
        EdiDocument = cls.env["edi.document"]

        # Create document
        cls.doc = EdiDocument.create(
            {
                "name": "ToDo list",
                "doc_type_id": cls.doc_type_unknown.id,
                "state": "draft",
            }
        )

    def test_action_close_issues(self):
        """Test action close issues"""
        with patch.object(edi_issues._logger, "error", autospec=True):
            issue = self.doc.raise_issue("Test issue: %s", UserError("Test close issues"))
        self.assertIn(issue, self.doc.issue_ids)
        self.doc.action_close_issues()
        self.assertEqual(len(self.doc.issue_ids), 0)

    def test_closing_issues_does_not_compute_their_working_time(self):
        """Test closing issues does not compute the working time of each issue"""
        Calendar = self.env["resource.calendar"]
        ProjectTask = self.env["project.task"]

        with patch.object(edi_issues._logger, "error", autospec=True):
            issue = self.doc.raise_issue("Test issue: %s", UserError("Test working time"))
        self.assertTrue(issue.use_edi_fields)
        # Backdate the issue, so that its working time would take a long time to compute
        self.env.cr.execute(
            "UPDATE project_task SET create_date = now() - interval '30 days' WHERE id = %s",
            (issue.id,),
        )
        ProjectTask.invalidate_cache()

        with patch.object(type(Calendar), "get_work_duration_data", autospec=True) as duration:
            self.doc.action_close_issues()
            ProjectTask.flush()

        duration.assert_not_called()
        self.assertEqual(issue.working_hours_close, 0.0)

    def test_closing_issues_writes_them_in_batches(self):
        """Test closing issues closes all of them with one write per batch"""
        ProjectTask = self.env["project.task"]
        context = self.doc.action_view_issues()["context"]
        for number in range(5):
            ProjectTask.with_context(context).create({"name": "Batch issue %d" % number})
        self.assertEqual(len(self.doc.issue_ids), 5)
        write = type(ProjectTask).write

        with patch.object(edi_issues, "CLOSE_ISSUES_BATCH_SIZE", 2), patch.object(
            type(ProjectTask), "write", autospec=True, side_effect=write
        ) as spy:
            self.doc.action_close_issues()

        self.assertEqual(len(self.doc.issue_ids), 0)
        stage_writes = [call for call in spy.call_args_list if "stage_id" in call[0][1]]
        self.assertEqual([len(call[0][0]) for call in stage_writes], [2, 2, 1])

    def test_action_view_issues(self):
        """Test action view issues"""
        ProjectTask = self.env["project.task"]
        action = self.doc.action_view_issues()
        self.assertEqual(len(ProjectTask.search(action["domain"])), 0)
        issue = ProjectTask.with_context(action["context"]).create(
            {
                "name": "Test context issue",
            }
        )
        self.assertIn(issue, self.doc.issue_ids)
        self.assertEqual(len(ProjectTask.search(action["domain"])), 1)

    def test_raise_issue_non_usererror(self):
        """Test issue raised non UserError"""
        with patch.object(edi_issues._logger, "error", autospec=True):
            issue = self.doc.raise_issue("Test issue: %s", ValidationError("Test non UserError"))
        self.assertIn(issue, self.doc.issue_ids)
