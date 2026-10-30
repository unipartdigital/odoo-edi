"""EDI issue tracking"""

import traceback
import logging
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html_escape

_logger = logging.getLogger(__name__)

EDI_FIELD_MAP = [(field, ("edi_%s" % field)) for field in ("doc_id", "gateway_id", "transfer_id")]


def compute_issue_counts(records, field_map):
    """Set issue_count and rel_issue_count on issue-tracked records

    Open issues are counted in the database rather than by loading them, as a record can have a
    very large number of open issues. Issues also linked to a related record that the model does
    not have a field for (e.g. a transfer's issue, seen from its gateway) count as related.
    """
    ProjectTask = records.env["project.task"]
    issue_field = records._fields["issue_ids"]
    inverse = issue_field.inverse_name
    related = [
        issue_field_name
        for field, issue_field_name in field_map
        if issue_field_name != inverse and field not in records._fields
    ]
    domain = [(inverse, "in", records.ids)] + issue_field.domain

    def count_by_record(extra_domain):
        groups = ProjectTask.read_group(domain + extra_domain, [inverse], [inverse])
        return {group[inverse][0]: group["%s_count" % inverse] for group in groups}

    open_counts = count_by_record([])
    # Without related fields every open issue is an own issue
    own_counts = open_counts
    if related:
        own_counts = count_by_record([(field, "=", False) for field in related])
    for issues in records:
        issues.issue_count = own_counts.get(issues.id, 0)
        issues.rel_issue_count = open_counts.get(issues.id, 0) - issues.issue_count


class Project(models.Model):
    """Extend ``project.project`` to include EDI information"""

    _inherit = "project.project"

    use_edi_fields = fields.Boolean(string="Use EDI Fields", default=False)


class ProjectTask(models.Model):
    """Extend ``project.task`` to include EDI information"""

    _inherit = "project.task"

    use_edi_fields = fields.Boolean(related="project_id.use_edi_fields", readonly=True)
    edi_doc_id = fields.Many2one(
        "edi.document", string="EDI Document", index=True, ondelete="cascade"
    )
    edi_gateway_id = fields.Many2one(
        "edi.gateway", string="EDI Gateway", index=True, ondelete="cascade"
    )
    edi_transfer_id = fields.Many2one(
        "edi.transfer", string="EDI Transfer", index=True, ondelete="cascade"
    )


class BaseIssue(models.AbstractModel):
    """Base Issue-Tracked Object"""

    # To consider: Would be good to move this abstract class in a module that only depends
    # on project module in the future. So if we want to replicate this solution not to depend on
    # EDI module by force.
    _name = "base.issues"
    _inherit = "mail.thread"
    _description = "Base Issue Object"

    # Empty list variable, will be overriden in the specific models where used.
    ISSUES_FIELD_MAP = []

    issue_ids = fields.One2many(
        "project.task",
        string="Issues",
        domain=["|", ("stage_id.fold", "=", False), ("stage_id", "=", False)],
    )
    issue_count = fields.Integer(string="Issue Count", compute="_compute_issue_counts", store=True)
    rel_issue_count = fields.Integer(
        string="Related Issue Count", compute="_compute_issue_counts", store=True
    )

    @api.model
    def display_issue_counts_depends(self):
        """
        Base fields which _compute_issue_counts depends on, it will be extended with super call
        on specific models where used.
        """
        return ["issue_ids", "issue_ids.stage_id"]

    @api.depends(lambda self: self.display_issue_counts_depends())
    def _compute_issue_counts(self):
        """Compute number of open issues (for UI display)"""
        compute_issue_counts(self, self.ISSUES_FIELD_MAP)

    def _issue_vals(self):
        """Construct values for corresponding issues"""
        self.ensure_one()
        vals = {'project_id': self.project_id.id}
        vals[self._fields['issue_ids'].inverse_name] = self.id
        for field, base_field in self.ISSUES_FIELD_MAP:
            if hasattr(self, field):
                rec = getattr(self, field)
                if rec:
                    vals[base_field] = rec.id
        return vals

    def raise_issue(self, fmt, err):
        """Raise issue via issue tracker

        Raise an issue in the associated issue tracker.
        """
        self.ensure_one()
        ProjectTask = self.env["project.task"]

        # Parse exception
        title = err.args[0] if isinstance(err, UserError) else str(err)
        vals = self._issue_vals()

        if hasattr(err, "collected_exceptions"):
            description = "<pre>%s</pre>" % (html_escape(err.collected_exceptions),)
        else:
            tbe = traceback.TracebackException.from_exception(err)
            trace = "".join(tbe.format())
            description = "<pre>%s</pre>" % (html_escape(trace),)

        vals["name"] = f"{fmt} " % self.name
        vals["description"] = description
        base_field = self._fields["issue_ids"].inverse_name

        issue_exists = ProjectTask.search_count(
            [
                ("project_id", "=", self.project_id.id),
                (base_field, "=", self.id),
                ("description", "=", description),
                ("is_closed", "=", False),
            ]
        )
        if issue_exists:
            _logger.warning(
                "Issue with description %s for %s already exists, Not creating a new issue",
                err,
                self.name,
                extra={"obj": self},
            )
            return True
        # Construct issue
        issue = ProjectTask.create(vals)

        # Construct list of threads
        threads = [self]
        for field, _issue_field in self.ISSUES_FIELD_MAP:
            if field in self._fields:
                thread = getattr(self, field)
                if thread:
                    threads += thread

        # Add traceback if applicable

        _logger.error(description)
        if not isinstance(err, UserError):
            issue.message_post(body=description, content_subtype="plaintext")
            for thread in threads:
                thread.sudo().message_post(body=description, content_subtype="plaintext")

        # Add summary
        for thread in threads:
            thread.sudo().message_post(body=(fmt % title), content_subtype="plaintext")
        return issue

    def close_issues(self):
        """Close all open issues"""
        for issue in self.mapped('issue_ids'):
            closed = issue.stage_find(issue.project_id.id,
                                      [('fold', '=', True)])
            issue.stage_id = closed

    def action_view_issues(self):
        """View open issues"""
        self.ensure_one()
        action = self.env.ref('project.action_view_task').read()[0]
        action['domain'] = [(self._fields['issue_ids'].inverse_name,
                             '=', self.id)]
        action['context'] = {'default_%s' % k: v
                             for k, v in self._issue_vals().items()}
        action['context'].update({'create': True})
        return action

    def action_close_issues(self):
        """Close all open issues"""
        self.close_issues()
        return True


class EdiIssue(models.AbstractModel):
    """EDI Issue-Tracked Object

    EDI errors are raised in an issue tracker (and logged as messages
    on the originating object).
    """

    _name = "edi.issues"
    _description = "EDI Issue-Tracked Object"
    _inherit = ["mail.thread"]

    def _default_project_id(self):
        return self.env.ref("edi.project_default")

    project_id = fields.Many2one(
        "project.project", string="Issue Tracker", required=True, default=_default_project_id
    )
    issue_ids = fields.One2many(
        "project.task",
        string="Issues",
        domain=["|", ("stage_id.fold", "=", False), ("stage_id", "=", False)],
    )
    issue_count = fields.Integer(string="Issue Count", compute="_compute_issue_counts", store=True)
    rel_issue_count = fields.Integer(
        string="Related Issue Count", compute="_compute_issue_counts", store=True
    )

    @api.depends(
        "issue_ids",
        "issue_ids.stage_id",
        "issue_ids.edi_doc_id",
        "issue_ids.edi_gateway_id",
        "issue_ids.edi_transfer_id",
    )
    def _compute_issue_counts(self):
        """Compute number of open issues (for UI display)"""
        compute_issue_counts(self, EDI_FIELD_MAP)

    def _issue_vals(self):
        """Construct values for corresponding issues"""
        self.ensure_one()
        vals = {"project_id": self.project_id.id}
        vals[self._fields["issue_ids"].inverse_name] = self.id
        for field, edi_field in EDI_FIELD_MAP:
            if hasattr(self, field):
                rec = getattr(self, field)
                if rec:
                    vals[edi_field] = rec.id
        return vals

    def raise_issue(self, fmt, err):
        """Raise issue via issue tracker

        Raise an issue in the associated issue tracker.
        """
        self.ensure_one()
        ProjectTask = self.env["project.task"]

        # Parse exception
        title = err.args[0] if isinstance(err, UserError) else str(err)
        vals = self._issue_vals()

        if hasattr(err, "collected_exceptions"):
            description = "<pre>%s</pre>" % (html_escape(err.collected_exceptions),)
        else:
            tbe = traceback.TracebackException.from_exception(err)
            trace = "".join(tbe.format())
            description = "<pre>%s</pre>" % (html_escape(trace),)

        vals["name"] = f"{fmt} " % self.name
        vals["description"] = description

        base_field = self._fields["issue_ids"].inverse_name
        issue_exists = ProjectTask.search_count(
            [
                ("project_id", "=", self.project_id.id),
                (base_field, "=", self.id),
                ("description", "=", description),
                ("is_closed", "=", False),
            ]
        )
        if issue_exists:
            _logger.warning(
                "Issue with description %s for %s already exists, Not creating a new issue",
                err,
                self.name,
                extra={"obj": self},
            )
            return True

        # Construct issue
        issue = ProjectTask.create(vals)

        # Construct list of threads
        threads = [self]
        for field, _edi_field in EDI_FIELD_MAP:
            if field in self._fields:
                thread = getattr(self, field)
                if thread:
                    threads += thread

        # Add traceback if applicable
        _logger.error(description)
        if not isinstance(err, UserError):
            issue.message_post(body=description, content_subtype="plaintext")
            for thread in threads:
                thread.sudo().message_post(body=description, content_subtype="plaintext")

        # Add summary
        for thread in threads:
            thread.sudo().message_post(body=(fmt % title), content_subtype="plaintext")
        return issue

    def close_issues(self):
        """Close all open issues"""
        for issue in self.mapped("issue_ids"):
            closed = issue.stage_find(issue.project_id.id, [("fold", "=", True)])
            issue.stage_id = closed

    def action_view_issues(self):
        """View open issues"""
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("project.action_view_task")
        action["domain"] = [(self._fields["issue_ids"].inverse_name, "=", self.id)]
        action["context"] = {"default_%s" % k: v for k, v in self._issue_vals().items()}
        action["context"].update({"create": True})
        return action

    def action_close_issues(self):
        """Close all open issues"""
        self.close_issues()
        return True
